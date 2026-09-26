from tests.test_web_page_actions import node, run_page


UPDATER = r"""
const updateCalls = [];
let updateListener, updateUnsubscribed = false;
let nativeUpdate = {schemaVersion: 1, supported: true, status: 'idle', currentVersion: '1.1.0', availableVersion: null, progress: null, message: 'Check for a newer version.'};
const emitUpdate = patch => { nativeUpdate = {...nativeUpdate, ...patch}; updateListener(nativeUpdate); };
const updateHandlers = {
  check: async () => { emitUpdate({status: 'available', availableVersion: '1.2.0', message: 'An update is available.'}); return nativeUpdate; },
  download: async () => { emitUpdate({status: 'downloaded', message: 'Ready to install.'}); return nativeUpdate; },
  cancel: async () => { emitUpdate({status: 'available', message: 'Download cancelled.'}); return nativeUpdate; },
  install: async () => nativeUpdate,
};
window.agentSwitchUpdater = {
  getState: async () => { updateCalls.push('getState'); return nativeUpdate; },
  onState: callback => { updateListener = callback; return () => { updateUnsubscribed = true; }; },
};
for (const action of ['check', 'download', 'cancel', 'install']) window.agentSwitchUpdater[action] = async (...args) => {
  assert.equal(args.length, 0); updateCalls.push(action); return updateHandlers[action]();
};
"""


def test_browser_dashboard_does_not_offer_a_native_updater(node):
    run_page(node, r"""
await navigate('settings');
assert.equal($('app-updates').hidden, true);
assert.equal($('update-check').disabled, true);
assert(!calls.some(call => call.path.includes('update')));
""")


def test_native_updates_are_explicit_and_separate_from_live_data_polling(node):
    run_page(node, r"""
assert.equal($('app-updates').hidden, false);
assert.match($('update-version').textContent, /Installed version 1.1.0/);
assert.equal($('update-check').disabled, false);
assert.deepEqual(updateCalls, ['getState']);
const releases = nodes('app-updates').find(node => node.tagName === 'A');
assert.equal(releases.href, 'https://github.com/Jingyi-Jia/agents-switcher/releases');
assert.equal(releases.rel, 'noopener noreferrer');
await navigate('settings'); await load(); await settle();
assert.deepEqual(updateCalls, ['getState']);
await $('update-check').click();
assert.equal($('update-download').hidden, false);
assert.equal($('update-install').hidden, true);
assert.match($('update-version').textContent, /Available version 1.2.0/);
assert.deepEqual(updateCalls, ['getState', 'check']);
await $('update-download').click();
assert.equal($('update-install').disabled, false);
assert.equal($('update-check').disabled, true);
assert.equal($('update-download').hidden, true);
assert(!updateCalls.includes('install'));
await $('update-install').click();
assert.equal(updateCalls.at(-1), 'install');
assert.equal($('update-install').disabled, false);
assert.equal(posts().length, 0);
window.listeners.pagehide[0]();
assert.equal(updateUnsubscribed, true);
""", setup=UPDATER)


def test_download_progress_stays_cancellable_while_the_download_promise_is_pending(node):
    run_page(node, r"""
await $('update-check').click();
let finishDownload;
updateHandlers.download = async () => {
  emitUpdate({status: 'downloading', message: 'Downloading update…', progress: {percent: 24.5, transferred: 2450000, total: 10000000}});
  return new Promise(resolve => { finishDownload = resolve; });
};
const downloading = $('update-download').click();
await settle();
assert.equal($('update-check').disabled, true);
assert.equal($('update-cancel').disabled, false);
assert.equal($('update-progress').hidden, false);
assert.equal($('update-progress').value, 24.5);
assert.match($('update-transfer').textContent, /24% downloaded.*2.5 of 10.0 MB/);
await $('update-cancel').click();
finishDownload({schemaVersion: 1, supported: true, status: 'downloaded', message: 'Stale result'});
await downloading;
assert.match($('update-status').textContent, /Download cancelled/);
assert.equal($('update-install').hidden, true);
assert.equal($('update-progress').hidden, true);
assert.equal($('update-download').disabled, false);
assert.equal(posts().length, 0);
""", setup=UPDATER)


def test_unsupported_invalid_and_error_states_never_offer_install(node):
    run_page(node, r"""
emitUpdate({supported: false, status: 'unsupported', message: 'Install a signed release to use app updates.'});
assert.equal($('update-check').disabled, true);
assert.match($('update-status').textContent, /signed release/);
emitUpdate({supported: true, status: 'error', message: 'Download verification failed.'});
assert.equal($('update-install').hidden, true);
assert.equal($('update-check').disabled, false);
assert.match($('update-status').textContent, /verification failed/);
updateListener({schemaVersion: 999, supported: true, status: 'downloaded'});
assert.equal($('update-install').hidden, true);
assert.equal($('update-check').disabled, true);
assert.match($('update-status').textContent, /unavailable/);
""", setup=UPDATER)


def test_bridge_exceptions_are_not_displayed_and_update_requests_respect_account_actions(node):
    run_page(node, r"""
busy = true; syncBusy();
await $('update-check').click();
assert.deepEqual(updateCalls, ['getState']);
busy = false; syncBusy();
updateHandlers.check = async () => { throw new Error('sensitive-path-and-token'); };
await $('update-check').click();
assert.match($('update-status').textContent, /couldn't be reached/);
assert.doesNotMatch($('app-updates').textContent, /sensitive-path-and-token/);
assert.equal($('update-install').hidden, true);
""", setup=UPDATER)


def test_initial_state_cannot_overwrite_a_more_recent_native_event(node):
    run_page(node, r"""
emitUpdate({status: 'downloaded', availableVersion: '1.2.0', message: 'Ready to install.'});
resolveInitial({schemaVersion: 1, supported: true, status: 'idle', message: 'Stale initial state'});
await settle();
assert.equal($('update-install').disabled, false);
assert.match($('update-status').textContent, /Ready to install/);
""", setup=UPDATER + r"""
let resolveInitial;
window.agentSwitchUpdater.getState = () => new Promise(resolve => { resolveInitial = resolve; });
""")


def test_partial_bridge_is_unavailable_without_breaking_settings(node):
    run_page(node, r"""
await navigate('settings');
assert.equal($('app-updates').hidden, false);
assert.equal($('update-check').disabled, true);
assert.match($('update-status').textContent, /unavailable/);
assert.equal($('view-settings').hidden, false);
""", setup="window.agentSwitchUpdater = {getState: async () => ({})};")
