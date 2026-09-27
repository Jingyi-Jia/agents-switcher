'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
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
    || result.executableActionsHidden !== true || result.releaseHidden !== true || result.accountsIsolated !== true
    || result.windowClose !== 'hide') {
    throw new SmokeError('Packaged renderer or account isolation did not match the build mode');
  }
}

async function verifyWindowState(command, readState, visibility) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const result = await command('Runtime.evaluate', { expression:
      '({visibility: document.visibilityState, sameDocument: window.__agentSwitchSmoke === true})', returnByValue: true });
    const renderer = result.result?.value;
    if (result.exceptionDetails || !renderer || renderer.sameDocument !== true) {
      throw new SmokeError('Window lifecycle did not retain the isolated renderer, backend and automation');
    }
    if (renderer.visibility === visibility) {
      const state = await readState();
      if (state?.desktop?.windowClose !== 'hide'
        || !['claude', 'codex'].every(provider => state[provider]?.available === true
          && state[provider]?.accounts?.length === 0 && state[provider]?.auto?.mode === 'dry-run')) {
        throw new SmokeError('Window lifecycle did not retain the isolated renderer, backend and automation');
      }
      return;
    }
    await delay(250);
  }
  throw new SmokeError('Window did not reach the expected visibility');
}

async function quitApplication(socket, exited, backendPort) {
  socket.send(JSON.stringify({ id: 0, method: 'Browser.close', params: {} }));
  const exit = await Promise.race([exited, delay(15000, null, { ref: false })]);
  if (!exit || exit.code !== 0 || exit.signal !== null) throw new SmokeError('Packaged app did not quit cleanly');
  const stopped = await new Promise(resolve => {
    const connection = net.createConnection({ host: '127.0.0.1', port: backendPort });
    connection.setTimeout(3000);
    connection.once('connect', () => { connection.destroy(); resolve(false); });
    connection.once('timeout', () => { connection.destroy(); resolve(false); });
    connection.once('error', error => resolve(error.code === 'ECONNREFUSED'));
  });
  if (!stopped) throw new SmokeError('The local service remained reachable after explicit quit');
}

async function closeNativeWindow(command) {
  const result = await command('Runtime.evaluate', { expression: `(() => {
    const windows = process.mainModule.require('electron').BrowserWindow.getAllWindows();
    if (windows.length !== 1) return false;
    windows[0].close();
    return !windows[0].isDestroyed() && !windows[0].isVisible();
  })()`, returnByValue: true });
  if (result.exceptionDetails || result.result?.value !== true) throw new SmokeError('Native window close did not hide the existing window');
}

async function closeFullScreenWindow(command) {
  const result = await command('Runtime.evaluate', { expression: `(async () => {
    const windows = process.mainModule.require('electron').BrowserWindow.getAllWindows();
    if (windows.length !== 1) return 'window-count';
    const window = windows[0];
    const transition = (event, action) => new Promise(resolve => {
      const done = () => { clearTimeout(timer); resolve(true); };
      const timer = setTimeout(() => { window.removeListener(event, done); resolve(false); }, 10000);
      window.once(event, done);
      action();
    });
    if (!await transition('enter-full-screen', () => window.setFullScreen(true))) return 'enter-timeout';
    if (!window.isFullScreen()) return 'not-full-screen-after-entry';
    if (!window.isVisible()) return 'not-visible-after-entry';
    const nativeHide = window.hide;
    let leftFullScreen = false, hiddenEarly = false;
    const onLeave = () => { leftFullScreen = true; };
    window.on('leave-full-screen', onLeave);
    try {
      const hidden = await new Promise(resolve => {
        const timer = setTimeout(() => resolve(false), 10000);
        window.hide = function (...args) {
          if (!leftFullScreen) hiddenEarly = true;
          const result = nativeHide.apply(this, args);
          clearTimeout(timer);
          resolve(true);
          return result;
        };
        window.close();
      });
      if (!hidden) return 'hide-timeout';
      if (!leftFullScreen) return 'exit-event-missing';
      if (hiddenEarly) return 'hidden-before-exit';
      if (window.isDestroyed()) return 'window-destroyed';
      if (window.isFullScreen()) return 'still-full-screen';
      if (window.isVisible()) return 'still-visible';
      return true;
    } finally {
      window.hide = nativeHide;
      window.removeListener('leave-full-screen', onLeave);
    }
  })()`, awaitPromise: true, returnByValue: true });
  if (!result.exceptionDetails && result.result?.value === true) return;
  const phases = ['window-count', 'enter-timeout', 'not-full-screen-after-entry', 'not-visible-after-entry',
    'hide-timeout', 'exit-event-missing', 'hidden-before-exit', 'window-destroyed', 'still-full-screen', 'still-visible'];
  const phase = !result.exceptionDetails && phases.includes(result.result?.value) ? result.result.value : 'evaluation-failed';
  throw new SmokeError(`Native full-screen close did not leave full screen before hiding (${phase})`);
}

async function connectDebugger(address) {
  const endpoint = new URL(address);
  if (endpoint.protocol !== 'ws:' || endpoint.hostname !== '127.0.0.1' || endpoint.username || endpoint.password) {
    throw new SmokeError('Debug endpoint was not local');
  }
  const socket = new WebSocket(endpoint);
  const pending = new Map();
  let sequence = 0;
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new SmokeError('Debug connection timed out')), 5000);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new SmokeError('Debug connection failed')); }, { once: true });
    });
  } catch (error) {
    socket.close();
    throw error;
  }
  socket.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(event.data); } catch { socket.close(); return; }
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) request.reject(new SmokeError('Packaged app debug command failed'));
    else request.resolve(message.result);
  });
  socket.addEventListener('close', () => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(new SmokeError('Debug connection closed'));
    }
    pending.clear();
  });
  return {
    socket,
    command: (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new SmokeError('Packaged app debug command timed out')); }, 30000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    }),
  };
}

function readJSON(port, pathname, headers = {}) {
  const backend = pathname === '/api/state';
  const timeoutMs = backend ? 30000 : 3000;
  const phase = backend ? 'Backend state collection' : 'Debugger discovery';
  return new Promise((resolve, reject) => {
    let request, settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) {
        request?.destroy();
        reject(new SmokeError(`${phase} ${error}`));
      } else resolve(value);
    };
    const timer = setTimeout(() => finish(`request timed out after ${timeoutMs / 1000} seconds`), timeoutMs);
    try {
      request = http.get({ hostname: '127.0.0.1', port, path: pathname, headers }, response => {
        let body = '';
        response.setEncoding('utf8');
        response.on('data', chunk => {
          body += chunk;
          if (body.length > 65536) finish('response exceeded its bound');
        });
        response.on('error', () => finish('response failed'));
        response.on('aborted', () => finish('response was interrupted'));
        response.on('end', () => {
          try {
            if (response.statusCode !== 200) throw new SmokeError();
            finish(null, JSON.parse(body));
          } catch { finish('response was invalid'); }
        });
      });
      request.on('error', () => finish('request failed'));
    } catch { finish('request failed'); }
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
  let child, second, renderer, mainDebugger;
  try {
    const profile = path.join(root, 'electron');
    const env = isolatedEnvironment(root);
    child = spawn(executable, [`--user-data-dir=${profile}`, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', '--inspect=127.0.0.1:0'], {
      cwd: root, env, stdio: ['ignore', 'ignore', 'pipe'], shell: false,
    });
    let inspectorAddress, diagnostic = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => {
      if (inspectorAddress) return;
      diagnostic = (diagnostic + chunk).slice(-8192);
      inspectorAddress = diagnostic.match(/Debugger listening on (ws:\/\/127\.0\.0\.1:\d+\/[a-f0-9-]+)/)?.[1];
      if (inspectorAddress) diagnostic = '';
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
          target = (await readJSON(port, '/json/list')).find(item => item.type === 'page' && item.url.startsWith('http://127.0.0.1:'));
          if (target && inspectorAddress) break;
        }
      } catch {}
      await delay(250);
    }
    if (!target || !inspectorAddress) throw new SmokeError('Packaged app did not expose its local debuggers in time');
    const address = new URL(target.url);
    const readState = () => readJSON(Number(address.port), '/api/state', { 'X-Auth-Token': address.searchParams.get('token') });
    renderer = await connectDebugger(target.webSocketDebuggerUrl);
    const { command } = renderer;
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
        windowClose: accounts.desktop?.windowClose,
        checkEnabled: !document.getElementById('update-check').disabled,
        settingsVisible: !document.getElementById('view-settings').hidden,
        updatesVisible: !document.getElementById('app-updates').hidden,
        releaseHidden: document.getElementById('update-view-release').hidden,
        executableActionsHidden: ['download', 'cancel', 'install'].every(action => document.getElementById('update-' + action).hidden),
        accountsIsolated: response.ok && ['claude', 'codex'].every(provider => accounts[provider]?.available === true && accounts[provider]?.accounts?.length === 0)};
    })()`, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new SmokeError('Packaged renderer evaluation failed');
    validateRenderer(result.result?.value, require('../package.json').version, distribution);
    const automation = await command('Runtime.evaluate', { expression: `(async () => {
      const headers = {'X-Auth-Token': new URL(location.href).searchParams.get('token'), 'Content-Type': 'application/json'};
      for (const provider of ['claude', 'codex']) {
        const response = await fetch('/api/auto', {method: 'POST', headers, body: JSON.stringify({provider, mode: 'dry-run'})});
        if (!response.ok || (await response.json()).ok !== true) return false;
      }
      window.__agentSwitchSmoke = true;
      return true;
    })()`, awaitPromise: true, returnByValue: true });
    if (automation.exceptionDetails || automation.result?.value !== true) throw new SmokeError('Isolated automation did not start');
    await verifyWindowState(command, readState, 'visible');
    mainDebugger = await connectDebugger(inspectorAddress);
    for (const fullScreen of platform === 'mac' ? [false, true] : [false]) {
      if (fullScreen) await closeFullScreenWindow(mainDebugger.command);
      else await closeNativeWindow(mainDebugger.command);
      await verifyWindowState(command, readState, 'hidden');
      second = spawn(executable, [`--user-data-dir=${profile}`], { cwd: root, env, stdio: 'ignore', shell: false });
      const secondExit = await Promise.race([
        new Promise((resolve, reject) => {
          second.once('error', () => reject(new SmokeError('Second app launch failed')));
          second.once('exit', (code, signal) => resolve({ code, signal }));
        }),
        delay(10000, null, { ref: false }),
      ]);
      if (!secondExit || secondExit.code !== 0 || secondExit.signal !== null) throw new SmokeError('Second launch did not return to the running app');
      await verifyWindowState(command, readState, 'visible');
    }
    mainDebugger.socket.close();
    if (screenshot) {
      const image = await command('Page.captureScreenshot', { format: 'png' });
      fs.mkdirSync(path.dirname(path.resolve(screenshot)), { recursive: true });
      fs.writeFileSync(screenshot, Buffer.from(image.data, 'base64'));
    }
    await quitApplication(renderer.socket, exited, Number(address.port));
    console.log(`Packaged ${platform}-${arch} app smoke passed (${distribution}; isolated accounts, native bridge, renderer, close hides${platform === 'mac' ? ', full-screen exit before hide' : ''}, second launch reopens, automation retained, explicit quit stops backend)`);
  } finally {
    mainDebugger?.socket.close();
    renderer?.socket.close();
    for (const process of [second, child]) {
      if (process?.pid && process.exitCode === null && process.signalCode === null) {
        process.kill();
        for (let i = 0; i < 40 && process.exitCode === null && process.signalCode === null; i += 1) await delay(250);
        if (process.exitCode === null && process.signalCode === null) process.kill('SIGKILL');
      }
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

module.exports = { isolatedEnvironment, validateRenderer, verifyWindowState, closeNativeWindow, closeFullScreenWindow, quitApplication, readJSON, exercise };
