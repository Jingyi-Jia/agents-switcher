'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const yaml = require('js-yaml');
const { stableVersion } = require('./updater.cjs');

const FEED = Object.freeze({ provider: 'github', owner: 'Jingyi-Jia', repo: 'agents-switcher', private: false, releaseType: 'release' });

function publisherNames(config) {
  const names = Array.isArray(config.publisherName) ? config.publisherName : [config.publisherName];
  if (!names.length || names.some(name => typeof name !== 'string' || !name.trim() || name.length > 512)) throw new Error();
  return names;
}

async function verifyWindowsSignature(file, names, { run = promisify(execFile), env = process.env } = {}) {
  const powershell = path.win32.join(env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const script = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $s=Get-AuthenticodeSignature -LiteralPath $env:AGENT_SWITCH_UPDATE_FILE; if ($s.Status -ne 'Valid' -or $null -eq $s.SignerCertificate) { exit 1 }; [ordered]@{path=$s.Path;subject=$s.SignerCertificate.Subject;name=$s.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false)} | ConvertTo-Json -Compress";
  const { stdout } = await run(powershell, ['-NoProfile', '-NonInteractive', '-Command', script], {
    shell: false, windowsHide: true, timeout: 25000, maxBuffer: 16384,
    env: { ...env, PSModulePath: '', AGENT_SWITCH_UPDATE_FILE: file },
  });
  const result = JSON.parse(stdout.replace(/^\uFEFF/, '').trim());
  if (typeof result.path !== 'string' || path.win32.normalize(result.path).toLowerCase() !== path.win32.normalize(file).toLowerCase()
    || !names.some(name => name === result.subject || name === result.name)) throw new Error();
}

function checkAppImage({ env, execPath, io }) {
  if (!env.APPIMAGE || !path.isAbsolute(env.APPIMAGE) || !env.APPIMAGE.endsWith('.AppImage')
    || !env.APPDIR || !path.isAbsolute(env.APPDIR) || env.APPIMAGE_EXTRACT_AND_RUN) throw new Error();
  const image = io.lstatSync(env.APPIMAGE);
  if (!image.isFile() || image.isSymbolicLink()) throw new Error();
  const relative = path.relative(io.realpathSync(env.APPDIR), io.realpathSync(execPath));
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error();
  io.accessSync(env.APPIMAGE, fs.constants.R_OK | fs.constants.W_OK);
  io.accessSync(path.dirname(env.APPIMAGE), fs.constants.W_OK | fs.constants.X_OK);
}

async function createUpdateRuntime({ app, releaseBuild, platform = process.platform, arch = process.arch,
  env = process.env, resourcesPath = process.resourcesPath, execPath = process.execPath, io = fs,
  run = promisify(execFile), library = () => require('electron-updater') }) {
  if (!app.isPackaged) return { reason: 'Updates are unavailable in development. Install an official desktop release.' };
  if (releaseBuild !== true || !stableVersion(app.getVersion())) {
    return { reason: 'This is a preview or unofficial build. Install an official stable release to enable updates.' };
  }
  if (!['darwin', 'win32', 'linux'].includes(platform) || (arch !== 'x64' && !(platform === 'darwin' && arch === 'arm64'))) {
    return { reason: 'In-app updates are not supported on this operating system or architecture.' };
  }
  let config;
  const channel = platform === 'darwin' ? `latest-${arch}` : 'latest';
  try {
    const content = io.readFileSync(path.join(resourcesPath, 'app-update.yml'), 'utf8');
    if (content.length > 16384) throw new Error();
    config = yaml.load(content);
    if (!config || config.provider !== FEED.provider || config.owner !== FEED.owner || config.repo !== FEED.repo
      || config.private !== false || config.channel !== channel || config.token || config.requestHeaders
      || (config.host && config.host !== 'github.com') || (config.protocol && config.protocol !== 'https')) throw new Error();
  } catch {
    return { reason: 'This installation has no valid official update configuration. Reinstall an official release.' };
  }
  let validateInstall;
  let verifyDownload = async () => {};
  try {
    if (platform === 'darwin') {
      validateInstall = async () => {
        if (!app.isInApplicationsFolder()) throw new Error();
        await run('/usr/bin/codesign', ['--verify', '--deep', '--strict', '-R',
          'anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists',
          path.posix.resolve(path.posix.dirname(execPath), '..', '..')], { shell: false, timeout: 25000, maxBuffer: 16384 });
      };
    } else if (platform === 'win32') {
      const names = publisherNames(config);
      validateInstall = async () => {
        if (!io.statSync(path.win32.join(path.win32.dirname(execPath), 'Uninstall Agent Switch.exe')).isFile()) throw new Error();
        await verifyWindowsSignature(execPath, names, { run, env });
      };
      verifyDownload = async info => {
        if (typeof info?.downloadedFile !== 'string' || !path.win32.isAbsolute(info.downloadedFile)) throw new Error();
        await verifyWindowsSignature(info.downloadedFile, names, { run, env });
      };
    } else {
      validateInstall = async () => checkAppImage({ env, execPath, io });
    }
    await validateInstall();
  } catch {
    return { reason: {
      darwin: 'Updates require an official Developer ID-signed app installed in Applications, not a DMG, ad-hoc preview, or modified app.',
      win32: 'Updates require a signed NSIS installation with a verified publisher and working Authenticode verification. Reinstall the official Windows installer.',
      linux: 'In-app updates require a writable, directly launched AppImage. DEB, tar, extracted, and read-only installations must be updated manually.',
    }[platform] };
  }
  try {
    const types = library();
    const Type = { darwin: types.MacUpdater, win32: types.NsisUpdater, linux: types.AppImageUpdater }[platform];
    const updater = new Type({ ...FEED, channel, timeout: 30000 });
    return { updater, validateInstall, verifyDownload };
  } catch {
    return { reason: 'The updater could not initialize. Reopen the app or reinstall an official release.' };
  }
}

module.exports = { createUpdateRuntime, verifyWindowsSignature, publisherNames, checkAppImage, FEED };
