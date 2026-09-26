'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const yaml = require('js-yaml');
const { NsisUpdater } = require('electron-updater');
const { FEED } = require('../src/updater-runtime.cjs');
const { validateUpdate } = require('../src/updater.cjs');

for (const [platform, arch, channel, metadata] of [
  ['darwin', 'arm64', 'latest-arm64', 'latest-arm64-mac.yml'],
  ['darwin', 'x64', 'latest-x64', 'latest-x64-mac.yml'],
  ['win32', 'x64', 'latest', 'latest.yml'],
  ['linux', 'x64', 'latest', 'latest-linux.yml'],
]) {
  test(`pinned real updater resolves ${metadata} and returns explicit availability`, async t => {
    assert.equal(require('electron-updater/package.json').version, '6.8.9');
    const originalArch = process.env.TEST_UPDATER_ARCH;
    process.env.TEST_UPDATER_ARCH = arch;
    t.after(() => {
      if (originalArch === undefined) delete process.env.TEST_UPDATER_ARCH;
      else process.env.TEST_UPDATER_ARCH = originalArch;
    });
    const userDataPath = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-switch-provider-'));
    t.after(() => fs.rmSync(userDataPath, { recursive: true, force: true }));
    const updater = new NsisUpdater(null, { version: '1.0.0', isPackaged: true, userDataPath, whenReady: async () => {} });
    updater.logger = null;
    updater.autoDownload = false;
    updater.allowPrerelease = false;
    updater.allowDowngrade = false;
    updater._testOnlyOptions = { platform };
    const requests = [], base = '/Jingyi-Jia/agents-switcher/releases';
    const filename = `Agent-Switch-1.2.0-${{ darwin: 'mac', win32: 'win', linux: 'linux' }[platform]}-${platform === 'linux' ? 'x86_64' : arch}.${{ darwin: 'zip', win32: 'exe', linux: 'AppImage' }[platform]}`;
    const info = { version: '1.2.0', files: [{ url: filename, size: 100, sha512: Buffer.alloc(64).toString('base64') }] };
    updater.httpExecutor = { request: async options => {
      requests.push(options);
      assert.equal(options.hostname, 'github.com');
      assert.equal(options.protocol, 'https:');
      assert.equal(options.headers?.Authorization, undefined);
      if (options.path.startsWith(`${base}.atom`)) {
        return `<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>v1.2.0</title><link href="https://github.com${base}/tag/v1.2.0"/><content>Stable</content></entry></feed>`;
      }
      if (options.path === `${base}/latest`) return JSON.stringify({ tag_name: 'v1.2.0' });
      if (options.path === `${base}/download/v1.2.0/${metadata}`) return yaml.dump(info);
      assert.fail('Unexpected request from the pinned updater');
    } };
    updater.setFeedURL({ ...FEED, channel });
    const result = await updater.checkForUpdates();
    assert.equal(result.isUpdateAvailable, true);
    assert.equal(result.downloadPromise, null);
    assert.equal(validateUpdate(result.updateInfo, '1.0.0', platform, arch), true);
    const provider = await updater.clientPromise;
    assert.equal(provider.constructor.name, 'GitHubProvider');
    assert.equal(provider.resolveFiles(result.updateInfo)[0].url.href, `https://github.com${base}/download/v1.2.0/${filename}`);
    assert.equal(requests.length, 3);
  });
}
