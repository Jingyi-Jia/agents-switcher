'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { UpdateController, validateUpdate, registerUpdaterIPC, CHANNELS, STATE_CHANNEL } = require('../src/updater.cjs');

const info = { version: '1.2.0', tag: 'v1.2.0', files: [{ url: 'Agent-Switch-1.2.0-win-x64.exe', size: 100, sha512: Buffer.alloc(64).toString('base64') }] };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

function fixture(options = {}) {
  const calls = [];
  const updater = new EventEmitter();
  updater.checkForUpdates = async () => { calls.push('check'); return { isUpdateAvailable: true, updateInfo: info }; };
  updater.downloadUpdate = async () => { calls.push('download'); updater.emit('update-downloaded', { ...info, downloadedFile: 'PRIVATE PATH' }); };
  updater.quitAndInstall = (...args) => calls.push(['install', ...args]);
  const controller = new UpdateController({ updater, currentVersion: '1.0.0', platform: 'win32', arch: 'x64',
    confirmInstall: async () => { calls.push('confirm'); return true; },
    prepareInstall: async () => calls.push('stop'), installFailed: () => calls.push('failed'), ...options });
  return { controller, updater, calls };
}

test('checks, downloads and installation are separate explicit actions', async () => {
  const { controller, updater, calls } = fixture();
  assert.equal(controller.getState().status, 'idle');
  assert.deepEqual(calls, []);
  for (const flag of ['autoDownload', 'autoInstallOnAppQuit', 'allowPrerelease', 'allowDowngrade', 'forceDevUpdateConfig']) assert.equal(updater[flag], false);
  assert.equal(updater.disableWebInstaller, true);
  assert.equal(updater.logger, null);
  await controller.download(); await controller.install(); assert.deepEqual(calls, []);
  assert.equal((await controller.check()).status, 'available'); assert.deepEqual(calls, ['check']);
  assert.equal((await controller.download()).status, 'downloaded'); assert.deepEqual(calls, ['check', 'download']);
  assert.equal((await controller.check()).status, 'downloaded');
  assert.equal((await controller.install()).status, 'installing');
  assert.deepEqual(calls, ['check', 'download', 'confirm', 'stop', ['install', false, true]]);
  assert.equal(controller.getState().currentVersion, '1.0.0');
  assert.equal(controller.getState().availableVersion, '1.2.0');
  await controller.install(); assert.equal(calls.length, 5);
  assert.equal(JSON.stringify(controller.getState()).includes('PRIVATE'), false);
});

test('unsupported builds never call the library', async () => {
  const controller = new UpdateController({ currentVersion: '1.0.0', reason: 'Use an installed release.' });
  for (const action of ['getState', 'check', 'download', 'cancel', 'install']) {
    const state = await controller[action]();
    assert.equal(state.supported, false); assert.equal(state.status, 'unsupported');
  }
});

test('no compatible release and a disabled library are reported honestly', async () => {
  const { controller, updater } = fixture();
  updater.checkForUpdates = async () => ({ isUpdateAvailable: false, updateInfo: { version: '1.0.0' } });
  assert.equal((await controller.check()).status, 'not-available');
  updater.checkForUpdates = async () => null;
  assert.equal((await controller.check()).status, 'error');
});

test('rejects prereleases, downgrades, wrong architectures and unsafe metadata', async () => {
  const { controller, updater } = fixture();
  for (const data of [
    null, { ...info, version: '0.9.0' }, { ...info, version: '1.0.0' }, { ...info, version: '1.2.0-beta.1' },
    { ...info, version: '<script>SECRET</script>' }, { ...info, tag: '../../Other/repo' },
    { ...info, files: [{ ...info.files[0], url: 'https://evil.example/update.exe' }] },
    { ...info, files: [{ ...info.files[0], url: 'Agent-Switch-1.2.0-win-arm64.exe' }] },
    { ...info, files: [{ ...info.files[0], sha512: '' }] },
    { ...info, files: [{ ...info.files[0], size: -1 }] },
  ]) {
    assert.equal(validateUpdate(data, '1.0.0', 'win32', 'x64'), false);
    updater.checkForUpdates = async () => ({ isUpdateAvailable: true, updateInfo: data });
    assert.equal((await controller.check()).status, 'error');
    assert.equal(controller.getState().availableVersion, null);
  }
});

test('serializes competing actions and never forwards raw errors or event payloads', async () => {
  const { controller, updater, calls } = fixture();
  const wait = deferred();
  updater.checkForUpdates = () => wait.promise;
  const checking = controller.check();
  await controller.check(); await controller.download(); await controller.install();
  assert.deepEqual(calls, []);
  updater.emit('error', new Error('SECRET response body'));
  wait.reject(new Error('SECRET token URL'));
  assert.equal((await checking).status, 'error');
  assert.equal(JSON.stringify(controller.getState()).includes('SECRET'), false);
});

test('progress is bounded numeric data and snapshots cannot alter internal state', async () => {
  const { controller, updater } = fixture();
  await controller.check();
  const wait = deferred(); updater.downloadUpdate = () => wait.promise;
  const download = controller.download();
  updater.emit('download-progress', { percent: 40, transferred: 40, total: 100, bytesPerSecond: 2, url: 'SECRET' });
  const snapshot = controller.getState();
  assert.deepEqual(snapshot.progress, { percent: 40, transferred: 40, total: 100, bytesPerSecond: 2 });
  snapshot.progress.percent = 9; snapshot.status = 'downloaded';
  assert.equal(controller.getState().progress.percent, 40);
  updater.emit('download-progress', { percent: Infinity, transferred: 5, total: 100, bytesPerSecond: 2 });
  assert.equal(controller.getState().progress.percent, 40);
  updater.emit('update-downloaded', info); wait.resolve();
  assert.equal((await download).status, 'downloaded');
});

for (const completes of [true, false]) {
  test(`download cancellation wins a completion race (${completes}) and permits retry`, async () => {
    const { controller, updater } = fixture();
    await controller.check();
    const wait = deferred();
    let token;
    updater.downloadUpdate = supplied => { token = supplied; return wait.promise; };
    const download = controller.download();
    assert.equal(controller.cancel().status, 'cancelling');
    assert.equal(token.cancelled, true);
    updater.emit('update-downloaded', info);
    if (completes) wait.resolve(); else wait.reject(new Error('SECRET cancelled'));
    await download;
    assert.equal(controller.getState().status, 'available');
    await controller.install();
    updater.downloadUpdate = async () => updater.emit('update-downloaded', info);
    assert.equal((await controller.download()).status, 'downloaded');
  });
}

test('signature verification failures and incomplete downloads never enable install', async () => {
  const { controller, updater, calls } = fixture({ verifyDownload: async () => { throw new Error('SECRET invalid signature'); } });
  await controller.check();
  assert.equal((await controller.download()).status, 'error');
  await controller.install(); assert.deepEqual(calls, ['check', 'download']);
  assert.equal(JSON.stringify(controller.getState()).includes('SECRET'), false);
  await controller.check(); updater.downloadUpdate = async () => [];
  assert.equal((await controller.download()).status, 'error');
});

test('signature and installation readiness are rechecked before stopping accounts', async () => {
  let allowed = true;
  const { controller, calls } = fixture({ validateInstall: async () => { if (!allowed) throw new Error(); } });
  await controller.check(); await controller.download(); allowed = false;
  assert.equal((await controller.install()).status, 'error');
  assert.deepEqual(calls, ['check', 'download', 'confirm']);
});

test('cancel native confirmation preserves the downloaded update and running service', async () => {
  const { controller, calls } = fixture({ confirmInstall: async () => false });
  await controller.check(); await controller.download();
  assert.equal((await controller.install()).status, 'downloaded');
  assert.deepEqual(calls, ['check', 'download']);
});

test('closing the app cancels downloads and ignores late callbacks', async () => {
  const { controller, updater, calls } = fixture();
  await controller.check();
  const wait = deferred(); let token;
  updater.downloadUpdate = supplied => { token = supplied; return wait.promise; };
  const download = controller.download(); controller.close();
  assert.equal(token.cancelled, true);
  updater.emit('update-downloaded', info); wait.resolve(); await download;
  await controller.install(); await controller.check();
  assert.deepEqual(calls, ['check']);
});

test('closing while a confirmation is open prevents a later install', async () => {
  const wait = deferred();
  const { controller, calls } = fixture({ confirmInstall: () => wait.promise });
  await controller.check(); await controller.download();
  const installing = controller.install(); controller.close(); wait.resolve(true); await installing;
  assert.deepEqual(calls, ['check', 'download']);
});

test('updater IPC accepts only zero-argument methods from the owned main frame', async () => {
  const { controller } = fixture();
  const handlers = new Map(), sent = [];
  const origin = 'http://127.0.0.1:12345';
  const contents = { isDestroyed: () => false, getURL: () => `${origin}/?token=PRIVATE`,
    mainFrame: { url: `${origin}/?token=PRIVATE`, send: (...args) => sent.push(args) } };
  let context = { contents, origin };
  registerUpdaterIPC({ handle: (channel, handler) => handlers.set(channel, handler) }, controller, () => context);
  assert.deepEqual([...handlers.keys()], Object.values(CHANNELS));
  const event = { sender: contents, senderFrame: contents.mainFrame };
  assert.equal(handlers.get(CHANNELS.getState)(event).status, 'idle');
  for (const handler of handlers.values()) {
    for (const bad of [{}, { sender: {}, senderFrame: contents.mainFrame }, { sender: contents, senderFrame: { url: contents.mainFrame.url } }, { sender: contents, senderFrame: null }]) {
      assert.throws(() => handler(bad), /^Error: Update request denied\.$/);
    }
    assert.throws(() => handler(event, { url: 'SECRET', path: 'PRIVATE' }), /denied/);
  }
  await handlers.get(CHANNELS.check)(event);
  assert.equal(sent.at(-1)[0], STATE_CHANNEL);
  assert.equal(JSON.stringify(sent).includes('PRIVATE'), false);
  for (const url of ['https://evil.example/', `${origin}/api/state`, 'file:///private', 'http://127.0.0.1:9999/']) {
    contents.mainFrame.url = url;
    assert.throws(() => handlers.get(CHANNELS.getState)(event), /denied/);
  }
  const before = sent.length; context = null;
  controller.update({ message: 'No renderer.' });
  assert.equal(sent.length, before);
});
