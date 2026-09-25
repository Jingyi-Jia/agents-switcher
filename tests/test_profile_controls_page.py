from __future__ import annotations

import pytest

from claude_swap.web.page import PAGE_HTML
from tests.test_web_page_actions import node, run_page


PROFILES = r"""
apiState.preferences = {theme: 'system', profileNoticeVersion: 1};
apiState.claudeDesktop = {available: true, canCreate: true, canManage: true, canDelete: true,
  supported: true, installed: true, running: false,
  profiles: [{id: 'a'.repeat(32), name: 'Work', emailLabel: 'work@example.test'}]};
await load();
const profileId = 'a'.repeat(32);
const details = () => nodes('claude-desktop-profiles').find(n => n.dataset.profileFocus === 'details:' + profileId);
"""


def test_desktop_actions_and_profile_creation_are_in_the_requested_places(node):
    run_page(node, PROFILES + r"""
assert.match($('claude-desktop-heading').textContent, /Claude Desktop.*Profiles.*Beta/);
assert.deepEqual(nodes('claude-desktop-actions').filter(n => n.tagName === 'BUTTON').map(n => n.textContent), ['Open Claude', 'Refresh']);
assert.equal($('claude-desktop-profiles').children.at(-1), button('claude-desktop-profiles', 'New profile'));
assert.match(details().textContent, /Work.*Email label.*work@example.test/);
await button('claude-desktop-actions', 'Open Claude').click();
assert.equal(posts().length, 0);
assert.match($('dialog-description').textContent, /usual default profile/);
await submitDialog();
assert.deepEqual(posts().at(-1).payload, {profileId: 'default', confirm: true});
""")


def test_profile_details_save_labels_without_claiming_a_verified_login(node):
    run_page(node, PROFILES + r"""
details().click();
assert.equal($('dialog-title').textContent, 'Profile details');
assert.equal(document.activeElement, $('dialog-cancel'));
assert.match($('dialog-fields').textContent, /entered by you.*does not verify/);
const name = nodes('dialog-fields').find(n => n.type === 'text');
const email = nodes('dialog-fields').find(n => n.type === 'email');
assert.equal(name.value, 'Work');
assert.equal(email.value, 'work@example.test');
name.value = 'Renamed work'; email.value = '';
fetchHandler = async path => {
  if (path === '/api/claude-desktop/update') apiState.claudeDesktop.profiles[0] = {id: profileId, name: 'Renamed work', emailLabel: ''};
  return {ok: true, json: async () => path.startsWith('/api/state') ? apiState : response};
};
await submitDialog(); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/update']);
assert.deepEqual(posts()[0].payload, {profileId, name: 'Renamed work', emailLabel: '', confirm: true});
assert.equal($('action-dialog').open, false);
assert.equal(document.activeElement, details());
assert.match(details().textContent, /Renamed work.*Profile settings/);
""")


def test_creation_passes_an_optional_email_label_and_cancel_does_nothing(node):
    run_page(node, PROFILES + r"""
button('claude-desktop-profiles', 'New profile').click();
nodes('dialog-fields').find(n => n.type === 'text').value = 'Lab';
nodes('dialog-fields').find(n => n.type === 'email').value = 'lab@example.test';
assert.equal(posts().length, 0);
$('dialog-cancel').click(); await settle();
assert.equal(document.activeElement, button('claude-desktop-profiles', 'New profile'));
button('claude-desktop-profiles', 'New profile').click();
nodes('dialog-fields').find(n => n.type === 'text').value = ' Lab ';
nodes('dialog-fields').find(n => n.type === 'email').value = 'lab@example.test';
await submitDialog();
assert.deepEqual(posts().at(-1).payload, {name: 'Lab', emailLabel: 'lab@example.test', confirm: true});
assert.equal(posts().at(-1).path, '/api/claude-desktop/create');
""")


def test_deletion_requires_its_own_confirmation_and_restores_focus(node):
    run_page(node, PROFILES + r"""
details().click();
button('dialog-fields', 'Delete profile…').click();
assert.match($('dialog-description').textContent, /local sign-in and session data.*does not delete your Claude account.*cannot be undone/);
assert.match($('dialog-submit').className, /danger/);
await submitDialog();
assert.equal(posts().length, 0);
$('dialog-cancel').click(); await settle();
assert.equal(document.activeElement, details());
details().click(); button('dialog-fields', 'Delete profile…').click();
nodes('dialog-fields').find(n => n.type === 'checkbox').checked = true;
fetchHandler = async path => {
  if (path === '/api/claude-desktop/delete') apiState.claudeDesktop.profiles = [];
  return {ok: true, json: async () => path.startsWith('/api/state') ? apiState : response};
};
await submitDialog(); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/delete']);
assert.deepEqual(posts()[0].payload, {profileId, confirm: true});
assert.equal($('action-dialog').open, false);
assert.equal(document.activeElement, button('claude-desktop-profiles', 'New profile'));
""")


@pytest.mark.parametrize("mode", ["running", "unknown", "removed"])
def test_a_later_readiness_change_blocks_an_open_delete_confirmation(node, mode):
    run_page(node, PROFILES + "const mode = '" + mode + "';" + r"""
details().click(); button('dialog-fields', 'Delete profile…').click();
nodes('dialog-fields').find(n => n.type === 'checkbox').checked = true;
if (mode === 'removed') apiState.claudeDesktop.profiles = [];
else {
  apiState.claudeDesktop.running = mode === 'running' ? true : null;
  apiState.claudeDesktop.canDelete = false;
  apiState.claudeDesktop.deleteError = 'Confirm Claude is quit before deleting.';
}
await load();
assert.equal($('dialog-submit').disabled, true);
assert.equal($('dialog-feedback').hidden, false);
assert.match($('dialog-feedback').textContent, mode === 'removed' ? /no longer in the saved list/ : /Confirm Claude is quit/);
await submitDialog();
assert.equal(posts().length, 0);
if (mode !== 'removed') {
  apiState.claudeDesktop.running = false;
  apiState.claudeDesktop.canDelete = true;
  await load();
  assert.equal($('dialog-submit').disabled, false);
  assert.equal($('dialog-feedback').hidden, true);
}
$('dialog-cancel').click(); await settle();
button('claude-desktop-profiles', 'New profile').click();
assert.equal($('dialog-submit').disabled, false);
assert.equal($('dialog-submit').title, '');
assert.doesNotMatch($('dialog-submit').className, /danger/);
""")


def test_incomplete_cleanup_is_visible_and_cannot_be_retried_against_a_removed_profile(node):
    run_page(node, PROFILES + r"""
details().click(); button('dialog-fields', 'Delete profile…').click();
nodes('dialog-fields').find(n => n.type === 'checkbox').checked = true;
fetchHandler = async path => {
  if (path === '/api/claude-desktop/delete') apiState.claudeDesktop.profiles = [];
  return {ok: true, json: async () => path.startsWith('/api/state') ? apiState : {ok: false, warning: true, message: 'Cleanup failed; local profile data may remain. This was not a complete deletion.'}};
};
await submitDialog();
assert.equal($('action-dialog').open, true);
assert.equal($('dialog-submit').disabled, true);
assert.equal($('dialog-cancel').disabled, false);
assert.equal($('dialog-feedback').hidden, false);
assert.match($('dialog-feedback').textContent, /not a complete deletion/);
assert.match($('toast').className, /ember/);
assert.match($('toast').textContent, /local profile data may remain/);
await load();
assert.match($('dialog-feedback').textContent, /not a complete deletion/);
await submitDialog();
assert.equal(posts().length, 1);
$('dialog-cancel').click(); await settle();
assert.equal(document.activeElement, button('claude-desktop-profiles', 'New profile'));
""")


def test_profile_save_locks_the_dialog_and_preserves_failure_feedback(node):
    run_page(node, PROFILES + r"""
details().click();
let finish;
fetchHandler = async path => path === '/api/claude-desktop/update' ? new Promise(resolve => {finish = resolve;}) : {ok: true, json: async () => apiState};
const pending = submitDialog(); await settle();
assert.equal($('dialog-submit').disabled, true);
assert.equal($('dialog-cancel').disabled, true);
assert.equal(button('dialog-fields', 'Delete profile…').disabled, true);
$('action-dialog').dispatch('cancel');
assert.equal($('action-dialog').open, true);
await submitDialog();
assert.equal(posts().length, 1);
finish({ok: false, json: async () => ({ok: false, message: 'The registry could not be saved.'})});
await pending;
assert.equal($('action-dialog').open, true);
assert.equal($('dialog-submit').disabled, false);
assert.match($('dialog-feedback').textContent, /registry could not be saved/);
""")


def test_profile_focus_survives_refreshed_labels_and_markup_is_never_interpreted(node):
    run_page(node, PROFILES + r"""
details().focus();
apiState.claudeDesktop.profiles[0].name = '<img src=x onerror=alert(1)>';
await load();
assert.equal(document.activeElement, details());
assert.match(details().textContent, /<img src=x onerror=alert\(1\)>/);
assert.equal(nodes('claude-desktop-profiles').filter(n => n.tagName === 'IMG').length, 0);
""")
    assert ".card.is-active::before" not in PAGE_HTML
    assert "0 0 0 1px var(--active-border)" in PAGE_HTML
    assert ".card { border:1px solid var(--hairline); border-radius:10px" in PAGE_HTML


def test_start_stop_and_preview_use_clear_separate_actions(node):
    run_page(node, r"""
const ui = providerUI.codex;
assert.equal(ui.modes.live.textContent, 'Start');
assert.equal(ui.modes.stopped.textContent, 'Stop');
assert.equal(ui.modes.stopped.disabled, true);
assert.equal(ui.modes['dry-run'].textContent, 'Start preview');
assert.match($('codex-auto').textContent, /Preview without switching.*Usage checks still run and may refresh credentials/);
assert.match($('codex-auto').textContent, /most-used quota window/);
assert.doesNotMatch($('codex-auto').textContent, /session quota/);
assert.doesNotMatch($('codex-auto').textContent, /Apply threshold|Dry run|Start live/);
ui.threshold.value = '94'; ui.threshold.oninput();
ui.modes.live.click();
assert.equal(posts().length, 0);
assert.match($('dialog-description').textContent, /change the active CLI account automatically.*94%/);
$('dialog-cancel').click(); await settle();
ui.modes['dry-run'].click(); await settle();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', mode: 'dry-run', threshold: 94});
apiState.codex.auto = {mode: 'live', threshold: 94, events: []};
await load();
assert.equal(ui.status.textContent, 'Running');
assert.equal(ui.modes.live.disabled, true);
ui.threshold.value = '95'; ui.threshold.oninput();
assert.equal(ui.modes.live.textContent, 'Save threshold');
ui.modes.live.click(); await submitDialog(); await settle();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', mode: 'live', threshold: 95, confirm: true});
ui.threshold.value = ''; ui.threshold.oninput();
ui.modes.stopped.click(); await settle();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', mode: 'stopped'});
""")
