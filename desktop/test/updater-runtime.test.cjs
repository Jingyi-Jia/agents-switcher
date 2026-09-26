'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const yaml = require('js-yaml');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createUpdateRuntime, verifyWindowsSignature, FEED } = require('../src/updater-runtime.cjs');

function fixture(overrides = {}) {
  const calls = [];
  class Updater extends EventEmitter {
    constructor(options) { super(); calls.push(options); }
  }
  const config = { ...FEED, channel: 'latest', publisherName: ['Example Publisher'], updaterCacheDirName: 'agents-switcher-desktop-updater' };
  const run = async (...args) => {
    calls.push(args);
    return { stdout: JSON.stringify({ path: args[2]?.env?.AGENT_SWITCH_UPDATE_FILE,
      subject: 'CN=Example Publisher, O=Example', name: 'Example Publisher' }) };
  };
  const options = {
    app: { isPackaged: true, getVersion: () => '1.1.0', isInApplicationsFolder: () => true },
    releaseBuild: true, platform: 'win32', arch: 'x64', env: { SystemRoot: 'C:\\Windows', GH_TOKEN: 'PRIVATE' },
    execPath: 'C:\\Program Files\\Agent Switch\\Agent Switch.exe', resourcesPath: '/resources',
    io: { readFileSync: () => yaml.dump(config), statSync: () => ({ isFile: () => true }),
      lstatSync: () => ({ isFile: () => true, isSymbolicLink: () => false }), realpathSync: value => value, accessSync: () => {} },
    run, library: () => ({ NsisUpdater: Updater, MacUpdater: Updater, AppImageUpdater: Updater }), ...overrides,
  };
  return { options, calls, config };
}

test('development, preview, prerelease, and unsupported architectures never load an updater', async () => {
  for (const change of [
    { releaseBuild: false }, { releaseBuild: 'true' }, { platform: 'freebsd' }, { arch: 'arm64' },
    { app: { isPackaged: false, getVersion: () => '1.1.0' } },
    { app: { isPackaged: true, getVersion: () => '1.2.0-beta.1' } },
  ]) {
    const { options, calls } = fixture(change);
    const result = await createUpdateRuntime(options);
    assert.equal(result.updater, undefined);
    assert.equal(typeof result.reason, 'string');
    assert.deepEqual(calls, []);
  }
});

test('Windows uses only the public fixed stable feed with no ambient GitHub auth', async () => {
  const { options, calls } = fixture();
  const runtime = await createUpdateRuntime(options);
  assert.ok(runtime.updater);
  assert.deepEqual(calls.at(-1), { ...FEED, channel: 'latest', timeout: 30000 });
  assert.equal(JSON.stringify(calls.at(-1)).includes('PRIVATE'), false);
  await runtime.verifyDownload({ downloadedFile: 'C:\\cache\\update.exe' });
  assert.equal(calls.at(-1)[2].env.AGENT_SWITCH_UPDATE_FILE, 'C:\\cache\\update.exe');
  await assert.rejects(runtime.verifyDownload({ downloadedFile: 'relative.exe' }));
});

test('invalid, redirected or credential-bearing packaged configuration fails closed', async () => {
  for (const mutation of [
    value => { value.owner = 'other'; }, value => { value.repo = 'other'; }, value => { value.provider = 'generic'; },
    value => { value.private = true; }, value => { value.channel = 'beta'; }, value => { value.token = 'PRIVATE'; },
    value => { value.requestHeaders = { Authorization: 'PRIVATE' }; }, value => { value.host = 'evil.example'; },
    value => { value.protocol = 'http'; },
  ]) {
    const { options, config, calls } = fixture(); mutation(config);
    assert.equal((await createUpdateRuntime(options)).updater, undefined);
    assert.deepEqual(calls, []);
  }
  for (const content of ['', 'null', '- 1', 'a: [', 'a'.repeat(16385)]) {
    const { options } = fixture(); options.io.readFileSync = () => content;
    assert.equal((await createUpdateRuntime(options)).updater, undefined);
  }
});

test('missing Windows publisher, uninstalled copy, or signature verification failure disables updates', async () => {
  for (const name of [undefined, [], [''], 123, ['Example', null]]) {
    const { options, config } = fixture(); config.publisherName = name;
    assert.equal((await createUpdateRuntime(options)).updater, undefined);
  }
  const missing = fixture(); missing.options.io.statSync = () => { throw new Error('PRIVATE path'); };
  assert.equal((await createUpdateRuntime(missing.options)).updater, undefined);
  const broken = fixture(); broken.options.run = async () => { throw new Error('PRIVATE signature'); };
  const result = await createUpdateRuntime(broken.options);
  assert.equal(result.updater, undefined);
  assert.equal(JSON.stringify(result).includes('PRIVATE'), false);
});

test('Windows Authenticode errors, malformed output and publisher mismatches are never bypassed', async () => {
  const file = 'C:\\cache\\update.exe', names = ['Example Publisher'];
  for (const stdout of ['', 'null', '{}', 'PRIVATE', JSON.stringify({ path: file, subject: 'CN=Other', name: 'Other' }),
    JSON.stringify({ path: 'C:\\other.exe', name: names[0] })]) {
    await assert.rejects(verifyWindowsSignature(file, names, { run: async () => ({ stdout }), env: {} }));
  }
  await assert.rejects(verifyWindowsSignature(file, names, { run: async () => { throw new Error('PowerShell unavailable'); }, env: {} }));
  await verifyWindowsSignature(file, names, { run: async (exe, args, options) => {
    assert.equal(exe, 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe');
    assert.equal(options.shell, false);
    assert.equal(options.timeout, 25000);
    assert.match(args.at(-1), /\$s.Status -ne 'Valid'/);
    assert.match(args.at(-1), /-LiteralPath \$env:AGENT_SWITCH_UPDATE_FILE/);
    assert.match(args.at(-1), /\[Console\]::OutputEncoding=\[System.Text.UTF8Encoding\]::new\(\$false\)/);
    assert.equal(args.join(' ').includes(file), false);
    return { stdout: JSON.stringify({ path: file, name: names[0] }) };
  }, env: {} });
});

for (const arch of ['arm64', 'x64']) {
  test(`macOS ${arch} requires Applications and a real Developer ID signature`, async () => {
    const { options, config, calls } = fixture({ platform: 'darwin', arch, execPath: '/Applications/Agent Switch.app/Contents/MacOS/Agent Switch' });
    config.channel = `latest-${arch}`;
    const runtime = await createUpdateRuntime(options);
    assert.ok(runtime.updater);
    assert.equal(calls[0][0], '/usr/bin/codesign');
    assert.ok(calls[0][1].includes('anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists'));
    assert.equal(calls[0][1].at(-1), '/Applications/Agent Switch.app');
    assert.equal(calls.at(-1).channel, `latest-${arch}`);
    options.app.isInApplicationsFolder = () => false;
    assert.equal((await createUpdateRuntime(options)).updater, undefined);
    options.app.isInApplicationsFolder = () => true;
    options.run = async () => { throw new Error('ad hoc signature'); };
    assert.equal((await createUpdateRuntime(options)).updater, undefined);
  });
}

test('Linux supports only a writable directly mounted AppImage, never a deb or tar directory', async () => {
  const { options } = fixture({ platform: 'linux', env: { APPIMAGE: '/apps/Agent-Switch.AppImage', APPDIR: '/mount/app' }, execPath: '/mount/app/agent-switch-desktop' });
  assert.ok((await createUpdateRuntime(options)).updater);
  for (const env of [{}, { APPIMAGE: 'relative.AppImage', APPDIR: '/mount/app' },
    { APPIMAGE: '/apps/Agent-Switch.AppImage' }, { APPIMAGE: '/apps/Agent-Switch.deb', APPDIR: '/mount/app' },
    { ...options.env, APPDIR: '/another/mount' }, { ...options.env, APPIMAGE_EXTRACT_AND_RUN: '1' }]) {
    assert.equal((await createUpdateRuntime({ ...options, env })).updater, undefined);
  }
  options.io.accessSync = () => { throw new Error('read-only'); };
  assert.equal((await createUpdateRuntime(options)).updater, undefined);
  options.io.accessSync = () => {};
  options.io.lstatSync = () => ({ isFile: () => true, isSymbolicLink: () => true });
  assert.equal((await createUpdateRuntime(options)).updater, undefined);
});

test('native Windows verification accepts a signed system file with the isolated module path', { skip: process.platform !== 'win32' }, async () => {
  await verifyWindowsSignature(path.join(process.env.SystemRoot, 'System32', 'cmd.exe'), ['Microsoft Windows', 'Microsoft Corporation']);
});

test('native Windows signature verification preserves Unicode and literal file paths', { skip: process.platform !== 'win32' }, async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-switch-signature-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, "签名 preview ['literal'].exe");
  fs.copyFileSync(path.join(process.env.SystemRoot, 'System32', 'cmd.exe'), file);
  await verifyWindowsSignature(file, ['Microsoft Windows', 'Microsoft Corporation']);
});

test('native Windows verification rejects an unsigned file without executing it', { skip: process.platform !== 'win32' }, async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-switch-signature-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'unsigned.exe');
  fs.writeFileSync(file, 'synthetic unsigned fixture');
  await assert.rejects(verifyWindowsSignature(file, ['Microsoft Windows', 'Microsoft Corporation']));
});
