'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const zlib = require('node:zlib');
const yaml = require('js-yaml');
const asar = require('@electron/asar');
const { verifyArtifacts, verifyPackagedConfig, metadataName, installerNames, digest } = require('../scripts/verify-updates.cjs');
const { FEED } = require('../src/updater-runtime.cjs');
const { validateUpdate } = require('../src/updater.cjs');

async function artifacts(t, platform, arch) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-switch-update-'));
  t.after(() => fs.rmSync(directory, { force: true, recursive: true }));
  const version = '1.2.0', files = [];
  const blockmap = Buffer.from(JSON.stringify({ version: '2', files: [{ offset: 0, sizes: [100], checksums: ['A'.repeat(24)] }] }));
  for (const ext of { mac: ['zip', 'dmg'], win: ['exe'], linux: ['AppImage'] }[platform]) {
    const url = `Agent-Switch-${version}-${platform}-${platform === 'linux' ? 'x86_64' : arch}.${ext}`, artifact = path.join(directory, url);
    let content = Buffer.alloc(100), blockMapSize;
    if (platform === 'linux') {
      const compressed = zlib.deflateRawSync(blockmap), tail = Buffer.alloc(4);
      blockMapSize = compressed.length; tail.writeUInt32BE(blockMapSize);
      content = Buffer.concat([content, compressed, tail]);
    } else fs.writeFileSync(`${artifact}.blockmap`, zlib.gzipSync(blockmap));
    fs.writeFileSync(artifact, content);
    files.push({ url, size: content.length, sha512: await digest(artifact), ...(blockMapSize ? { blockMapSize } : {}) });
  }
  const info = { version, files, path: files[0].url, sha512: files[0].sha512 };
  const write = () => fs.writeFileSync(path.join(directory, metadataName(platform, arch)), yaml.dump(info));
  write();
  return { directory, platform, arch, version, info, write };
}

for (const [platform, arch] of [['mac', 'arm64'], ['mac', 'x64'], ['win', 'x64'], ['linux', 'x64']]) {
  test(`verifies ${platform}-${arch} metadata, SHA-512, and complete blockmaps`, async t => {
    const options = await artifacts(t, platform, arch);
    await verifyArtifacts(options);
    assert.equal(validateUpdate(options.info, '1.1.0', { mac: 'darwin', win: 'win32', linux: 'linux' }[platform], arch), true);
    fs.appendFileSync(path.join(options.directory, options.info.files[0].url), 'tamper');
    await assert.rejects(verifyArtifacts(options), /size or SHA-512/);
  });
}

test('architectures never overwrite the same macOS feed metadata', () => {
  assert.equal(metadataName('mac', 'arm64'), 'latest-arm64-mac.yml');
  assert.equal(metadataName('mac', 'x64'), 'latest-x64-mac.yml');
  assert.notEqual(metadataName('mac', 'arm64'), metadataName('mac', 'x64'));
  assert.throws(() => metadataName('linux', 'arm64'), /Unsupported/);
});

test('verification refuses missing metadata, traversal, corrupt hashes, and absent blockmaps', async t => {
  const options = await artifacts(t, 'win', 'x64');
  const original = structuredClone(options.info);
  for (const mutation of [
    info => { info.version = '9.9.9'; },
    info => { info.files[0].url = '../outside.exe'; },
    info => { info.files[0].sha512 = 'bad'; },
    info => { info.sha512 = 'bad'; },
    info => { info.files = []; },
  ]) {
    Object.assign(options.info, structuredClone(original)); mutation(options.info); options.write();
    await assert.rejects(verifyArtifacts(options));
  }
  Object.assign(options.info, original); options.write();
  const block = path.join(options.directory, `${options.info.files[0].url}.blockmap`);
  fs.writeFileSync(block, zlib.gzipSync(Buffer.from('{"version":"2","files":[]}')));
  await assert.rejects(verifyArtifacts(options), /blockmap/);
  fs.unlinkSync(block); await assert.rejects(verifyArtifacts(options));
  fs.unlinkSync(path.join(options.directory, metadataName('win', 'x64')));
  await assert.rejects(verifyArtifacts(options));
});

test('Linux verification requires a real embedded blockmap and correct footer', async t => {
  const options = await artifacts(t, 'linux', 'x64');
  const artifact = path.join(options.directory, options.info.files[0].url);
  const bytes = fs.readFileSync(artifact); bytes.writeUInt32BE(1, bytes.length - 4); fs.writeFileSync(artifact, bytes);
  options.info.files[0].sha512 = await digest(artifact); options.info.sha512 = options.info.files[0].sha512; options.write();
  await assert.rejects(verifyArtifacts(options), /embedded blockmap/);
});

test('packaged ASAR includes the isolated bridge and pinned updater with the correct release capability', async t => {
  const options = await artifacts(t, 'win', 'x64');
  const input = path.join(options.directory, 'input'), resources = path.join(options.directory, 'win-unpacked', 'resources');
  const manifest = { version: options.version, agentSwitchRelease: true, agentSwitchCommunityRelease: false,
    dependencies: { 'electron-updater': require('../package.json').dependencies['electron-updater'] } };
  for (const file of ['src/preload.cjs', 'src/updater.cjs', 'src/updater-runtime.cjs', 'node_modules/electron-updater/out/main.js']) {
    fs.mkdirSync(path.dirname(path.join(input, file)), { recursive: true }); fs.writeFileSync(path.join(input, file), '');
  }
  fs.mkdirSync(resources, { recursive: true });
  fs.writeFileSync(path.join(input, 'package.json'), JSON.stringify(manifest));
  await asar.createPackage(input, path.join(resources, 'app.asar'));
  const config = { ...FEED, channel: 'latest', publisherName: 'Example Publisher' };
  const write = () => fs.writeFileSync(path.join(resources, 'app-update.yml'), yaml.dump(config));
  write(); verifyPackagedConfig({ ...options, releaseBuild: true });
  assert.throws(() => verifyPackagedConfig({ ...options, releaseBuild: false }), /release capability/);
  delete config.publisherName; write(); assert.throws(() => verifyPackagedConfig({ ...options, releaseBuild: true }));
  config.publisherName = 'Example Publisher'; config.token = 'PRIVATE'; write();
  assert.throws(() => verifyPackagedConfig({ ...options, releaseBuild: true }), /fixed public/);
});

test('release workflows retain strict signing gates and stage draft-only update metadata', () => {
  const config = yaml.load(fs.readFileSync(path.join(__dirname, '../electron-builder.yml'), 'utf8'));
  assert.equal(config.extraMetadata.agentSwitchRelease, false);
  assert.equal(config.extraMetadata.agentSwitchCommunityRelease, false);
  assert.deepEqual(config.publish, { ...FEED, channel: 'latest' });
  assert.equal(config.mac.publish.channel, 'latest-${arch}');
  assert.equal(config.win.verifyUpdateCodeSignature, true);
  assert.deepEqual(config.win.target, ['nsis']);
  assert.ok(config.mac.target.includes('zip'));
  assert.equal(config.deb.publish, null);
  const workflow = yaml.load(fs.readFileSync(path.join(__dirname, '../../.github/workflows/desktop.yml'), 'utf8'));
  assert.equal(workflow.permissions.contents, 'read');
  assert.equal(workflow.on.release, undefined);
  assert.equal(workflow.jobs.build.with.mode, 'preview');
  assert.equal(workflow.jobs.build.secrets, undefined);
  const native = yaml.load(fs.readFileSync(path.join(__dirname, '../../.github/workflows/desktop-build.yml'), 'utf8'));
  const release = yaml.load(fs.readFileSync(path.join(__dirname, '../../.github/workflows/release.yml'), 'utf8'));
  const steps = native.jobs.build.steps;
  const mac = steps.find(step => step.name === 'Build signed and notarized macOS release');
  const win = steps.find(step => step.name === 'Build signed Windows release');
  for (const step of [mac, win]) {
    assert.match(step.if, /inputs.mode == 'signed'/);
    assert.match(step.run, /Required release signing secret is missing/);
    assert.match(step.run, /-c.forceCodeSigning=true/);
    assert.match(step.run, /-c.extraMetadata.agentSwitchRelease=true/);
    assert.match(step.run, /-c.extraMetadata.agentSwitchCommunityRelease=false/);
  }
  assert.match(mac.run, /certificate leaf\[field\.1\.2\.840\.113635\.100\.6\.1\.13\]/);
  assert.match(mac.run, /stapler validate/);
  const winVerification = steps.find(step => step.name === 'Verify Windows release signatures');
  assert.match(winVerification.run, /Get-AuthenticodeSignature/);
  assert.match(winVerification.run, /Status -ne 'Valid'/);
  assert.match(winVerification.if, /inputs.mode == 'signed'/);
  assert.match(winVerification.run, /backend\/agent-switch-backend.exe/);
  for (const step of steps.filter(step => /preview/.test(step.name || ''))) {
    assert.match(step.if, /inputs.mode == 'preview'/);
    assert.doesNotMatch(step.run, /agentSwitchRelease=true/);
  }
  for (const step of steps.filter(step => /community installers/.test(step.name || ''))) {
    assert.match(step.if, /inputs.mode == 'community'/);
    assert.match(step.run, /agentSwitchRelease=false/);
    assert.match(step.run, /agentSwitchCommunityRelease=true/);
    assert.equal(step.env.CSC_IDENTITY_AUTO_DISCOVERY, 'false');
    assert.equal(Object.keys(step.env).some(name => /CERTIFICATE|APPLE|CSC_LINK/.test(name)), false);
  }
  const communityMac = steps.find(step => step.name === 'Build ad-hoc signed macOS community installers');
  assert.match(communityMac.run, /-c.mac.identity=-/);
  assert.match(communityMac.run, /-c.mac.notarize=false/);
  assert.equal(config.mac.hardenedRuntime, true);
  assert.equal(config.mac.entitlements, 'scripts/entitlements.mac.plist');
  assert.equal(config.mac.entitlementsInherit, config.mac.entitlements);
  assert.equal(steps.find(step => step.name === 'Upload installers').with.path, 'desktop/release/artifacts/*');
  assert.deepEqual(release.jobs.draft.needs, ['source', 'native', 'python']);
  assert.equal(release.jobs.draft.permissions.contents, 'write');
  assert.match(release.jobs.draft.steps.at(-1).run, /desktop_release.py draft/);
  assert.match(require('../package.json').scripts.dist, /--publish never$/);
});

test('the community installer allowlist matches native builder architecture names', () => {
  const { Arch, getArtifactArchName } = require('builder-util');
  for (const [platform, arch, extensions] of [
    ['mac', 'arm64', ['dmg', 'zip']], ['mac', 'x64', ['dmg', 'zip']],
    ['win', 'x64', ['exe']], ['linux', 'x64', ['AppImage', 'deb', 'tar.gz']],
  ]) {
    assert.deepEqual(installerNames(platform, arch, '3.2.1'),
      extensions.map(extension => `Agent-Switch-3.2.1-${platform}-${getArtifactArchName(Arch[arch], extension)}.${extension}`));
  }
});

test('trusted native build attests only after tests, signature gates, and packaged-helper smoke checks', () => {
  const native = yaml.load(fs.readFileSync(path.join(__dirname, '../../.github/workflows/desktop-build.yml'), 'utf8'));
  const release = yaml.load(fs.readFileSync(path.join(__dirname, '../../.github/workflows/release.yml'), 'utf8'));
  assert.deepEqual(native.jobs.build.strategy.matrix.include.map(({ platform, arch }) => `${platform}-${arch}`),
    ['mac-arm64', 'mac-x64', 'win-x64', 'linux-x64']);
  assert.deepEqual(native.on, { workflow_call: native.on.workflow_call });
  assert.equal(native.on.workflow_call.inputs.mode.default, 'preview');
  assert.equal(native.jobs.build.permissions, undefined);
  assert.deepEqual(release.jobs.native.permissions, { contents: 'read', 'id-token': 'write', attestations: 'write' });
  assert.equal(release.jobs.draft.permissions['id-token'], undefined);
  assert.equal(release.jobs.draft.permissions.attestations, 'read');
  assert.equal(release.permissions.contents, 'read');
  assert.equal(release.jobs.source.permissions, undefined);
  assert.match(release.jobs.source.if, /github.ref == 'refs\/heads\/main'/);
  assert.deepEqual(Object.keys(release.on), ['workflow_dispatch']);
  assert.deepEqual(release.on.workflow_dispatch.inputs.distribution.options, ['community', 'signed']);
  assert.equal(release.on.workflow_dispatch.inputs.distribution.default, 'community');
  assert.equal(release.on.workflow_dispatch.inputs.source_sha.required, true);
  const steps = native.jobs.build.steps;
  const attestIndex = steps.findIndex(step => step.uses?.startsWith('actions/attest@'));
  for (const name of ['Test Python backend', 'Test native process probes without competing test workers', 'Test Electron shell',
    'Verify Windows release signatures', 'Verify macOS app and distributed bundle signatures',
    'Smoke-test frozen backend in disposable account directories', 'Smoke-test the helper copied into the app',
    'Verify packaged updater and release metadata', 'Smoke-test packaged desktop app', 'Stage allowlisted final files and their checksums']) {
    const index = steps.findIndex(step => step.name === name);
    assert.ok(index >= 0 && index < attestIndex, name);
  }
  for (const step of steps.filter(step => step.name?.startsWith('Smoke-test') && step.name !== 'Smoke-test packaged desktop app')) {
    assert.match(step.run, /--disposable-runner --check-tls --check-processes/);
  }
  assert.equal(steps[attestIndex].if, "inputs.mode != 'preview'");
  assert.equal(steps[attestIndex].with['subject-path'], 'desktop/release/artifacts/*');
  assert.equal(steps[attestIndex].with['create-storage-record'], false);
  const verify = steps.findIndex(step => step.name === 'Retain and verify the native build attestation');
  assert.ok(attestIndex < verify && verify < steps.findIndex(step => step.name === 'Upload installers'));
  assert.match(steps[verify].run, /desktop_release.py verify/);
  const smokeIndex = steps.findIndex(step => step.name === 'Smoke-test packaged desktop app');
  assert.ok(smokeIndex > steps.findIndex(step => step.name === 'Verify packaged updater and release metadata'));
  assert.ok(smokeIndex < steps.findIndex(step => step.name === 'Stage allowlisted final files and their checksums'));
  assert.match(steps[smokeIndex].run, /xvfb-run -a/);
  assert.match(steps[smokeIndex].run, /smoke_app.cjs "\$TARGET_PLATFORM" "\$TARGET_ARCH" "\$DISTRIBUTION" --disposable-runner --screenshot/);
  assert.equal(steps[smokeIndex].env.DISTRIBUTION, '${{ inputs.mode }}');
  const screenshot = steps.find(step => step.name === 'Upload successful app smoke screenshot');
  assert.equal(screenshot.if, 'success()');
  assert.match(screenshot.with.name, /^app-smoke-/);
  assert.match(screenshot.with.path, /^\$\{\{ runner.temp \}\}\/app-smoke-/);
  const downloads = release.jobs.draft.steps.filter(step => step.uses?.startsWith('actions/download-artifact@'));
  assert.deepEqual(downloads.map(step => step.with.pattern || step.with.name), ['desktop-*', 'python-dist']);
  for (const workflow of [native, release]) {
    for (const job of Object.values(workflow.jobs)) {
      for (const step of job.steps || []) {
        if (step.uses) assert.match(step.uses, /@[a-f0-9]{40}$/);
      }
    }
  }
});
