'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const asar = require('@electron/asar');
const yaml = require('js-yaml');
const { verifyArtifacts, verifyPackagedConfig, installerNames, parseArgs } = require('../scripts/verify-updates.cjs');
const { FEED } = require('../src/updater-runtime.cjs');

const targets = [['mac', 'arm64'], ['mac', 'x64'], ['win', 'x64'], ['linux', 'x64']];

async function packaged(t, platform, arch, flags) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'community-packaging-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const resources = platform === 'mac'
    ? path.join(directory, arch === 'arm64' ? 'mac-arm64' : 'mac', 'Agent Switch.app', 'Contents', 'Resources')
    : path.join(directory, platform === 'win' ? 'win-unpacked' : 'linux-unpacked', 'resources');
  fs.mkdirSync(resources, { recursive: true });
  const input = path.join(directory, 'input');
  for (const file of ['src/preload.cjs', 'src/updater.cjs', 'src/updater-runtime.cjs', 'node_modules/electron-updater/out/main.js']) {
    fs.mkdirSync(path.dirname(path.join(input, file)), { recursive: true });
    fs.writeFileSync(path.join(input, file), '');
  }
  const version = '3.2.1';
  const manifest = { version, ...flags, dependencies: { 'electron-updater': require('../package.json').dependencies['electron-updater'] } };
  const write = async () => {
    fs.writeFileSync(path.join(input, 'package.json'), JSON.stringify(manifest));
    await asar.createPackage(input, path.join(resources, 'app.asar'));
    asar.uncacheAll();
  };
  await write();
  fs.writeFileSync(path.join(resources, 'app-update.yml'), yaml.dump({ ...FEED,
    channel: platform === 'mac' ? `latest-${arch}` : 'latest',
    ...(flags.agentSwitchRelease && platform === 'win' ? { publisherName: 'Synthetic Publisher' } : {}),
  }));
  for (const name of installerNames(platform, arch, version)) fs.writeFileSync(path.join(directory, name), 'synthetic installer');
  return { directory, platform, arch, version, manifest, write };
}

for (const [platform, arch] of targets) {
  for (const mode of ['preview', 'community', 'signed']) {
    test(`${mode} ${platform}-${arch} enforces both packaged capability flags`, async t => {
      const flags = { agentSwitchRelease: mode === 'signed', agentSwitchCommunityRelease: mode === 'community' };
      const options = await packaged(t, platform, arch, flags);
      const build = { ...options, releaseBuild: flags.agentSwitchRelease, communityBuild: flags.agentSwitchCommunityRelease };
      verifyPackagedConfig(build);
      for (const field of ['agentSwitchRelease', 'agentSwitchCommunityRelease']) {
        for (const invalid of [!flags[field], 'false', undefined, null, 0]) {
          options.manifest[field] = invalid;
          await options.write();
          assert.throws(() => verifyPackagedConfig(build), /release capability/);
        }
        options.manifest[field] = flags[field];
      }
    });
  }
  test(`community ${platform}-${arch} needs every installer but no feed or blockmap`, async t => {
    const options = await packaged(t, platform, arch, { agentSwitchRelease: false, agentSwitchCommunityRelease: true });
    await verifyArtifacts({ ...options, communityBuild: true });
    await assert.rejects(verifyArtifacts({ ...options, releaseBuild: true }), /ENOENT/);
    await assert.rejects(verifyArtifacts(options), /ENOENT/);
    const file = path.join(options.directory, installerNames(platform, arch, options.version).at(-1));
    fs.writeFileSync(file, '');
    await assert.rejects(verifyArtifacts({ ...options, communityBuild: true }), /missing or invalid/);
    fs.unlinkSync(file);
    await assert.rejects(verifyArtifacts({ ...options, communityBuild: true }), /ENOENT/);
  });
}

test('default packaged verification requires an explicitly preview manifest', async t => {
  const options = await packaged(t, 'win', 'x64', { agentSwitchRelease: false, agentSwitchCommunityRelease: false });
  verifyPackagedConfig(options);
  assert.throws(() => verifyPackagedConfig({ ...options, releaseBuild: true }));
  assert.throws(() => verifyPackagedConfig({ ...options, communityBuild: true }), /release capability/);
});

test('community verification rejects conflicting flags, unsupported targets, and unstable versions', async t => {
  const options = await packaged(t, 'win', 'x64', { agentSwitchRelease: false, agentSwitchCommunityRelease: true });
  for (const flags of [{ releaseBuild: true, communityBuild: true }, { communityBuild: 'true' }, { releaseBuild: null }]) {
    assert.throws(() => verifyPackagedConfig({ ...options, ...flags }), /Conflicting or invalid/);
    await assert.rejects(verifyArtifacts({ ...options, ...flags }), /Conflicting or invalid/);
  }
  assert.throws(() => installerNames('win', 'arm64', '1.0.0'), /Unsupported/);
  assert.throws(() => installerNames('linux', 'x64', '1.0.0-beta.1'), /stable/);
  await assert.rejects(verifyArtifacts({ ...options, version: '1.0.0+build', communityBuild: true }), /stable/);
});

test('command line keeps preview as the default and refuses conflicting release switches', () => {
  assert.deepEqual(parseArgs(['win', 'x64']), { platform: 'win', arch: 'x64', releaseBuild: false, communityBuild: false });
  assert.equal(parseArgs(['mac', 'arm64', '--community']).communityBuild, true);
  assert.equal(parseArgs(['linux', 'x64', '--release']).releaseBuild, true);
  for (const args of [['win', 'x64', '--release', '--community'], ['win', 'x64', '--signed'], ['win', 'x64', ''], ['linux', 'arm64'], []]) {
    assert.throws(() => parseArgs(args));
  }
});
