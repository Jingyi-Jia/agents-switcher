'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

class SmokeError extends Error {}

function isolatedEnvironment(root, source = process.env) {
  const allowed = new Set(['PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'LANG', 'LC_ALL', 'TZ',
    'DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS']);
  const env = Object.fromEntries(Object.entries(source).filter(([key]) => allowed.has(key.toUpperCase())));
  for (const [key, directory] of Object.entries({
    HOME: 'home', USERPROFILE: 'home', XDG_CONFIG_HOME: 'config', XDG_DATA_HOME: 'data',
    XDG_CACHE_HOME: 'cache', XDG_STATE_HOME: 'state', XDG_RUNTIME_DIR: 'runtime',
    CLAUDE_CONFIG_DIR: 'claude', CODEX_HOME: 'codex', APPDATA: 'appdata', LOCALAPPDATA: 'localappdata',
    TMPDIR: 'tmp', TEMP: 'tmp', TMP: 'tmp',
  })) {
    env[key] = path.join(root, directory);
    fs.mkdirSync(env[key], { recursive: true, mode: 0o700 });
  }
  env.PYTHON_KEYRING_BACKEND = 'keyring.backends.null.Keyring';
  return env;
}

function validateRenderer(result, version, distribution) {
  const manual = distribution === 'community';
  if (!result || result.version !== version || result.mode !== (manual ? 'manual' : 'unsupported')
    || result.status !== (manual ? 'idle' : 'unsupported') || result.supported !== manual
    || result.checkEnabled !== manual || result.updatesVisible !== true || result.settingsVisible !== true
    || result.executableActionsHidden !== true || result.releaseHidden !== true || result.accountsIsolated !== true) {
    throw new SmokeError('Packaged renderer or account isolation did not match the build mode');
  }
}

function targets(port) {
  return new Promise((resolve, reject) => {
    const request = http.get({ hostname: '127.0.0.1', port, path: '/json/list', timeout: 3000 }, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => {
        body += chunk;
        if (body.length > 65536) request.destroy(new SmokeError('Debug target response exceeded its bound'));
      });
      response.on('error', reject);
      response.on('end', () => {
        try {
          if (response.statusCode !== 200) throw new SmokeError();
          resolve(JSON.parse(body));
        } catch { reject(new SmokeError('Debug target response was invalid')); }
      });
    });
    request.on('timeout', () => request.destroy(new SmokeError('Debug target request timed out')));
    request.on('error', reject);
  });
}

async function exercise({ platform, arch, distribution, disposable, screenshot }) {
  if ({ darwin: 'mac', win32: 'win', linux: 'linux' }[process.platform] !== platform || arch !== process.arch
    || !['preview', 'community', 'signed'].includes(distribution)) throw new SmokeError('A matching native target and valid distribution are required');
  if (process.platform !== 'linux' && !disposable) {
    throw new SmokeError('HOME isolation does not isolate native credential stores; use a disposable VM with --disposable-runner');
  }
  const desktop = path.resolve(__dirname, '..');
  const executable = path.join(desktop, 'release', ...{
    mac: [`mac${arch === 'arm64' ? '-arm64' : ''}`, 'Agent Switch.app', 'Contents', 'MacOS', 'Agent Switch'],
    win: ['win-unpacked', 'Agent Switch.exe'], linux: ['linux-unpacked', 'agent-switch-desktop'],
  }[platform]);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-switch-app-smoke-'));
  let child, socket;
  const pending = new Map();
  let sequence = 0;
  try {
    const profile = path.join(root, 'electron');
    child = spawn(executable, [`--user-data-dir=${profile}`, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0'], {
      cwd: root, env: isolatedEnvironment(root), stdio: 'ignore', shell: false,
    });
    let spawnFailed = false;
    child.on('error', () => { spawnFailed = true; });
    const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
    const deadline = Date.now() + 60000;
    let target;
    while (Date.now() < deadline) {
      if (spawnFailed || child.exitCode !== null || child.signalCode !== null) throw new SmokeError('Packaged app exited before the renderer became ready');
      try {
        const port = Number(fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]);
        if (Number.isInteger(port) && port > 0 && port < 65536) {
          target = (await targets(port)).find(item => item.type === 'page' && item.url.startsWith('http://127.0.0.1:'));
          if (target) break;
        }
      } catch {}
      await delay(250);
    }
    if (!target) throw new SmokeError('Packaged app did not expose its local renderer in time');
    const endpoint = new URL(target.webSocketDebuggerUrl);
    if (endpoint.protocol !== 'ws:' || endpoint.hostname !== '127.0.0.1' || endpoint.username || endpoint.password) {
      throw new SmokeError('Debug endpoint was not local');
    }
    socket = new WebSocket(endpoint);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new SmokeError('Debug connection timed out')), 5000);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new SmokeError('Debug connection failed')); }, { once: true });
    });
    socket.addEventListener('message', event => {
      let message;
      try { message = JSON.parse(event.data); } catch { socket.close(); return; }
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      clearTimeout(request.timer);
      if (message.error) request.reject(new SmokeError('Packaged renderer command failed'));
      else request.resolve(message.result);
    });
    socket.addEventListener('close', () => {
      for (const request of pending.values()) {
        clearTimeout(request.timer);
        request.reject(new SmokeError('Debug connection closed'));
      }
      pending.clear();
    });
    const command = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new SmokeError('Packaged renderer command timed out')); }, 30000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
    let ready = false;
    while (Date.now() < deadline) {
      const result = await command('Runtime.evaluate', { expression: "Boolean(window.agentSwitchUpdater && document.getElementById('update-kind')?.textContent)", returnByValue: true });
      if (result.result?.value === true) { ready = true; break; }
      await delay(250);
    }
    if (!ready) throw new SmokeError('Packaged update controls did not become ready');
    const result = await command('Runtime.evaluate', { expression: `(async () => {
      document.getElementById('nav-settings').click();
      const state = await window.agentSwitchUpdater.getState();
      const response = await fetch('/api/state', {headers: {'X-Auth-Token': new URL(location.href).searchParams.get('token')}});
      const accounts = await response.json();
      return {version: state.currentVersion, mode: state.mode, status: state.status, supported: state.supported,
        checkEnabled: !document.getElementById('update-check').disabled,
        settingsVisible: !document.getElementById('view-settings').hidden,
        updatesVisible: !document.getElementById('app-updates').hidden,
        releaseHidden: document.getElementById('update-view-release').hidden,
        executableActionsHidden: ['download', 'cancel', 'install'].every(action => document.getElementById('update-' + action).hidden),
        accountsIsolated: response.ok && ['claude', 'codex'].every(provider => accounts[provider]?.available === true && accounts[provider]?.accounts?.length === 0)};
    })()`, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new SmokeError('Packaged renderer evaluation failed');
    validateRenderer(result.result?.value, require('../package.json').version, distribution);
    if (screenshot) {
      const image = await command('Page.captureScreenshot', { format: 'png' });
      fs.mkdirSync(path.dirname(path.resolve(screenshot)), { recursive: true });
      fs.writeFileSync(screenshot, Buffer.from(image.data, 'base64'));
    }
    await command('Runtime.evaluate', { expression: 'setTimeout(() => window.close(), 100); true', returnByValue: true });
    const exit = await Promise.race([exited, delay(45000, null, { ref: false })]);
    if (!exit || exit.code !== 0 || exit.signal !== null) throw new SmokeError('Packaged app did not quit cleanly');
    console.log(`Packaged ${platform}-${arch} app smoke passed (${distribution}; isolated accounts, native bridge, renderer, normal quit)`);
  } finally {
    for (const request of pending.values()) clearTimeout(request.timer);
    socket?.close();
    if (child?.pid && child.exitCode === null && child.signalCode === null) {
      child.kill();
      for (let i = 0; i < 40 && child.exitCode === null && child.signalCode === null; i += 1) await delay(250);
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
    fs.rmSync(root, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 });
  }
}

if (require.main === module) {
  const [platform, arch, distribution, ...flags] = process.argv.slice(2);
  const screenshotIndex = flags.indexOf('--screenshot');
  void exercise({ platform, arch, distribution, disposable: flags.includes('--disposable-runner'),
    screenshot: screenshotIndex < 0 ? undefined : flags[screenshotIndex + 1] }).catch(error => {
    console.error(error instanceof SmokeError ? `Packaged app smoke failed: ${error.message}` : 'Packaged app smoke failed; native diagnostics are withheld to protect credentials and debug URLs.');
    process.exitCode = 1;
  });
}

module.exports = { isolatedEnvironment, validateRenderer, exercise };
