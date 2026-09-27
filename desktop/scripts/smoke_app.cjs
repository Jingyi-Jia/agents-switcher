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
    const state = await readState();
    if (result.exceptionDetails || !renderer || renderer.sameDocument !== true || state?.desktop?.windowClose !== 'hide'
      || !['claude', 'codex'].every(provider => state[provider]?.available === true
        && state[provider]?.accounts?.length === 0 && state[provider]?.auto?.mode === 'dry-run')) {
      throw new SmokeError('Window lifecycle did not retain the isolated renderer, backend and automation');
    }
    if (renderer.visibility === visibility) return;
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
  return new Promise((resolve, reject) => {
    const request = http.get({ hostname: '127.0.0.1', port, path: pathname, headers, timeout: 3000 }, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => {
        body += chunk;
        if (body.length > 65536) request.destroy(new SmokeError('Local response exceeded its bound'));
      });
      response.on('error', reject);
      response.on('end', () => {
        try {
          if (response.statusCode !== 200) throw new SmokeError();
          resolve(JSON.parse(body));
        } catch { reject(new SmokeError('Local response was invalid')); }
      });
    });
    request.on('timeout', () => request.destroy(new SmokeError('Local request timed out')));
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
    await closeNativeWindow(mainDebugger.command);
    mainDebugger.socket.close();
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
    if (screenshot) {
      const image = await command('Page.captureScreenshot', { format: 'png' });
      fs.mkdirSync(path.dirname(path.resolve(screenshot)), { recursive: true });
      fs.writeFileSync(screenshot, Buffer.from(image.data, 'base64'));
    }
    await quitApplication(renderer.socket, exited, Number(address.port));
    console.log(`Packaged ${platform}-${arch} app smoke passed (${distribution}; isolated accounts, native bridge, renderer, close hides, second launch reopens, automation retained, explicit quit stops backend)`);
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

module.exports = { isolatedEnvironment, validateRenderer, verifyWindowState, closeNativeWindow, quitApplication, exercise };
