'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { CHANNELS, STATE_CHANNEL } = require('../src/updater.cjs');

function preload(isMainFrame) {
  const calls = [], listeners = new Map(); let api;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/preload.cjs'), 'utf8'), {
    process: { isMainFrame },
    require: name => {
      assert.equal(name, 'electron');
      return {
        contextBridge: { exposeInMainWorld: (key, value) => { assert.equal(key, 'agentSwitchUpdater'); api = value; } },
        ipcRenderer: { invoke: (...args) => { calls.push(args); return Promise.resolve({ status: 'idle' }); },
          on: (key, listener) => listeners.set(key, listener),
          removeListener: (key, listener) => { assert.equal(listeners.get(key), listener); listeners.delete(key); } },
      };
    },
  });
  return { api, calls, listeners };
}

test('preload grants only fixed no-argument updater methods, never raw IPC', async () => {
  const { api, calls, listeners } = preload(true);
  assert.deepEqual(Object.keys(api).sort(), [...Object.keys(CHANNELS), 'onState'].sort());
  assert.equal(Object.isFrozen(api), true);
  for (const [method, channel] of Object.entries(CHANNELS)) {
    await api[method]('SECRET path', { url: 'https://evil.example/' });
    assert.deepEqual(calls.at(-1), [channel]);
  }
  let received;
  const unsubscribe = api.onState((...args) => { received = args; });
  const snapshot = { status: 'available' };
  listeners.get(STATE_CHANNEL)({ sender: 'PRIVATE privileged event' }, snapshot);
  assert.deepEqual(received, [snapshot]);
  unsubscribe(); assert.equal(listeners.size, 0);
  assert.throws(() => api.onState('bad callback'), /callback/);
});

test('preload exposes no API to subframes', () => {
  assert.equal(preload(false).api, undefined);
});
