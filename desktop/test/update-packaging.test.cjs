'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const zlib = require('node:zlib');
const yaml = require('js-yaml');
const asar = require('@electron/asar');
const { verifyArtifacts, verifyPackagedConfig, metadataName, digest } = require('../scripts/verify-updates.cjs');
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
  const manifest = { version: options.version, agentSwitchRelease: true,
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

test('release workflow retains signing gates and publishes update metadata last', () => {
  const config = yaml.load(fs.readFileSync(path.join(__dirname, '../electron-builder.yml'), 'utf8'));
  assert.equal(config.extraMetadata.agentSwitchRelease, false);
  assert.deepEqual(config.publish, { ...FEED, channel: 'latest' });
  assert.equal(config.mac.publish.channel, 'latest-${arch}');
  assert.equal(config.win.verifyUpdateCodeSignature, true);
  assert.ok(config.mac.target.includes('zip'));
  assert.equal(config.deb.publish, null);
  const workflow = yaml.load(fs.readFileSync(path.join(__dirname, '../../.github/workflows/desktop.yml'), 'utf8'));
  assert.equal(workflow.permissions.contents, 'read');
  assert.equal(workflow.jobs['upload-release'].needs, 'build');
  const steps = workflow.jobs.build.steps;
  const mac = steps.find(step => step.name === 'Build signed and notarized macOS release');
  const win = steps.find(step => step.name === 'Build signed Windows release');
  for (const step of [mac, win]) {
    assert.match(step.if, /github.event_name == 'release'/);
    assert.match(step.run, /Required release signing secret is missing/);
    assert.match(step.run, /-c.forceCodeSigning=true/);
    assert.match(step.run, /-c.extraMetadata.agentSwitchRelease=true/);
  }
  assert.match(mac.run, /certificate leaf\[field\.1\.2\.840\.113635\.100\.6\.1\.13\]/);
  assert.match(mac.run, /stapler validate/);
  const winVerification = steps.find(step => step.name === 'Verify Windows release signatures');
  assert.match(winVerification.run, /Get-AuthenticodeSignature/);
  assert.match(winVerification.run, /Status -ne 'Valid'/);
  for (const step of steps.filter(step => /preview/.test(step.name || ''))) {
    assert.match(step.if, /github.event_name != 'release'/);
    assert.doesNotMatch(step.run, /agentSwitchRelease=true/);
  }
  const artifacts = steps.find(step => step.name === 'Upload installers').with.path;
  assert.match(artifacts, /\*\.blockmap/); assert.match(artifacts, /latest\*\.yml/);
  const publish = workflow.jobs['upload-release'].steps.at(-1).run;
  assert.ok(publish.indexOf('installers/Agent-Switch-*') < publish.indexOf('installers/latest*.yml'));
  assert.match(require('../package.json').scripts.dist, /--publish never$/);
});
