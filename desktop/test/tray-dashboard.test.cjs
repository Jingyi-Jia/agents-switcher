'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const { usageItems, quotaModel, readState, TrayDashboard } = require('../src/tray-dashboard.cjs');

const now = 1800000000;
const window = (label, usedPercent, extra = {}) => ({ label, usedPercent,
  observedAt: now - 60, resetAt: now + 3600, windowSeconds: 18000, ...extra });
const labels = state => usageItems(state, now).map(item => item.label).filter(Boolean);

test('both providers show remaining windows, model limits, active and pending saved accounts', () => {
  const items = usageItems({ claude: { available: true, accounts: [
    { email: 'first@example.test', windows: [window('5h', 0), window('7d', 60), window('Fable · 7d', 100, { scope: 'model' })] },
    { email: 'second@example.test', active: true, windows: [window('5h', 100)] },
  ] }, codex: { available: true, accounts: [
    { alias: 'Work', email: 'work@example.test', disabled: true, activationRequired: true, windows: [window('5h', 25)] },
  ] } }, now);
  const text = items.map(item => item.label).join('\n');
  for (const expected of ['5h: 100% left', '7d: 40% left', 'Fable · 7d: 0% left · model limit',
    'second@example.test · active', '5h: 0% left', 'Work · excluded from auto · saved login pending', '5h: 75% left', 'resets 1h 0m']) {
    assert.ok(text.includes(expected), expected);
  }
  for (const item of items.filter(item => item.label)) {
    assert.equal(item.enabled, false);
    assert.equal(item.click, undefined);
  }
});

test('missing, error, metered and last-good reports cannot look like fresh healthy quota', () => {
  const text = labels({ claude: { available: true, accounts: [
    { email: 'unknown@example.test' },
    { error: 'SECRET transport error', windows: [window('5h', 0)] },
    { loginRequired: true },
    { sentinel: 'api key' },
    { onCredits: true, windows: [window('5h', 0)] },
    { usageFailed: true, windows: [window('7d', 60)] },
    { windows: [window('5h', null), window('7d', NaN), window('invalid', -1), window('over', 120)] },
  ] } }).join('\n');
  for (const expected of ['Quota unknown', 'Usage unavailable', 'Sign-in required', 'API key · metered usage',
    'On paid credits', 'Usage update failed · last report', '7d: 40% left', 'over: 0% left', 'Codex', 'Unavailable']) {
    assert.ok(text.includes(expected), expected);
  }
  assert.equal(text.includes('SECRET'), false);
  assert.equal(text.includes('100% left'), false);
  assert.deepEqual(labels(null), ['Loading account usage…']);
  assert.ok(labels({ claude: { available: true, accounts: [] } }).includes('  No saved accounts'));
});

test('reset countdown stays anchored to a valid measurement and never invents a reset', () => {
  for (const extra of [{ resetAt: null }, { observedAt: null }, { observedAt: now + 1 },
    { resetAt: now - 1 }, { resetAt: now + 18001 }, { windowSeconds: 0 }, { resetAt: NaN }]) {
    const text = labels({ claude: { available: true, accounts: [{ windows: [window('5h', 25, extra)] }] } }).join('\n');
    assert.equal(text.includes('resets'), false, JSON.stringify(extra));
  }
  assert.ok(labels({ claude: { available: true, accounts: [{ windows: [window('5h', 25, { resetAt: now + 10 })] }] } })
    .some(text => text.includes('resets 1m')));
});

test('local read uses header-only auth and a fixed cached state route without redirects', async t => {
  const token = 'a'.repeat(64);
  let received;
  let status = 200;
  let body = JSON.stringify({ claude: { available: true, accounts: [] } });
  const server = http.createServer((request, response) => {
    received = request;
    response.writeHead(status, { Location: 'https://example.test/' });
    response.end(body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const address = `http://127.0.0.1:${server.address().port}/?token=${token}&desktop=1`;
  assert.equal((await readState(address)).claude.available, true);
  assert.equal(received.url, '/api/state');
  assert.equal(received.headers['x-auth-token'], token);
  status = 302;
  await assert.rejects(readState(address), /Local usage unavailable/);
  status = 200;
  body = 'invalid JSON';
  await assert.rejects(readState(address), /Invalid local usage response/);
  body = 'x'.repeat(1024 * 1024 + 1);
  await assert.rejects(readState(address));
  for (const bad of ['https://127.0.0.1/?token=' + token, 'http://localhost/?token=' + token,
    'http://user@127.0.0.1/?token=' + token, 'http://127.0.0.1/?token=bad']) {
    await assert.rejects(readState(bad), /Invalid local dashboard/);
  }
});

test('cancellation discards late results, stops scheduling, and sanitizes transport failures', async () => {
  const rendered = [];
  let resolveRead;
  let signal;
  const dashboard = new TrayDashboard(items => rendered.push(items), (_address, readSignal) => {
    signal = readSignal;
    return new Promise(resolve => { resolveRead = resolve; });
  });
  dashboard.start('synthetic');
  assert.equal(rendered.length, 1);
  dashboard.stop();
  assert.equal(signal.aborted, true);
  resolveRead({});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(rendered.length, 1);
  assert.equal(dashboard.timer, null);
  const failing = new TrayDashboard(items => rendered.push(items), async () => { throw new Error('SECRET'); });
  failing.start('synthetic');
  await new Promise(resolve => setImmediate(resolve));
  assert.match(rendered.at(-1)[0].label, /unavailable/);
  assert.equal(JSON.stringify(rendered).includes('SECRET'), false);
  assert.ok(failing.timer);
  failing.stop();
});

test('state requests have a hard deadline even when response data keeps arriving', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let response;
  t.mock.method(http, 'get', (_url, _options, respond) => {
    const request = new EventEmitter();
    request.destroy = error => { request.emit('error', error); request.emit('close'); };
    response = Object.assign(new EventEmitter(), { statusCode: 200 });
    queueMicrotask(() => respond(response));
    return request;
  });
  const expired = assert.rejects(readState(`http://127.0.0.1/?token=${'a'.repeat(64)}`), /Local usage timed out/);
  await new Promise(resolve => setImmediate(resolve));
  t.mock.timers.tick(29999);
  response.emit('data', Buffer.from('{'));
  t.mock.timers.tick(1);
  await expired;
});

test('polls every 20 seconds without overlapping reads, and restart cancels the old session', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const reads = [];
  const rendered = [];
  const dashboard = new TrayDashboard(items => rendered.push(items), (address, signal) =>
    new Promise(resolve => reads.push({ address, signal, resolve })));
  dashboard.start('first');
  t.mock.timers.tick(60000);
  assert.equal(reads.length, 1);
  reads[0].resolve({});
  await new Promise(resolve => setImmediate(resolve));
  t.mock.timers.tick(19999);
  assert.equal(reads.length, 1);
  t.mock.timers.tick(1);
  assert.equal(reads.length, 2);
  dashboard.start('second');
  assert.equal(reads[1].signal.aborted, true);
  assert.equal(reads[2].address, 'second');
  const count = rendered.length;
  reads[1].resolve({});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(rendered.length, count);
  dashboard.stop();
  reads[2].resolve({});
  await new Promise(resolve => setImmediate(resolve));
  t.mock.timers.tick(60000);
  assert.equal(reads.length, 3);
});

test('popover projection allowlists display fields and never forwards errors, credentials, paths or unknown records', () => {
  const secret = 'SECRET-TOKEN';
  const model = quotaModel({ token: secret, backendURL: secret, claudeDesktop: { profiles: [secret] },
    preferences: { theme: 'dark', token: secret, profileNoticeVersion: 1 },
    claude: { available: true, error: secret, liveLogin: secret, auto: { token: secret }, accounts: [
      { alias: 'Work\naccount', email: 'not-projected@example.test', active: true, disabled: true,
        activationRequired: true, auth: { refresh_token: secret }, unknown: secret, number: '1',
        windows: [window('5h', 12.345, { token: secret, debug: secret, scope: 'model' })] },
      { alias: '', email: ' ', number: '2', error: secret, windows: [window('5h', 0)] },
    ] }, codex: { available: false, accounts: [{ email: secret }] } }, now);
  assert.deepEqual(Object.keys(model), ['status', 'theme', 'providers']);
  assert.equal(model.theme, 'dark');
  assert.equal(JSON.stringify(model).includes(secret), false);
  assert.equal(JSON.stringify(model).includes('not-projected'), false);
  const [account, error] = model.providers[0].accounts;
  assert.deepEqual(account, { name: 'Work account', active: true, excluded: true, pending: true, status: 'ready',
    windows: [{ name: '5h', model: true, remaining: 87.655, reset: 'resets 1h 0m' }] });
  assert.equal(error.name, 'Account 2');
  assert.equal(error.status, 'unavailable');
  assert.deepEqual(error.windows, []);
  assert.deepEqual(model.providers[1].accounts, []);
});

test('popover percentages preserve numeric precision and reject invalid reports without healthy-looking bars', () => {
  const model = quotaModel({ claude: { available: true, accounts: [{ windows: [
    ...[null, NaN, Infinity, -1, 101, '20', undefined].map(value => window('bad', value)),
    window('full', 0), window('empty', 100), window('fractional', 35.1),
  ] }] } }, now);
  assert.deepEqual(model.providers[0].accounts[0].windows.map(window => window.remaining),
    [null, null, null, null, null, null, null, 100, 0, 64.9]);
});

test('popover handles last-good, missing, login, metered, credits and malformed account labels honestly', () => {
  const model = quotaModel({ preferences: { theme: 'SECRET' }, claude: { available: true, accounts: [
    { usageFailed: true, windows: [window('7d', 60)] },
    { usageFailed: true }, { sentinel: 'SECRET error', windows: [window('5h', NaN)] },
    { loginRequired: true, windows: [window('5h', 0)] },
    { onCredits: true, windows: [window('5h', 0)] },
    { sentinel: 'api key', windows: [window('5h', 0)] },
    { number: { token: 'SECRET' }, alias: ' ', email: '' },
    { number: '1&SECRET' }, { number: '3', email: 'e'.repeat(500) },
  ] } }, now);
  const accounts = model.providers[0].accounts;
  assert.deepEqual(accounts.map(account => account.status),
    ['last-good', 'failed', 'failed', 'login', 'credits', 'metered', 'ready', 'ready', 'ready']);
  assert.equal(accounts[0].windows[0].remaining, 40);
  for (const account of accounts.slice(1, 6)) assert.deepEqual(account.windows, []);
  assert.equal(accounts[6].name, 'Saved account');
  assert.equal(accounts[7].name, 'Saved account');
  assert.equal(accounts[8].name.length, 320);
  assert.equal(model.theme, 'system');
  assert.equal(JSON.stringify(model).includes('SECRET'), false);
  assert.deepEqual(quotaModel(null), { status: 'loading', theme: 'system', providers: [] });
  assert.equal(quotaModel(null, now, 'unavailable').status, 'unavailable');
});

test('tray callback retains text items while publishing a sanitized model from the same single cached read', async () => {
  const rendered = [];
  let reads = 0;
  const dashboard = new TrayDashboard((items, model) => rendered.push({ items, model }), async () => {
    reads += 1;
    return { preferences: { theme: 'light' }, claude: { available: true, accounts: [{ email: 'Synthetic', token: 'SECRET' }] } };
  });
  dashboard.start('synthetic');
  await new Promise(resolve => setImmediate(resolve));
  dashboard.stop();
  assert.equal(reads, 1);
  assert.equal(rendered[0].model.status, 'loading');
  assert.ok(rendered[1].items.some(item => item.label?.includes('Synthetic')));
  assert.equal(rendered[1].model.providers[0].accounts[0].name, 'Synthetic');
  assert.equal(rendered[1].model.theme, 'light');
  assert.equal(JSON.stringify(rendered).includes('SECRET'), false);
});
