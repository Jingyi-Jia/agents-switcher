'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { UpdateController, registerUpdaterIPC, CHANNELS } = require('../src/updater.cjs');

const release = { version: '1.2.0', url: 'https://github.com/Jingyi-Jia/agents-switcher/releases/tag/v1.2.0' };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

function fixture(options = {}) {
  const calls = [], states = [];
  const controller = new UpdateController({ currentVersion: '1.0.0', platform: 'win32', arch: 'x64',
    checkRelease: async ({ signal }) => { calls.push(['check', signal]); return release; },
    openRelease: async url => { calls.push(['open', url]); },
    confirmInstall: () => assert.fail('manual mode requested installation consent'),
    prepareInstall: () => assert.fail('manual mode stopped the backend'),
    verifyDownload: () => assert.fail('manual mode verified an executable'),
    validateInstall: () => assert.fail('manual mode checked installation readiness'),
    installFailed: () => assert.fail('manual mode invoked the installation failure callback'), ...options });
  controller.on('state', state => states.push(state));
  return { controller, calls, states };
}

test('manual checks and opening the trusted release page require separate user actions', async () => {
  const { controller, calls } = fixture();
  assert.equal(controller.getState().mode, 'manual');
  assert.equal(controller.getState().supported, true);
  assert.equal(controller.getState().status, 'idle');
  assert.deepEqual(calls, []);
  await controller.viewRelease(); await controller.download(); controller.cancel(); await controller.install();
  assert.deepEqual(calls, []);
  const state = await controller.check();
  assert.equal(state.status, 'available');
  assert.equal(state.currentVersion, '1.0.0');
  assert.equal(state.availableVersion, '1.2.0');
  assert.equal(state.progress, null);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'check');
  assert.equal(calls[0][1].aborted, false);
  assert.equal(state.message.includes('View release'), true);
  await controller.viewRelease();
  assert.deepEqual(calls[1], ['open', release.url]);
  assert.equal(controller.getState().status, 'available');
  assert.match(controller.getState().message, /Opened the release page/);
  assert.equal(controller.getState().currentVersion, '1.0.0');
  assert.equal(JSON.stringify(controller).includes(release.url), false);
  assert.equal(JSON.stringify(controller.getState()).includes('https://'), false);
});

test('current, older and missing public releases never enable opening or installation', async () => {
  for (const result of [null, { version: '1.0.0', url: release.url.replaceAll('1.2.0', '1.0.0') },
    { version: '0.9.0', url: release.url.replaceAll('1.2.0', '0.9.0') }]) {
    const { controller, calls } = fixture({ checkRelease: async () => result });
    assert.equal((await controller.check()).status, 'not-available');
    assert.equal(controller.getState().availableVersion, null);
    await controller.viewRelease(); await controller.download(); controller.cancel(); await controller.install();
    assert.deepEqual(calls, []);
  }
});

test('manual actions cannot reach any executable download, installation or backend callback', async () => {
  const { controller, calls } = fixture();
  const initial = controller.getState();
  for (const method of ['download', 'cancel', 'install']) assert.deepEqual(await controller[method](), initial);
  await controller.check();
  const available = controller.getState();
  for (const method of ['download', 'cancel', 'install']) assert.deepEqual(await controller[method](), available);
  controller.failInstall();
  assert.equal(controller.installStarted, false);
  assert.equal(controller.updater, null);
  assert.equal(controller.token, null);
  assert.equal(controller.downloaded, null);
  assert.equal(calls.length, 1);
});

test('conflicting runtime capabilities fail closed rather than selecting an updater', async () => {
  const updater = new EventEmitter();
  updater.checkForUpdates = () => assert.fail('ambiguous updater checked');
  updater.downloadUpdate = () => assert.fail('ambiguous updater downloaded');
  updater.quitAndInstall = () => assert.fail('ambiguous updater installed');
  const { controller, calls } = fixture({ updater });
  assert.equal(controller.getState().mode, 'unsupported');
  for (const method of ['check', 'viewRelease', 'download', 'cancel', 'install']) {
    assert.equal((await controller[method]()).status, 'unsupported');
  }
  assert.deepEqual(calls, []);
  assert.equal(updater.listenerCount('error'), 0);
});

test('invalid checker results and failures cannot expose raw data or enable opening', async () => {
  for (const result of [undefined, {}, 'PRIVATE RELEASE HTML', { ...release, version: '1.2.0-beta.1' },
    { ...release, version: 'v1.2.0' }, { ...release, version: '<script>PRIVATE</script>' },
    { ...release, url: `${release.url}?token=PRIVATE` }, { ...release, url: 'https://evil.example/release' },
    { ...release, url: release.url.replace('/tag/', '/download/') },
    { ...release, url: release.url.replace('Jingyi-Jia', 'other') }, { ...release, url: release.url.replaceAll('1.2.0', '1.3.0') }]) {
    const { controller, calls } = fixture({ checkRelease: async () => result });
    assert.equal((await controller.check()).status, 'error');
    assert.equal(controller.getState().availableVersion, null);
    assert.equal(JSON.stringify(controller.getState()).includes('PRIVATE'), false);
    await controller.viewRelease();
    assert.deepEqual(calls, []);
  }
  const { controller } = fixture({ checkRelease: async () => { throw new Error('PRIVATE transport failure'); } });
  assert.equal((await controller.check()).status, 'error');
  assert.equal(JSON.stringify(controller.getState()).includes('PRIVATE'), false);
});

test('manual checks serialize and a new check invalidates the previously checked URL', async () => {
  const wait = deferred();
  let checking = 0;
  const { controller, calls } = fixture({ checkRelease: () => ++checking === 1 ? release : wait.promise });
  await controller.check();
  const refresh = controller.check();
  assert.equal(controller.getState().status, 'checking');
  assert.equal(controller.getState().availableVersion, null);
  await controller.check(); await controller.viewRelease(); await controller.download(); controller.cancel(); await controller.install();
  assert.equal(checking, 2);
  assert.deepEqual(calls, []);
  wait.reject(new Error('PRIVATE response'));
  await refresh; await controller.viewRelease();
  assert.deepEqual(calls, []);
  assert.equal(controller.getState().status, 'error');
});

test('browser opening serializes competing actions and failures allow a fresh check', async () => {
  const wait = deferred();
  let opens = 0;
  const { controller, calls } = fixture({ openRelease: () => { opens += 1; return wait.promise; } });
  await controller.check();
  const opening = controller.viewRelease();
  await controller.viewRelease(); await controller.check(); await controller.download(); controller.cancel(); await controller.install();
  assert.equal(opens, 1);
  assert.equal(calls.length, 1);
  wait.reject(new Error('PRIVATE browser error'));
  assert.equal((await opening).status, 'error');
  assert.equal(controller.getState().availableVersion, null);
  assert.equal(JSON.stringify(controller.getState()).includes('PRIVATE'), false);
  await controller.viewRelease();
  assert.equal(opens, 1);
  await controller.check();
  assert.equal(controller.getState().status, 'available');
  assert.equal(calls.length, 2);
});

for (const success of [true, false]) {
  test(`closing aborts a manual check and ignores its late ${success ? 'result' : 'failure'}`, async () => {
    const wait = deferred();
    let signal;
    const { controller, calls, states } = fixture({ checkRelease: options => { signal = options.signal; return wait.promise; } });
    const checking = controller.check(), snapshot = controller.getState();
    controller.close(); controller.close();
    assert.equal(signal.aborted, true);
    if (success) wait.resolve(release); else wait.reject(new Error('PRIVATE late response'));
    await checking; await controller.check(); await controller.viewRelease();
    assert.deepEqual(controller.getState(), snapshot);
    assert.equal(states.length, 1);
    assert.deepEqual(calls, []);
  });

  test(`closing ignores a browser callback's late ${success ? 'result' : 'failure'}`, async () => {
    const wait = deferred();
    const { controller, states } = fixture({ openRelease: () => wait.promise });
    await controller.check();
    const opening = controller.viewRelease(), snapshot = controller.getState(), count = states.length;
    controller.close();
    if (success) wait.resolve(); else wait.reject(new Error('PRIVATE late browser failure'));
    await opening;
    assert.deepEqual(controller.getState(), snapshot);
    assert.equal(states.length, count);
  });
}

test('a controller closed from its checking event never starts HTTP work', async () => {
  const { controller, calls } = fixture();
  controller.on('state', () => controller.close());
  await controller.check();
  assert.deepEqual(calls, []);
});

test('closing with an available manual update cannot open a browser later', async () => {
  const { controller, calls, states } = fixture();
  await controller.check();
  controller.close();
  const count = states.length;
  await controller.viewRelease(); await controller.download(); controller.cancel(); await controller.install();
  assert.equal(calls.length, 1);
  assert.equal(states.length, count);
});

test('manual IPC rejects supplied URLs, other windows and subframes, and cannot invoke install actions', async () => {
  const { controller, calls } = fixture();
  const handlers = new Map();
  const origin = 'http://127.0.0.1:12345';
  const contents = { isDestroyed: () => false, getURL: () => `${origin}/?token=PRIVATE`,
    mainFrame: { url: `${origin}/?token=PRIVATE`, send: () => {} } };
  registerUpdaterIPC({ handle: (channel, handler) => handlers.set(channel, handler) }, controller, () => ({ contents, origin }));
  assert.equal(CHANNELS.viewRelease, 'agent-switch:update:view-release');
  const event = { sender: contents, senderFrame: contents.mainFrame };
  for (const handler of handlers.values()) {
    for (const supplied of [undefined, release.url, 'https://evil.example/update.exe', { url: release.url }]) {
      assert.throws(() => handler(event, supplied), /Update request denied/);
    }
    assert.throws(() => handler({ sender: {}, senderFrame: contents.mainFrame }), /Update request denied/);
    assert.throws(() => handler({ sender: contents, senderFrame: { url: contents.mainFrame.url } }), /Update request denied/);
  }
  await handlers.get(CHANNELS.check)(event);
  for (const method of ['download', 'cancel', 'install']) {
    assert.equal((await handlers.get(CHANNELS[method])(event)).status, 'available');
  }
  assert.equal(calls.length, 1);
  await handlers.get(CHANNELS.viewRelease)(event);
  assert.deepEqual(calls[1], ['open', release.url]);
});
