'use strict';

const { EventEmitter } = require('node:events');
const { CancellationToken } = require('builder-util-runtime');
const semver = require('semver');
const { isNavigationAllowed } = require('./security.cjs');

const CHANNELS = Object.freeze({
  getState: 'agent-switch:update:state',
  check: 'agent-switch:update:check',
  download: 'agent-switch:update:download',
  cancel: 'agent-switch:update:cancel',
  install: 'agent-switch:update:install',
});
const STATE_CHANNEL = 'agent-switch:update:changed';

function stableVersion(value) {
  return typeof value === 'string' && value.length < 64 && semver.valid(value) === value
    && semver.prerelease(value) === null;
}

function validateUpdate(info, currentVersion, platform, arch) {
  if (!info || !stableVersion(info.version) || !semver.gt(info.version, currentVersion)
    || (info.tag !== undefined && ![info.version, `v${info.version}`].includes(info.tag))) return false;
  const os = { darwin: 'mac', win32: 'win', linux: 'linux' }[platform];
  const ext = { darwin: 'zip', win32: 'exe', linux: 'AppImage' }[platform];
  const artifactArch = platform === 'linux' && arch === 'x64' ? 'x86_64' : arch;
  const required = `Agent-Switch-${info.version}-${os}-${artifactArch}.${ext}`;
  const allowed = new Set([required]);
  if (platform === 'darwin') allowed.add(`Agent-Switch-${info.version}-${os}-${artifactArch}.dmg`);
  return Array.isArray(info.files) && info.files.length > 0 && info.files.length < 10
    && info.files.some(file => file.url === required)
    && info.files.every(file => file && allowed.has(file.url)
      && typeof file.sha512 === 'string' && /^[A-Za-z0-9+/]{86}==$/.test(file.sha512)
      && Number.isSafeInteger(file.size) && file.size > 0);
}

class UpdateController extends EventEmitter {
  constructor({ updater = null, currentVersion, platform, arch, reason,
    confirmInstall, prepareInstall, installFailed, verifyDownload = async () => {}, validateInstall = async () => {} }) {
    super();
    Object.assign(this, { updater, platform, arch, confirmInstall, prepareInstall, installFailed, verifyDownload, validateInstall });
    this.closed = false;
    this.busy = false;
    this.token = null;
    this.downloaded = null;
    this.installStarted = false;
    this.state = {
      schemaVersion: 1, supported: Boolean(updater), status: updater ? 'idle' : 'unsupported',
      currentVersion, availableVersion: null, progress: null,
      message: reason || 'Check for a new stable release. Nothing is downloaded or installed automatically.',
    };
    if (!updater) return;
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.autoRunAppAfterInstall = true;
    updater.allowPrerelease = false;
    updater.allowDowngrade = false;
    updater.disableWebInstaller = true;
    updater.forceDevUpdateConfig = false;
    updater.logger = null;
    updater.on('error', () => {
      if (this.installStarted && !this.closed) this.failInstall();
    });
    updater.on('update-downloaded', info => {
      if (!this.closed && this.token && !this.token.cancelled
        && this.state.status === 'downloading' && info.version === this.state.availableVersion) this.downloaded = info;
    });
    updater.on('download-progress', progress => {
      if (this.closed || this.state.status !== 'downloading' || this.token?.cancelled) return;
      const safe = {};
      for (const name of ['percent', 'transferred', 'total', 'bytesPerSecond']) {
        if (!Number.isFinite(progress?.[name]) || progress[name] < 0) return;
        safe[name] = name === 'percent' ? Math.min(100, progress[name]) : Math.min(Number.MAX_SAFE_INTEGER, progress[name]);
      }
      this.update({ progress: safe });
    });
  }

  getState() {
    return { ...this.state, progress: this.state.progress ? { ...this.state.progress } : null };
  }

  update(values) {
    if (this.closed) return;
    Object.assign(this.state, values);
    this.emit('state', this.getState());
  }

  async check() {
    if (!this.updater || this.closed || this.busy || this.installStarted || this.state.status === 'downloaded') return this.getState();
    this.busy = true;
    this.downloaded = null;
    this.update({ status: 'checking', message: 'Checking the official stable release…', availableVersion: null, progress: null });
    try {
      const result = await this.updater.checkForUpdates();
      if (!result || typeof result.isUpdateAvailable !== 'boolean' || !stableVersion(result.updateInfo?.version)) throw new Error();
      if (result.isUpdateAvailable) {
        if (!validateUpdate(result.updateInfo, this.state.currentVersion, this.platform, this.arch)) throw new Error();
        this.update({ status: 'available', availableVersion: result.updateInfo.version, message: 'A new version is available. Choose Download to continue.' });
      } else {
        this.update({ status: 'not-available', message: 'No newer compatible stable release is available.' });
      }
    } catch {
      this.update({ status: 'error', message: 'Could not check for updates. Check your connection and try again; the release may not have finished publishing.' });
    } finally {
      this.busy = false;
    }
    return this.getState();
  }

  async download() {
    if (!this.updater || this.closed || this.busy || this.state.status !== 'available') return this.getState();
    this.busy = true;
    const token = new CancellationToken();
    this.token = token;
    this.downloaded = null;
    this.update({ status: 'downloading', progress: null, message: 'Downloading the update. You can cancel; the app will not restart automatically.' });
    try {
      await this.updater.downloadUpdate(token);
      if (token.cancelled || this.closed) return this.getState();
      if (!this.downloaded) throw new Error();
      await this.verifyDownload(this.downloaded);
      if (!token.cancelled && !this.closed) {
        this.update({ status: 'downloaded', progress: null, message: 'Update downloaded. Install and restart only when you are ready. Normal Quit will not install it.' });
      }
    } catch {
      if (!token.cancelled) {
        this.downloaded = null;
        this.update({ status: 'error', progress: null, message: 'The update could not be downloaded or verified. Nothing was installed. Check for updates to retry.' });
      }
    } finally {
      if (token.cancelled) {
        this.downloaded = null;
        this.update({ status: 'available', progress: null, message: 'Download cancelled. Nothing was installed.' });
      }
      this.token = null;
      this.busy = false;
    }
    return this.getState();
  }

  cancel() {
    if (!this.closed && this.token && this.state.status === 'downloading') {
      this.update({ status: 'cancelling', message: 'Cancelling the download…', progress: null });
      this.token.cancel();
    }
    return this.getState();
  }

  async install() {
    if (!this.updater || this.closed || this.busy || this.state.status !== 'downloaded') return this.getState();
    this.busy = true;
    let preparing = false;
    try {
      if (!(await this.confirmInstall()) || this.closed) return this.getState();
      this.update({ status: 'installing', progress: null, message: 'Stopping the local service before installing and restarting…' });
      await this.validateInstall();
      await this.verifyDownload(this.downloaded);
      if (this.closed) return this.getState();
      preparing = true;
      await this.prepareInstall();
      if (this.closed) return this.getState();
      this.installStarted = true;
      this.update({ message: 'Handing the update to the installer. The new version is confirmed only after relaunch.' });
      this.updater.quitAndInstall(false, true);
    } catch {
      if (preparing) this.failInstall();
      else this.update({ status: 'error', message: 'The update could not be verified for installation. Nothing was installed. Check for updates to retry.' });
    } finally {
      this.busy = false;
    }
    return this.getState();
  }

  failInstall() {
    if (this.closed || this.state.status === 'error') return;
    this.update({ status: 'error', message: 'Installation could not finish safely. Reopen Agent Switch and check its version before trying again.' });
    this.installFailed();
  }

  close() {
    this.closed = true;
    this.token?.cancel();
  }
}

function trustedContext(context, event) {
  try {
    const { contents, origin } = context || {};
    return Boolean(contents && !contents.isDestroyed() && contents.mainFrame
      && isNavigationAllowed(contents.mainFrame.url, origin)
      && isNavigationAllowed(contents.getURL(), origin)
      && (!event || (event.sender === contents && event.senderFrame === contents.mainFrame)));
  } catch {
    return false;
  }
}

function registerUpdaterIPC(ipcMain, controller, getContext) {
  for (const [method, channel] of Object.entries(CHANNELS)) {
    ipcMain.handle(channel, (event, ...args) => {
      if (args.length || !trustedContext(getContext(), event)) throw new Error('Update request denied.');
      return controller[method]();
    });
  }
  controller.on('state', state => {
    const context = getContext();
    if (trustedContext(context)) {
      try { context.contents.mainFrame.send(STATE_CHANNEL, state); } catch {}
    }
  });
}

module.exports = { UpdateController, registerUpdaterIPC, CHANNELS, STATE_CHANNEL, stableVersion, validateUpdate };
