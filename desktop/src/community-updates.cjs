'use strict';

const https = require('node:https');
const semver = require('semver');

const LATEST_RELEASE_API = 'https://api.github.com/repos/Jingyi-Jia/agents-switcher/releases/latest';
const RELEASES_URL = 'https://github.com/Jingyi-Jia/agents-switcher/releases';
const MAX_RESPONSE_BYTES = 1024 * 1024;
const REQUEST_TIMEOUT_MS = 30000;

function stableVersion(value) {
  return typeof value === 'string' && value.length < 64 && semver.valid(value) === value
    && semver.prerelease(value) === null;
}

function releaseUrl(version) {
  return stableVersion(version) ? `${RELEASES_URL}/tag/v${version}` : null;
}

function validateCommunityRelease(info, platform, arch) {
  const version = typeof info?.tag_name === 'string' && info.tag_name.startsWith('v') ? info.tag_name.slice(1) : null;
  const url = releaseUrl(version);
  if (!url || info.draft !== false || info.prerelease !== false || info.html_url !== url
    || typeof info.published_at !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(info.published_at)
    || !Number.isFinite(Date.parse(info.published_at))
    || new Date(info.published_at).toISOString() !== info.published_at.replace('Z', '.000Z')) throw new Error('Invalid public release metadata.');
  let names;
  if (platform === 'darwin' && ['arm64', 'x64'].includes(arch)) {
    names = ['dmg', 'zip'].map(ext => `Agent-Switch-${version}-mac-${arch}.${ext}`);
  } else if (platform === 'win32' && arch === 'x64') {
    names = [`Agent-Switch-${version}-win-x64.exe`];
  } else if (platform === 'linux' && arch === 'x64') {
    names = [`Agent-Switch-${version}-linux-x86_64.AppImage`, `Agent-Switch-${version}-linux-amd64.deb`,
      `Agent-Switch-${version}-linux-x64.tar.gz`];
  } else {
    throw new Error('Unsupported release platform.');
  }
  if (!Array.isArray(info.assets) || !info.assets.length || info.assets.length > 100
    || !info.assets.every(asset => asset && typeof asset.name === 'string' && /^[A-Za-z0-9][A-Za-z0-9._+-]{0,254}$/.test(asset.name)
      && asset.state === 'uploaded' && Number.isSafeInteger(asset.size) && asset.size > 0
      && asset.browser_download_url === `${RELEASES_URL}/download/v${version}/${encodeURIComponent(asset.name)}`)
    || new Set(info.assets.map(asset => asset.name)).size !== info.assets.length
    || !info.assets.some(asset => names.includes(asset.name))) throw new Error('Invalid public release assets.');
  return { version, url };
}

function checkCommunityRelease({ platform = process.platform, arch = process.arch, signal, request = https.get } = {}) {
  return new Promise((resolve, reject) => {
    let pending, response, timer, settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      response?.destroy();
      pending?.destroy();
      if (error) reject(new Error('Could not check public releases.'));
      else resolve(value);
    };
    const abort = () => finish(true);
    if (signal?.aborted) { finish(true); return; }
    signal?.addEventListener('abort', abort, { once: true });
    timer = setTimeout(abort, REQUEST_TIMEOUT_MS);
    try {
      pending = request(LATEST_RELEASE_API, {
        method: 'GET', agent: false, rejectUnauthorized: true, maxHeaderSize: 16384,
        headers: { Accept: 'application/vnd.github+json', 'Accept-Encoding': 'identity',
          'User-Agent': 'agents-switcher-desktop', 'X-GitHub-Api-Version': '2022-11-28' },
      }, incoming => {
        if (settled) { incoming.destroy(); return; }
        response = incoming;
        response.on('error', abort);
        response.on('aborted', abort);
        response.on('close', () => { if (!response.complete) abort(); });
        if (response.statusCode === 404) { finish(false, null); return; }
        const length = response.headers['content-length'];
        if (response.statusCode !== 200
          || !/^application\/(?:json|vnd\.github\+json)(?:\s*;|$)/i.test(response.headers['content-type'] || '')
          || (response.headers['content-encoding'] && response.headers['content-encoding'] !== 'identity')
          || (length !== undefined && (!/^\d+$/.test(length) || Number(length) > MAX_RESPONSE_BYTES))) {
          finish(true); return;
        }
        const chunks = [];
        let bytes = 0;
        response.on('data', chunk => {
          if (settled) return;
          bytes += chunk.length;
          if (bytes > MAX_RESPONSE_BYTES) { finish(true); return; }
          chunks.push(chunk);
        });
        response.on('end', () => {
          if (settled) return;
          try {
            if (!response.complete || (length !== undefined && bytes !== Number(length))) throw new Error();
            finish(false, validateCommunityRelease(JSON.parse(Buffer.concat(chunks).toString('utf8')), platform, arch));
          } catch {
            finish(true);
          }
        });
      });
      pending.on('error', abort);
      if (settled) pending.destroy();
    } catch {
      finish(true);
    }
  });
}

module.exports = { checkCommunityRelease, validateCommunityRelease, stableVersion, releaseUrl,
  LATEST_RELEASE_API, MAX_RESPONSE_BYTES, REQUEST_TIMEOUT_MS };
