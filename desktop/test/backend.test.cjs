'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter, once } = require('node:events');
const { PassThrough, Writable } = require('node:stream');
const path = require('node:path');
const { Backend, BackendError, backendCommand } = require('../src/backend.cjs');

class Child extends EventEmitter {
  constructor({ hangs = false } = {}) {
    super();
    this.pid = 4321;
    this.lines = [];
    this.signals = [];
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.stdin = new Writable({ write: (chunk, _encoding, callback) => {
      this.lines.push(...chunk.toString().trim().split('\n').map((line) => JSON.parse(line)));
      callback();
    } });
    this.hangs = hangs;
  }

  kill(signal) {
    this.signals.push(signal);
    if (!this.hangs) this.emit('exit', null, signal);
    return true;
  }

  ready(port = 12345) {
    this.stdout.write(`${JSON.stringify({ type: 'ready', protocol: 1, port })}\n`);
  }
}

function fixture(child = new Child(), options = {}) {
  const calls = [];
  const backend = new Backend({
    isPackaged: false, repoPath: '/repo', env: {}, startupTimeoutMs: 100,
    shutdownTimeoutMs: 15, killTimeoutMs: 15,
    spawn: (...args) => { calls.push(args); return child; }, ...options,
  });
  return { backend, child, calls };
}

test('packaged helper ignores all development executable overrides', () => {
  const result = backendCommand({
    isPackaged: true, resourcesPath: '/app/resources', repoPath: '/repo',
    platform: 'linux', env: { AGENT_SWITCH_BACKEND: '/untrusted', AGENT_SWITCH_PYTHON: '/untrusted-python' },
  });
  assert.deepEqual(result, {
    command: path.join('/app/resources', 'backend', 'agent-switch-backend'),
    args: [], cwd: path.join('/app/resources', 'backend'),
  });
  assert.match(backendCommand({ isPackaged: true, resourcesPath: '/res', platform: 'win32' }).command, /agent-switch-backend\.exe$/);
});

test('development uses explicit Python or platform-specific repository virtualenv', () => {
  assert.deepEqual(backendCommand({ isPackaged: false, repoPath: '/repo', env: {}, platform: 'linux' }), {
    command: path.join('/repo', '.venv', 'bin', 'python'), args: ['-m', 'claude_swap.desktop'], cwd: '/repo',
  });
  assert.match(backendCommand({ isPackaged: false, repoPath: '/repo', env: {}, platform: 'win32' }).command, /Scripts[/\\]python\.exe$/);
  assert.equal(backendCommand({ isPackaged: false, repoPath: '/repo', env: { AGENT_SWITCH_PYTHON: '/explicit/python' } }).command, '/explicit/python');
  assert.deepEqual(backendCommand({ isPackaged: false, repoPath: '/repo', env: { AGENT_SWITCH_BACKEND: '/fixture' } }), {
    command: '/fixture', args: [], cwd: '/repo',
  });
});

test('starts without shell and sends a random token only through stdin', async () => {
  const { backend, child, calls } = fixture();
  const ready = backend.start();
  assert.equal(child.lines.length, 1);
  const start = child.lines[0];
  assert.equal(start.type, 'start');
  assert.equal(start.protocol, 1);
  assert.match(start.token, /^[a-f0-9]{64}$/);
  assert.equal(calls[0][2].shell, false);
  assert.equal(calls[0][2].windowsHide, true);
  assert.deepEqual(calls[0][2].stdio, ['pipe', 'pipe', 'pipe']);
  assert.ok(!JSON.stringify(calls).includes(start.token));
  child.stdout.write('{"type":"ready",');
  child.stdout.write('"protocol":1,"port":23456}\n');
  const url = new URL(await ready);
  assert.equal(url.origin, 'http://127.0.0.1:23456');
  assert.equal(url.searchParams.get('token'), start.token);
  assert.equal(url.searchParams.get('desktop'), '1');
  const stopped = backend.stop();
  assert.deepEqual(child.lines[1], { type: 'shutdown' });
  assert.equal(child.stdin.writableEnded, true);
  child.emit('exit', 0);
  await stopped;
  assert.equal(backend.state, 'stopped');
  assert.deepEqual(child.signals, []);

  const second = fixture();
  const secondReady = second.backend.start();
  second.child.ready();
  await secondReady;
  assert.notEqual(second.child.lines[0].token, start.token);
  await second.backend.stop();
});

for (const [name, response] of [
  ['raw output', 'sensitive credential error\n'],
  ['invalid JSON', '{\n'],
  ['null', 'null\n'],
  ['wrong protocol', '{"type":"ready","protocol":2,"port":1234}\n'],
  ['string port', '{"type":"ready","protocol":1,"port":"1234"}\n'],
  ['zero port', '{"type":"ready","protocol":1,"port":0}\n'],
  ['large port', '{"type":"ready","protocol":1,"port":65536}\n'],
  ['fractional port', '{"type":"ready","protocol":1,"port":12.5}\n'],
  ['auth URL field', '{"type":"ready","protocol":1,"port":1234,"url":"sensitive"}\n'],
  ['oversize line', `${'s'.repeat(4097)}\n`],
  ['oversize unfinished line', 's'.repeat(4097)],
]) {
  test(`rejects ${name} without exposing child output`, async () => {
    const { backend, child } = fixture();
    const ready = backend.start();
    child.stdout.write(response);
    await assert.rejects(ready, (error) => error.code === 'invalid_response' && !error.message.includes('sensitive'));
    await backend.stop();
    assert.equal(backend.state, 'stopped');
  });
}

test('never exposes child error code, stderr, or arbitrary exception text', async () => {
  const { backend, child } = fixture();
  const ready = backend.start();
  child.stderr.write('PRIVATE CREDENTIAL VALUE');
  child.stdout.write('{"type":"error","code":"PRIVATE CREDENTIAL VALUE"}\n');
  await assert.rejects(ready, (error) => error.code === 'backend_error' && !error.message.includes('PRIVATE'));
  await backend.stop();
  assert.equal(new BackendError('PRIVATE CREDENTIAL VALUE').code, 'backend_error');
});

test('startup timeout stops and hard-kills a non-responsive helper', async () => {
  const { backend, child } = fixture(new Child(), { startupTimeoutMs: 5 });
  await assert.rejects(backend.start(), { code: 'startup_timeout' });
  await backend.stop();
  assert.deepEqual(child.lines[1], { type: 'shutdown' });
  assert.deepEqual(child.signals, ['SIGKILL']);
});

test('shutdown waits for actual exit and is idempotent', async () => {
  const { backend, child } = fixture();
  const ready = backend.start();
  child.ready();
  await ready;
  let finished = false;
  const stop = backend.stop();
  stop.then(() => { finished = true; });
  assert.equal(backend.stop(), stop);
  await Promise.resolve();
  assert.equal(finished, false);
  child.emit('exit', 0);
  await stop;
  assert.equal(finished, true);
});

test('shutdown failure is bounded even when process exit is not reported', async () => {
  const { backend, child } = fixture(new Child({ hangs: true }));
  const ready = backend.start();
  child.ready();
  await ready;
  await assert.rejects(backend.stop(), { code: 'shutdown_failed' });
  assert.deepEqual(child.signals, ['SIGKILL']);
  child.emit('exit', 0);
});

test('quit during startup rejects ready and shuts down the helper', async () => {
  const { backend, child } = fixture();
  const ready = backend.start();
  const stopped = backend.stop();
  await assert.rejects(ready, { code: 'stopped' });
  child.emit('exit', 0);
  await stopped;
});

test('unexpected exit invalidates the running backend', async () => {
  const { backend, child } = fixture();
  const ready = backend.start();
  child.ready();
  await ready;
  const failure = once(backend, 'failure');
  child.emit('exit', 27);
  assert.equal((await failure)[0].code, 'unexpected_exit');
  assert.equal(backend.state, 'stopped');
  await backend.stop();
});

test('runtime protocol errors invalidate and stop the backend', async () => {
  const { backend, child } = fixture();
  const ready = backend.start();
  child.ready();
  await ready;
  const failure = once(backend, 'failure');
  child.ready();
  assert.equal((await failure)[0].code, 'invalid_response');
  await backend.stop();
});

test('spawn exceptions and async spawn failures are sanitized', async () => {
  const sync = fixture(new Child(), { spawn: () => { throw new Error('SECRET PATH'); } });
  await assert.rejects(sync.backend.start(), (error) => error.code === 'launch_failed' && !error.message.includes('SECRET'));
  await sync.backend.stop();
  const { backend, child } = fixture();
  const ready = backend.start();
  child.pid = undefined;
  child.emit('error', new Error('SECRET PATH'));
  await assert.rejects(ready, { code: 'launch_failed' });
  await backend.stop();
  assert.equal(backend.state, 'stopped');
});

test('EOF before readiness and exit before readiness fail safely', async () => {
  const first = fixture();
  const ready = first.backend.start();
  first.child.stdout.end();
  await assert.rejects(ready, { code: 'unexpected_exit' });
  await first.backend.stop();
  const second = fixture();
  const secondReady = second.backend.start();
  second.child.emit('exit', 1);
  await assert.rejects(secondReady, { code: 'unexpected_exit' });
  await second.backend.stop();
});

test('a controller cannot launch multiple helpers', async () => {
  const { backend, child, calls } = fixture();
  const ready = backend.start();
  await assert.rejects(backend.start(), { code: 'launch_failed' });
  child.ready();
  await ready;
  await backend.stop();
  assert.equal(calls.length, 1);
});
