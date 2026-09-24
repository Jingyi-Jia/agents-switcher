'use strict';

const { EventEmitter } = require('node:events');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const path = require('node:path');
const { dashboardURL } = require('./security.cjs');

const MESSAGES = Object.freeze({
  launch_failed: 'The local service could not be started. Reinstall Agent Switch or check your development Python environment.',
  startup_timeout: 'The local service took too long to start. Try again or restart Agent Switch.',
  invalid_response: 'The local service returned an unsupported response. Reinstall Agent Switch and try again.',
  backend_error: 'The local service could not continue. Try again or restart Agent Switch.',
  unexpected_exit: 'The local service stopped unexpectedly. Try again to reconnect.',
  shutdown_failed: 'The local service did not confirm shutdown. Agent Switch will now close.',
  stopped: 'The local service was stopped.',
});

class BackendError extends Error {
  constructor(code) {
    const safeCode = Object.hasOwn(MESSAGES, code) ? code : 'backend_error';
    super(MESSAGES[safeCode]);
    this.code = safeCode;
  }
}

function backendCommand({ isPackaged, resourcesPath, repoPath, env = process.env, platform = process.platform }) {
  if (isPackaged) {
    return {
      command: path.join(resourcesPath, 'backend', platform === 'win32' ? 'agent-switch-backend.exe' : 'agent-switch-backend'),
      args: [],
      cwd: path.join(resourcesPath, 'backend'),
    };
  }
  if (env.AGENT_SWITCH_BACKEND) return { command: env.AGENT_SWITCH_BACKEND, args: [], cwd: repoPath };
  return {
    command: env.AGENT_SWITCH_PYTHON || path.join(repoPath, '.venv', ...(platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'])),
    args: ['-m', 'claude_swap.desktop'],
    cwd: repoPath,
  };
}

class Backend extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.state = 'idle';
    this.child = null;
    this.buffer = '';
    this.startupTimer = null;
    this.killTimer = null;
    this.finalTimer = null;
    this.stopPromise = null;
  }

  start() {
    if (this.state !== 'idle') return Promise.reject(new BackendError('launch_failed'));
    this.state = 'starting';
    return new Promise((resolve, reject) => {
      this.resolveStart = resolve;
      this.rejectStart = reject;
      const token = randomBytes(32).toString('hex');
      const command = backendCommand(this.options);
      try {
        this.child = (this.options.spawn || spawn)(command.command, command.args, {
          cwd: command.cwd,
          stdio: ['pipe', 'pipe', 'pipe'],
          shell: false,
          windowsHide: true,
        });
      } catch {
        this.state = 'stopped';
        reject(new BackendError('launch_failed'));
        return;
      }
      this.child.stderr.resume();
      this.child.stdout.setEncoding('utf8');
      this.child.stdout.on('data', (data) => this.receive(data, token));
      this.child.stdout.on('error', () => this.fail('backend_error'));
      this.child.stderr.on('error', () => {});
      this.child.stdin.on('error', () => {
        if (this.state !== 'stopping') this.fail('backend_error');
      });
      this.child.on('error', () => {
        this.fail('launch_failed');
        if (!this.child.pid) this.exited();
      });
      this.child.once('exit', () => this.exited());
      this.child.stdout.once('end', () => {
        if (this.state === 'starting' || this.state === 'running') this.fail('unexpected_exit');
      });
      this.startupTimer = setTimeout(() => this.fail('startup_timeout'), this.options.startupTimeoutMs ?? 20000);
      try {
        this.child.stdin.write(`${JSON.stringify({ type: 'start', protocol: 1, token })}\n`);
      } catch {
        this.fail('launch_failed');
      }
    });
  }

  receive(data, token) {
    if (this.state !== 'starting' && this.state !== 'running') return;
    this.buffer += data;
    while (this.buffer.includes('\n')) {
      const end = this.buffer.indexOf('\n');
      if (end > 4096) return this.fail('invalid_response');
      const line = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 1);
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        return this.fail('invalid_response');
      }
      if (!message || typeof message !== 'object') return this.fail('invalid_response');
      if (message.type === 'error') return this.fail('backend_error');
      if (this.state !== 'starting' || message.type !== 'ready' || message.protocol !== 1
          || Object.keys(message).sort().join(',') !== 'port,protocol,type'
          || !Number.isInteger(message.port) || message.port < 1 || message.port > 65535) {
        return this.fail('invalid_response');
      }
      clearTimeout(this.startupTimer);
      this.state = 'running';
      this.resolveStart(dashboardURL(message.port, token));
    }
    if (this.buffer.length > 4096) this.fail('invalid_response');
  }

  fail(code) {
    const previous = this.state;
    if (previous !== 'starting' && previous !== 'running') return;
    const error = new BackendError(code);
    if (previous === 'starting') this.rejectStart(error);
    void this.stop().catch(() => {});
    if (previous === 'running') this.emit('failure', error);
  }

  exited() {
    const previous = this.state;
    this.state = 'stopped';
    clearTimeout(this.startupTimer);
    clearTimeout(this.killTimer);
    clearTimeout(this.finalTimer);
    if (previous === 'starting') this.rejectStart(new BackendError('unexpected_exit'));
    if (this.resolveStop) this.resolveStop();
    if (previous === 'running') this.emit('failure', new BackendError('unexpected_exit'));
  }

  stop() {
    if (this.stopPromise) return this.stopPromise;
    if (this.state === 'idle' || this.state === 'stopped') return Promise.resolve();
    const previous = this.state;
    this.state = 'stopping';
    clearTimeout(this.startupTimer);
    if (previous === 'starting') this.rejectStart(new BackendError('stopped'));
    this.stopPromise = new Promise((resolve, reject) => {
      this.resolveStop = resolve;
      this.killTimer = setTimeout(() => {
        this.finalTimer = setTimeout(() => reject(new BackendError('shutdown_failed')), this.options.killTimeoutMs ?? 2000);
        this.forceKill();
      }, this.options.shutdownTimeoutMs ?? 35000);
      try {
        this.child.stdin.end(`${JSON.stringify({ type: 'shutdown' })}\n`);
      } catch {
        this.forceKill();
      }
    });
    return this.stopPromise;
  }

  forceKill() {
    if (!this.child || this.state === 'stopped') return;
    try {
      this.child.kill('SIGKILL');
    } catch {}
  }
}

module.exports = { Backend, BackendError, backendCommand };
