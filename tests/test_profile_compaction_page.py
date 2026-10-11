from __future__ import annotations

import pytest

from tests.test_profile_controls_page import PROFILES
from tests.test_web_page_actions import node, run_page


COMPACTION = PROFILES + r"""
apiState.claudeDesktop.canCompact = true;
await load();
details().click();
const compact = button('dialog-fields', 'Compact VM disk');
const consent = nodes('dialog-fields').find(n => n.type === 'checkbox');
const name = nodes('dialog-fields').find(n => n.type === 'text');
const email = nodes('dialog-fields').find(n => n.type === 'email');
const result = nodes('dialog-fields').find(n => n.attributes.role === 'status');
const readiness = $('profile-compact-readiness');
"""


def test_compaction_is_capability_gated_and_always_requires_its_own_consent(node):
    run_page(node, PROFILES + r"""
for (const capability of [undefined, false, 'true', 1]) {
  apiState.claudeDesktop.canCompact = capability;
  await load(); details().click();
  assert.equal(button('dialog-fields', 'Compact VM disk'), undefined);
  $('dialog-cancel').click(); await settle();
}
""")
    run_page(node, COMPACTION + r"""
assert.equal(consent.checked, false);
assert.notEqual(consent.required, true);
assert.match($('dialog-fields').textContent, /Storage.*preserving its bytes.*does not delete the VM.*Savings are not guaranteed/);
assert.match($('dialog-fields').textContent, /Claude is fully quit and I will keep it quit until compaction finishes/);
assert.equal(compact.attributes['aria-describedby'], readiness.id);
await compact.click();
assert.equal(posts().length, 0);
assert.equal(document.activeElement, consent);
assert.match(result.textContent, /Confirm that Claude is fully quit/);
assert.equal($('action-dialog').open, true);
name.value = 'Unsaved work'; email.value = '';
await submitDialog();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/update']);
assert.deepEqual(posts()[0].payload, {profileId, name: 'Unsaved work', emailLabel: '', confirm: true});
""")


def test_success_preserves_unsaved_labels_and_keeps_the_precise_result_visible(node):
    run_page(node, COMPACTION + r"""
name.value = 'A'.repeat(64); email.value = 'long+' + 'e'.repeat(250) + '@example.test';
response = {ok: true, storage: {logicalBytes: 10000, beforeBytes: 8000, afterBytes: 5000, reclaimedBytes: 3000}, message: 'Compaction verified. No data was deleted.'};
consent.checked = true;
await compact.click(); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/compact']);
assert.deepEqual(posts()[0].payload, {profileId, confirm: true});
assert.equal(posts()[0].options.headers['X-Auth-Token'], TOKEN);
assert.equal($('action-dialog').open, true);
assert.equal(name.value, 'A'.repeat(64));
assert.equal(email.value, 'long+' + 'e'.repeat(250) + '@example.test');
assert.equal(consent.checked, false);
assert.equal(document.activeElement, compact);
assert.equal(result.hidden, false);
assert.match(result.textContent, /Compaction verified.*Allocated bytes: 8,000 → 5,000.*Reclaimed: 3,000 bytes.*Logical size unchanged: 10,000 bytes/);
assert.equal(preferences.profileNoticeVersion, 1);
assert.equal(apiState.claudeDesktop.profiles[0].name, 'Work');
await compact.click();
assert.equal(posts().length, 1);
$('dialog-cancel').click(); await settle(); details().click();
assert.equal(nodes('dialog-fields').find(n => n.type === 'checkbox').checked, false);
""")


@pytest.mark.parametrize("change,reason", [
    ("apiState.claudeDesktop.running = true", "Fully quit Claude Desktop"),
    ("apiState.claudeDesktop.running = null", "process status is unknown"),
    ("delete apiState.claudeDesktop.running", "process status is unknown"),
    ("apiState.claudeDesktop.canManage = false", "readable saved profile registry"),
    ("apiState.claudeDesktop.profiles = []", "no longer in the saved list"),
])
def test_compaction_tracks_current_readiness_without_losing_edits(node, change, reason):
    run_page(node, COMPACTION + f"""
name.value = 'Do not lose this'; consent.checked = true;
{change}; await load();
assert.equal(compact.disabled, true);
assert.match(readiness.textContent, /{reason}/);
await compact.click();
assert.equal(posts().length, 0);
assert.equal(name.value, 'Do not lose this');
assert.equal(button('dialog-fields', 'Check readiness').disabled, false);
""")


@pytest.mark.parametrize("change", [
    "apiState.claudeDesktop.running = true",
    "apiState.claudeDesktop.running = null",
    "apiState.claudeDesktop.profiles = []",
    "apiState.claudeDesktop.canManage = false",
    "apiState.claudeDesktop.canCompact = false",
])
def test_compaction_rechecks_stale_readiness_before_dispatch(node, change):
    run_page(node, COMPACTION + f"""
consent.checked = true;
{change};
await compact.click();
assert.equal(posts().length, 0);
assert.match(result.textContent, /No compaction request was sent/);
assert.equal(consent.checked, false);
assert.equal($('action-dialog').open, true);
if (apiState.claudeDesktop.canCompact === false) assert.equal(document.activeElement, $('dialog-cancel'));
""")


def test_compaction_locks_the_existing_dialog_and_blocks_duplicate_requests(node):
    run_page(node, COMPACTION + r"""
let finish;
fetchHandler = async path => path === '/api/claude-desktop/compact'
  ? new Promise(resolve => {finish = resolve;})
  : {ok: true, json: async () => path === '/api/claude-desktop/status' ? apiState.claudeDesktop : apiState};
name.value = 'Pending labels'; consent.checked = true;
const pending = compact.click(); await settle();
assert.equal(compact.disabled, true);
assert.equal(name.disabled, true);
assert.equal(consent.disabled, true);
assert.equal($('dialog-submit').disabled, true);
assert.equal($('dialog-cancel').disabled, true);
assert.equal(button('dialog-fields', 'Remove profile').disabled, true);
assert.equal(button('dialog-fields', 'Check readiness').disabled, true);
assert.match(result.textContent, /verifying its bytes.*Keep Claude fully quit/);
$('action-dialog').dispatch('cancel');
assert.equal($('action-dialog').open, true);
await compact.click(); await compactDesktopProfile(desktopCompactUI); await submitDialog();
assert.equal(posts().length, 1);
finish({ok: false, json: async () => ({ok: false, message: 'Claude started running. Quit Claude and retry.'})});
await pending;
assert.equal($('action-dialog').open, true);
assert.equal(name.value, 'Pending labels');
assert.equal(name.disabled, false);
assert.equal(consent.checked, false);
assert.match(result.textContent, /Claude started running/);
assert.equal(document.activeElement, compact);
""")


def test_busy_switch_and_enrollment_block_compaction_and_cancel_sends_nothing(node):
    run_page(node, COMPACTION + r"""
for (const flow of ['busy', 'switch', 'enrollment']) {
  busy = flow === 'busy'; codexFlow = flow === 'switch' ? {} : null;
  codexEnrollmentFlow = flow === 'enrollment' ? {} : null;
  syncBusy();
  assert.equal(compact.disabled, true);
  assert.match(readiness.textContent, /current action, switch or sign-in/);
  await compactDesktopProfile(desktopCompactUI);
  assert.equal(posts().length, 0);
}
busy = false; codexFlow = null; codexEnrollmentFlow = null; syncBusy();
consent.checked = true;
$('dialog-cancel').click(); await settle();
assert.equal(posts().length, 0);
assert.equal(document.activeElement, details());
""")


def test_refresh_failure_does_not_obscure_a_successful_compaction(node):
    run_page(node, COMPACTION + r"""
let compacted = false;
fetchHandler = async path => {
  if (path === '/api/claude-desktop/compact') {
    compacted = true;
    return {ok: true, json: async () => ({ok: true, message: 'Verified: reclaimed 0 bytes. The VM disk is unchanged.', storage: {logicalBytes: 4096, beforeBytes: 4096, afterBytes: 4096, reclaimedBytes: 0}})};
  }
  if (compacted) throw new Error('Synthetic refresh failure');
  return {ok: true, json: async () => path === '/api/claude-desktop/status' ? apiState.claudeDesktop : apiState};
};
consent.checked = true; name.value = 'Unsaved';
await compact.click();
assert.match(result.textContent, /Verified: reclaimed 0 bytes.*VM disk is unchanged.*Reclaimed: 0 bytes.*This result still stands.*readiness could not be refreshed/);
assert.equal(name.value, 'Unsaved');
assert.equal(consent.checked, false);
assert.equal($('action-dialog').open, true);
""")


def test_result_stays_visible_when_the_registry_becomes_unreadable_after_success(node):
    run_page(node, COMPACTION + r"""
fetchHandler = async path => {
  if (path === '/api/claude-desktop/compact') {
    apiState.claudeDesktop.canCompact = false;
    return {ok: true, json: async () => ({ok: true, message: 'Compaction verified; reclaimed 0 bytes.'})};
  }
  return {ok: true, json: async () => path === '/api/claude-desktop/status' ? apiState.claudeDesktop : apiState};
};
consent.checked = true;
await compact.click();
assert.equal(desktopCompactUI.section.hidden, true);
assert.equal(result.hidden, false);
assert.match(result.textContent, /Compaction verified; reclaimed 0 bytes/);
assert.equal(document.activeElement, $('dialog-cancel'));
""")


def test_unconfirmed_network_result_is_not_presented_as_success_or_deletion(node):
    run_page(node, COMPACTION + r"""
fetchHandler = async path => {
  if (path === '/api/claude-desktop/compact') throw new Error('Synthetic disconnect');
  return {ok: true, json: async () => path === '/api/claude-desktop/status' ? apiState.claudeDesktop : apiState};
};
consent.checked = true; email.value = 'unsaved@example.test';
await compact.click();
assert.match(result.textContent, /result could not be confirmed.*may still be running.*keep Claude quit/);
assert.doesNotMatch(result.textContent, /deleted|completed|Nothing was/);
assert.equal(email.value, 'unsaved@example.test');
assert.equal(consent.checked, false);
assert.equal($('action-dialog').open, true);
""")


def test_failed_readiness_fetch_sends_no_request_and_result_markup_is_text(node):
    run_page(node, COMPACTION + r"""
fetchHandler = async () => {throw new Error('Synthetic readiness failure');};
consent.checked = true;
await compact.click();
assert.equal(posts().length, 0);
assert.match(result.textContent, /No compaction request was sent/);
assert.equal(consent.checked, false);
fetchHandler = async path => ({ok: true, json: async () => path === '/api/claude-desktop/status' ? apiState.claudeDesktop : path.startsWith('/api/state') ? apiState : {ok: true, message: '<img src=x onerror=alert(1)>', storage: {logicalBytes: -1}}});
await button('dialog-fields', 'Check readiness').click();
assert.equal(state.claudeDesktop.running, false);
consent.checked = true;
await compact.click();
assert.match(result.textContent, /<img src=x onerror=alert\(1\)>/);
assert.equal(nodes('dialog-fields').filter(n => n.tagName === 'IMG').length, 0);
assert.doesNotMatch(result.textContent, /Allocated bytes|NaN/);
""")


def test_storage_readiness_and_post_action_refresh_do_not_collect_provider_quotas(node):
    run_page(node, COMPACTION + r"""
calls.length = 0;
await button('dialog-fields', 'Check readiness').click();
assert.deepEqual(calls.map(c => c.path), ['/api/claude-desktop/status']);
calls.length = 0;
consent.checked = true;
await compact.click();
assert.deepEqual(calls.map(c => c.path), ['/api/claude-desktop/status', '/api/claude-desktop/compact', '/api/claude-desktop/status']);
assert(calls.every(c => c.options.headers['X-Auth-Token'] === TOKEN));
assert.equal(posts().length, 1);
""")
