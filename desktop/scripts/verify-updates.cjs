'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const yaml = require('js-yaml');
const asar = require('@electron/asar');
const { FEED, publisherNames } = require('../src/updater-runtime.cjs');
const { stableVersion } = require('../src/updater.cjs');

async function digest(file) {
  const hash = crypto.createHash('sha512');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('base64');
}

function metadataName(platform, arch) {
  if (platform === 'mac' && ['arm64', 'x64'].includes(arch)) return `latest-${arch}-mac.yml`;
  if (platform === 'win' && arch === 'x64') return 'latest.yml';
  if (platform === 'linux' && arch === 'x64') return 'latest-linux.yml';
  throw new Error('Unsupported update target');
}

function verifyBlockmap(content, size) {
  const blockmap = JSON.parse(content);
  if (blockmap.version !== '2' || !Array.isArray(blockmap.files) || blockmap.files.length !== 1) throw new Error('Invalid update blockmap');
  const part = blockmap.files[0];
  if (part.offset !== 0 || !Array.isArray(part.sizes) || !Array.isArray(part.checksums)
    || part.sizes.length === 0 || part.sizes.length !== part.checksums.length
    || !part.checksums.every(value => typeof value === 'string' && /^[A-Za-z0-9+/]{24}$/.test(value))
    || !part.sizes.every(value => Number.isSafeInteger(value) && value > 0)
    || part.sizes.reduce((total, value) => total + value, 0) !== size) throw new Error('Invalid update blockmap');
}

async function verifyArtifacts({ directory, platform, arch, version }) {
  if (!stableVersion(version)) throw new Error('Update artifacts require a stable version');
  const info = yaml.load(fs.readFileSync(path.join(directory, metadataName(platform, arch)), 'utf8'));
  const extensions = { mac: ['zip', 'dmg'], win: ['exe'], linux: ['AppImage'] }[platform];
  const artifactArch = platform === 'linux' && arch === 'x64' ? 'x86_64' : arch;
  const required = extensions.map(ext => `Agent-Switch-${version}-${platform}-${artifactArch}.${ext}`);
  if (!info || info.version !== version || !Array.isArray(info.files) || info.files.length !== required.length
    || !required.every(name => info.files.some(file => file.url === name))) throw new Error('Update metadata references unexpected artifacts');
  for (const file of info.files) {
    if (!required.includes(file.url)) throw new Error('Unsafe update artifact reference');
    const artifact = path.join(directory, file.url);
    if (!fs.lstatSync(artifact).isFile() || fs.statSync(artifact).size !== file.size || await digest(artifact) !== file.sha512) {
      throw new Error('Update artifact size or SHA-512 does not match metadata');
    }
    if (platform !== 'linux') {
      verifyBlockmap(zlib.gunzipSync(fs.readFileSync(`${artifact}.blockmap`), { maxOutputLength: 16 * 1024 * 1024 }), file.size);
    } else {
      if (!Number.isSafeInteger(file.blockMapSize) || file.blockMapSize <= 0
        || file.blockMapSize >= file.size - 4 || file.blockMapSize > 16 * 1024 * 1024) throw new Error('AppImage embedded blockmap is missing');
      const tail = Buffer.alloc(file.blockMapSize + 4);
      const fd = fs.openSync(artifact, 'r');
      try {
        if (fs.readSync(fd, tail, 0, tail.length, file.size - tail.length) !== tail.length
          || tail.readUInt32BE(tail.length - 4) !== file.blockMapSize) throw new Error('Invalid embedded blockmap size');
        verifyBlockmap(zlib.inflateRawSync(tail.subarray(0, -4), { maxOutputLength: 16 * 1024 * 1024 }), file.size - tail.length);
      } finally {
        fs.closeSync(fd);
      }
    }
  }
  if (!required.includes(info.path) || info.sha512 !== info.files.find(file => file.url === info.path).sha512) {
    throw new Error('Legacy update metadata disagrees with artifact hashes');
  }
}

function verifyPackagedConfig({ directory, platform, arch, version, releaseBuild }) {
  const resources = platform === 'mac'
    ? path.join(directory, arch === 'arm64' ? 'mac-arm64' : 'mac', 'Agent Switch.app', 'Contents', 'Resources')
    : path.join(directory, platform === 'win' ? 'win-unpacked' : 'linux-unpacked', 'resources');
  const config = yaml.load(fs.readFileSync(path.join(resources, 'app-update.yml'), 'utf8'));
  const allowed = new Set([...Object.keys(FEED), 'channel', 'updaterCacheDirName', 'publisherName']);
  if (!config || Object.keys(config).some(key => !allowed.has(key))
    || Object.entries(FEED).some(([key, value]) => config[key] !== value)
    || config.channel !== (platform === 'mac' ? `latest-${arch}` : 'latest')) throw new Error('Packaged updater feed is not the fixed public stable feed');
  if (platform === 'win' && releaseBuild) publisherNames(config);
  const manifest = JSON.parse(asar.extractFile(path.join(resources, 'app.asar'), 'package.json').toString('utf8'));
  if (manifest.version !== version || manifest.agentSwitchRelease !== releaseBuild
    || manifest.dependencies?.['electron-updater'] !== require('../package.json').dependencies['electron-updater']) {
    throw new Error('Packaged version, updater, or release capability differs from the build');
  }
  for (const file of ['src/preload.cjs', 'src/updater.cjs', 'src/updater-runtime.cjs', 'node_modules/electron-updater/out/main.js']) {
    asar.statFile(path.join(resources, 'app.asar'), path.normalize(file));
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length < 2 || args.length > 3 || (args[2] && args[2] !== '--release')) {
    throw new Error('Usage: verify-updates.cjs mac|win|linux x64|arm64 [--release]');
  }
  const [platform, arch] = args;
  const options = { directory: path.resolve(__dirname, '../release'), platform, arch,
    version: require('../package.json').version, releaseBuild: args[2] === '--release' };
  metadataName(platform, arch);
  verifyPackagedConfig(options);
  await verifyArtifacts(options);
  console.log(`Verified ${platform}-${arch} updater configuration, artifacts, hashes, and blockmaps`);
}

if (require.main === module) void main().catch(() => {
  console.error('Desktop update verification failed. Inspect the local build artifacts before publishing.');
  process.exitCode = 1;
});

module.exports = { verifyArtifacts, verifyPackagedConfig, metadataName, digest };
