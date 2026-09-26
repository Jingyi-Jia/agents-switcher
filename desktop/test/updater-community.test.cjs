'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { checkCommunityRelease, validateCommunityRelease, releaseUrl,
  LATEST_RELEASE_API, MAX_RESPONSE_BYTES, REQUEST_TIMEOUT_MS } = require('../src/community-updates.cjs');

const releases = 'https://github.com/Jingyi-Jia/agents-switcher/releases';

function metadata(name = 'Agent-Switch-1.2.0-win-x64.exe') {
  return { tag_name: 'v1.2.0', draft: false, prerelease: false, published_at: '2026-08-01T10:00:00Z',
    html_url: `${releases}/tag/v1.2.0`, body: '<script>PRIVATE RELEASE HTML</script>',
    assets: [{ name, size: 123, state: 'uploaded', browser_download_url: `${releases}/download/v1.2.0/${name}` }] };
}

function transport({ status = 200, headers = {} } = {}) {
  const calls = [];
  const pending = new EventEmitter(), response = new EventEmitter();
  pending.destroy = () => { pending.destroyed = true; };
  response.destroy = () => { response.destroyed = true; response.emit('close'); };
  Object.assign(response, { statusCode: status, complete: false, headers: { 'content-type': 'application/json; charset=utf-8', ...headers } });
  let callback;
  const request = (url, options, cb) => { calls.push({ url, options }); callback = cb; return pending; };
  const respond = () => callback(response);
  const send = (body = JSON.stringify(metadata()), complete = true) => {
    respond();
    for (const chunk of Array.isArray(body) ? body : [body]) response.emit('data', Buffer.from(chunk));
    response.complete = complete;
    response.emit('end');
  };
  return { request, pending, response, calls, respond, send };
}

function check(request, extra = {}) {
  return checkCommunityRelease({ platform: 'win32', arch: 'x64', request, ...extra });
}

for (const [platform, arch, suffix] of [
  ['darwin', 'arm64', 'mac-arm64.dmg'], ['darwin', 'arm64', 'mac-arm64.zip'],
  ['darwin', 'x64', 'mac-x64.dmg'], ['darwin', 'x64', 'mac-x64.zip'], ['win32', 'x64', 'win-x64.exe'],
  ['linux', 'x64', 'linux-x86_64.AppImage'], ['linux', 'x64', 'linux-amd64.deb'], ['linux', 'x64', 'linux-x64.tar.gz'],
]) {
  test(`public stable release accepts the canonical ${suffix} asset`, () => {
    assert.deepEqual(validateCommunityRelease(metadata(`Agent-Switch-1.2.0-${suffix}`), platform, arch),
      { version: '1.2.0', url: `${releases}/tag/v1.2.0` });
  });
}

test('release URLs can only be generated for stable canonical versions', () => {
  assert.equal(releaseUrl('1.2.0'), `${releases}/tag/v1.2.0`);
  for (const version of [null, undefined, 123, 'v1.2.0', '01.2.0', '1.2', '1.2.0-beta.1', '1.2.0/../../../foreign', '<html>', '1'.repeat(64)]) {
    assert.equal(releaseUrl(version), null);
  }
});

test('metadata rejects unpublished, preview, incomplete and noncanonical releases', () => {
  for (const value of [null, undefined, [], 'PRIVATE', {},
    ...[
      { draft: true }, { draft: undefined }, { draft: 'false' }, { prerelease: true }, { prerelease: undefined },
      { tag_name: '1.2.0' }, { tag_name: 'v1.2.0-beta.1' }, { tag_name: 'v01.2.0' }, { tag_name: 'v1.2' }, { tag_name: 'v1.2.0/other' },
      { published_at: null }, { published_at: 'PRIVATE' }, { published_at: '2026-99-01T10:00:00Z' },
      { published_at: '2026-02-30T10:00:00Z' }, { published_at: '2026-08-01T24:00:00Z' },
      { published_at: '2026-08-01' }, { published_at: '2026-08-01T10:00:00' },
      { html_url: undefined }, { html_url: `${releases}/tag/v1.3.0` }, { html_url: `${releases}/latest` },
      ...['http://github.com/Jingyi-Jia/agents-switcher/releases/tag/v1.2.0',
        'https://github.com/Other/agents-switcher/releases/tag/v1.2.0',
        'https://github.com/Jingyi-Jia/other/releases/tag/v1.2.0', 'https://evil.example/release',
        'https://github.com.evil.example/Jingyi-Jia/agents-switcher/releases/tag/v1.2.0',
        'https://PRIVATE@github.com/Jingyi-Jia/agents-switcher/releases/tag/v1.2.0',
        `${releases}/tag/v1.2.0?token=PRIVATE`, `${releases}/tag/v1.2.0#PRIVATE`, `${releases}/tag/v1.2.0/`,
        `${releases}/download/v1.2.0/Agent-Switch-1.2.0-win-x64.exe`].map(html_url => ({ html_url })),
    ].map(change => ({ ...metadata(), ...change })),
  ]) assert.throws(() => validateCommunityRelease(value, 'win32', 'x64'), /Invalid public release/);
});

test('native assets must be uploaded, nonempty and bound to this release and repository', () => {
  const base = metadata();
  for (const assets of [undefined, null, {}, [], [null], ['PRIVATE'], Array(101).fill(base.assets[0]),
    [base.assets[0], base.assets[0]],
    ...[
      { name: undefined }, { name: '../update.exe' }, { name: 'PRIVATE\n.exe' }, { name: 'a'.repeat(256) },
      { state: undefined }, { state: 'new' }, { size: undefined }, { size: 0 }, { size: -1 },
      { size: 1.5 }, { size: Number.MAX_SAFE_INTEGER + 1 }, { size: '123' },
      { browser_download_url: undefined }, { browser_download_url: 'https://evil.example/update.exe' },
      { browser_download_url: `${releases}/download/v1.3.0/${base.assets[0].name}` },
      { browser_download_url: `${base.assets[0].browser_download_url}?token=PRIVATE` },
      { browser_download_url: base.assets[0].browser_download_url.replace('Jingyi-Jia', 'Other') },
      { browser_download_url: base.assets[0].browser_download_url.replace('https:', 'http:') },
    ].map(change => [{ ...base.assets[0], ...change }]),
  ]) assert.throws(() => validateCommunityRelease({ ...base, assets }, 'win32', 'x64'), /Invalid public release assets/);
  assert.throws(() => validateCommunityRelease({ ...base,
    assets: [...base.assets, { name: 'SHA256SUMS.txt', size: 10, state: 'uploaded', browser_download_url: 'https://evil.example/checksums' }] },
  'win32', 'x64'), /Invalid public release assets/);
});

test('release metadata cannot substitute another version, architecture, platform or source archive', () => {
  for (const name of ['Agent-Switch-1.2.0-mac-arm64.dmg', 'Agent-Switch-1.2.0-win-arm64.exe',
    'Agent-Switch-1.2.0-linux-x64.tar.gz', 'Agent-Switch-1.3.0-win-x64.exe',
    'Agent-Switch-1.2.0-win-x64.zip', 'Source-1.2.0.tar.gz', 'latest.yml']) {
    assert.throws(() => validateCommunityRelease(metadata(name), 'win32', 'x64'), /Invalid public release assets/);
  }
  for (const [platform, arch] of [['darwin', 'ia32'], ['win32', 'arm64'], ['linux', 'arm64'], ['freebsd', 'x64']]) {
    assert.throws(() => validateCommunityRelease(metadata(), platform, arch), /Unsupported release platform/);
  }
});

test('checks issue one fixed anonymous HTTPS GET and return only the version and release page', async t => {
  const env = { GH_TOKEN: 'PRIVATE', GITHUB_TOKEN: 'PRIVATE', HTTP_PROXY: 'https://PRIVATE@proxy.invalid',
    HTTPS_PROXY: 'https://PRIVATE@proxy.invalid', NODE_USE_ENV_PROXY: '1' };
  const previous = Object.fromEntries(Object.keys(env).map(name => [name, process.env[name]]));
  Object.assign(process.env, env);
  t.after(() => {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name]; else process.env[name] = value;
    }
  });
  const net = transport(), checking = check(net.request);
  assert.deepEqual(net.calls, [{ url: 'https://api.github.com/repos/Jingyi-Jia/agents-switcher/releases/latest',
    options: { method: 'GET', agent: false, rejectUnauthorized: true, maxHeaderSize: 16384,
      headers: { Accept: 'application/vnd.github+json', 'Accept-Encoding': 'identity',
        'User-Agent': 'agents-switcher-desktop', 'X-GitHub-Api-Version': '2022-11-28' } } }]);
  assert.equal(net.calls[0].url, LATEST_RELEASE_API);
  assert.equal(JSON.stringify(net.calls).includes('PRIVATE'), false);
  net.send();
  assert.deepEqual(await checking, { version: '1.2.0', url: `${releases}/tag/v1.2.0` });
  assert.equal(net.calls.length, 1);
  assert.equal(net.pending.destroyed, true);
});

test('404 means no published release without consuming or exposing its response body', async () => {
  const net = transport({ status: 404, headers: { 'content-type': 'text/html' } });
  const checking = check(net.request);
  net.send('PRIVATE HTML');
  assert.equal(await checking, null);
  assert.equal(net.response.destroyed, true);
});

test('all redirects and HTTP failures are refused without following the Location header', async () => {
  for (const status of [201, 204, 301, 302, 303, 304, 307, 308, 400, 401, 403, 429, 500, 503]) {
    const net = transport({ status, headers: { location: `${releases}/latest?token=PRIVATE` } });
    const checking = check(net.request);
    net.send('PRIVATE HTML');
    await assert.rejects(checking, /^Error: Could not check public releases\.$/);
    assert.equal(net.calls.length, 1);
    assert.equal(net.response.destroyed, true);
  }
});

test('malformed JSON, wrong content types and compressed responses fail safely', async () => {
  for (const body of ['', 'PRIVATE', '{}', 'null', '[]', '<html>PRIVATE</html>', JSON.stringify({ ...metadata(), draft: true })]) {
    const net = transport(), checking = check(net.request);
    net.send(body);
    await assert.rejects(checking, /^Error: Could not check public releases\.$/);
  }
  for (const headers of [{ 'content-type': undefined }, { 'content-type': 'text/html' },
    { 'content-type': 'application/json-invalid' }, { 'content-encoding': 'gzip' },
    { 'content-length': '-1' }, { 'content-length': 'PRIVATE' }, { 'content-length': String(MAX_RESPONSE_BYTES + 1) }]) {
    const net = transport({ headers }), checking = check(net.request);
    net.send();
    await assert.rejects(checking, /^Error: Could not check public releases\.$/);
    assert.equal(net.response.destroyed, true);
  }
});

test('valid JSON is accepted at the byte bound and refused above it without trusting Content-Length', async () => {
  const body = JSON.stringify(metadata());
  const allowed = transport({ headers: { 'content-length': String(MAX_RESPONSE_BYTES) } });
  const checking = check(allowed.request);
  allowed.send([body, ' '.repeat(MAX_RESPONSE_BYTES - Buffer.byteLength(body))]);
  assert.equal((await checking).version, '1.2.0');
  for (const headers of [{}, { 'content-length': '1' }]) {
    const net = transport({ headers }), checking = check(net.request);
    net.send([body, ' '.repeat(MAX_RESPONSE_BYTES - Buffer.byteLength(body)), ' ']);
    await assert.rejects(checking, /^Error: Could not check public releases\.$/);
    assert.equal(net.pending.destroyed, true);
  }
});

test('partial responses, stream failures and transport errors never look like no release', async () => {
  for (const event of ['error', 'aborted', 'close']) {
    const net = transport(), checking = check(net.request);
    net.respond();
    net.response.emit(event, new Error('PRIVATE body'));
    await assert.rejects(checking, /^Error: Could not check public releases\.$/);
  }
  const truncated = transport(), incomplete = check(truncated.request);
  truncated.send(JSON.stringify(metadata()), false);
  await assert.rejects(incomplete, /Could not check public releases/);
  const wrongLength = transport({ headers: { 'content-length': '12345' } }), mismatch = check(wrongLength.request);
  wrongLength.send();
  await assert.rejects(mismatch, /Could not check public releases/);
  const net = transport(), checking = check(net.request);
  net.pending.emit('error', new Error('PRIVATE request diagnostic'));
  await assert.rejects(checking, /^Error: Could not check public releases\.$/);
  await assert.rejects(check(() => { throw new Error('PRIVATE connection setup'); }), /^Error: Could not check public releases\.$/);
});

test('the total request deadline bounds both missing headers and a trickling response', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const respond of [false, true]) {
    const net = transport(), checking = check(net.request);
    if (respond) net.respond();
    t.mock.timers.tick(REQUEST_TIMEOUT_MS - 1);
    assert.equal(net.pending.destroyed, undefined);
    if (respond) net.response.emit('data', Buffer.from('{'));
    t.mock.timers.tick(1);
    await assert.rejects(checking, /^Error: Could not check public releases\.$/);
    assert.equal(net.pending.destroyed, true);
    if (respond) assert.equal(net.response.destroyed, true);
  }
});

test('abort closes pending HTTP work and ignores callbacks arriving after cancellation', async () => {
  for (const respond of [false, true]) {
    const abort = new AbortController(), net = transport();
    const checking = check(net.request, { signal: abort.signal });
    if (respond) net.respond();
    abort.abort();
    await assert.rejects(checking, /^Error: Could not check public releases\.$/);
    assert.equal(net.pending.destroyed, true);
    net.send();
    assert.equal(net.response.destroyed, true);
    net.pending.emit('error', new Error('PRIVATE late error'));
  }
  const abort = new AbortController(), net = transport();
  abort.abort();
  await assert.rejects(check(net.request, { signal: abort.signal }), /Could not check public releases/);
  assert.deepEqual(net.calls, []);
});
