'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { isolatedEnvironment, validateRenderer } = require('../scripts/smoke_app.cjs');

test('packaged app smoke isolates all account paths and strips ambient credentials and overrides', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-switch-smoke-env-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const env = isolatedEnvironment(root, { PATH: '/usr/bin', DISPLAY: ':99', HOME: '/real-home', USERPROFILE: '/real-home',
    OPENAI_API_KEY: 'synthetic', CLAUDE_CONFIG_DIR: '/real-claude', CODEX_HOME: '/real-codex',
    CLAUDE_CODE_OAUTH_TOKEN: 'synthetic', GH_TOKEN: 'synthetic', PYTHONPATH: '/unsafe',
    ELECTRON_RUN_AS_NODE: '1', AGENT_SWITCH_BACKEND: '/unsafe/backend' });
  assert.equal(env.PATH, '/usr/bin');
  assert.equal(env.DISPLAY, ':99');
  for (const name of ['OPENAI_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'GH_TOKEN', 'PYTHONPATH', 'ELECTRON_RUN_AS_NODE', 'AGENT_SWITCH_BACKEND']) {
    assert.equal(env[name], undefined);
  }
  for (const name of ['HOME', 'USERPROFILE', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'APPDATA', 'LOCALAPPDATA',
    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'XDG_CACHE_HOME', 'TMPDIR', 'TEMP', 'TMP']) {
    assert.equal(path.dirname(env[name]), root);
    assert.equal(fs.statSync(env[name]).isDirectory(), true);
    if (process.platform !== 'win32') assert.equal(fs.statSync(env[name]).mode & 0o777, 0o700);
  }
  assert.equal(env.PYTHON_KEYRING_BACKEND, 'keyring.backends.null.Keyring');
});

test('native app smoke demands real manual controls, empty accounts, and no executable actions', () => {
  const result = {version: '1.2.1', mode: 'manual', status: 'idle', supported: true, checkEnabled: true,
    updatesVisible: true, settingsVisible: true, executableActionsHidden: true, releaseHidden: true, accountsIsolated: true};
  validateRenderer(result, '1.2.1', 'community');
  for (const mutation of [{version: '1.2.0'}, {mode: 'install'}, {status: 'checking'}, {supported: false},
    {checkEnabled: false}, {updatesVisible: false}, {settingsVisible: false}, {executableActionsHidden: false},
    {releaseHidden: false}, {accountsIsolated: false}]) {
    assert.throws(() => validateRenderer({...result, ...mutation}, '1.2.1', 'community'));
  }
  for (const distribution of ['preview', 'signed']) {
    validateRenderer({...result, mode: 'unsupported', status: 'unsupported', supported: false, checkEnabled: false}, '1.2.1', distribution);
  }
});
