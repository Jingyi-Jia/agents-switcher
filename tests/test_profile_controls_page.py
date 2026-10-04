from __future__ import annotations

import pytest

from agents_switcher.web.page import PAGE_HTML
from tests.test_web_page_actions import node, run_page


PROFILES = r"""
apiState.preferences = {theme: 'system', profileNoticeVersion: 1};
apiState.claudeDesktop = {available: true, canCreate: true, canManage: true,
  supported: true, installed: true, running: false, removedProfiles: [],
  profiles: [{id: 'a'.repeat(32), name: 'Work', emailLabel: 'work@example.test'}]};
await load();
const profileId = 'a'.repeat(32);
const details = () => nodes('claude-desktop-profiles').find(n => n.dataset.profileFocus === 'details:' + profileId);
"""


def test_desktop_actions_and_profile_creation_are_in_the_requested_places(node):
    run_page(node, PROFILES + r"""
assert.match($('claude-desktop-heading').textContent, /Claude Desktop.*Profiles/);
assert.doesNotMatch($('claude-desktop-heading').textContent, /Beta/);
assert.deepEqual(nodes('claude-desktop-actions').filter(n => n.tagName === 'BUTTON').map(n => n.textContent), ['Open Claude', 'Refresh']);
assert.equal($('claude-desktop-profiles').children.at(-1), button('claude-desktop-profiles', 'New profile'));
assert.match(details().textContent, /Work.*Email label.*work@example.test/);
await button('claude-desktop-actions', 'Open Claude').click();
assert.equal(posts().length, 1);
assert.notEqual($('action-dialog').open, true);
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


REMOVED = r"""
apiState.claudeDesktop.removedProfiles = [
  {id: 'b'.repeat(32), name: 'Old lab', emailLabel: 'Lab@Example.test', removedAt: '2026-10-01T10:00:00Z'},
  {id: 'c'.repeat(32), name: 'Older lab', emailLabel: 'lab@example.test', removedAt: '2026-09-01T10:00:00Z'},
  {id: 'd'.repeat(32), name: 'Personal', emailLabel: '', removedAt: '2026-08-01T10:00:00Z'}];
await load();
const startCreate = (name, email) => {
  button('claude-desktop-profiles', 'New profile').click();
  nodes('dialog-fields').find(n => n.type === 'text').value = name;
  nodes('dialog-fields').find(n => n.type === 'email').value = email;
  return submitDialog();
};
"""


def test_remove_takes_effect_at_once_and_undo_restores_the_same_labels(node):
    run_page(node, PROFILES + r"""
apiState.claudeDesktop.running = true;
await load();
details().click();
assert.match($('dialog-fields').textContent, /without deleting anything.*stay on this computer.*same email label/);
const remove = button('dialog-fields', 'Remove profile');
assert.equal(remove.disabled, false);
assert.doesNotMatch(remove.className, /danger/);
const record = {id: profileId, name: 'Work', emailLabel: 'work@example.test', removedAt: '2026-10-03T21:00:00Z'};
fetchHandler = async path => {
  if (path === '/api/claude-desktop/remove') {
    apiState.claudeDesktop.profiles = []; apiState.claudeDesktop.removedProfiles = [record];
  }
  if (path === '/api/claude-desktop/restore') {
    apiState.claudeDesktop.profiles = [{id: profileId, name: 'Work', emailLabel: 'work@example.test'}];
    apiState.claudeDesktop.removedProfiles = [];
  }
  return {ok: true, json: async () => path.startsWith('/api/state') ? apiState : response};
};
await remove.click(); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/remove']);
assert.deepEqual(posts()[0].payload, {profileId, confirm: true});
assert.equal($('action-dialog').open, false);
assert.equal(details(), undefined);
assert.equal(document.activeElement, button('claude-desktop-profiles', 'New profile'));
assert.match($('toast').className, /show/);
assert.match($('toast').textContent, /Removed Work.*stays on this computer/);
await button('toast', 'Undo').click(); await settle();
assert.equal(posts().at(-1).path, '/api/claude-desktop/restore');
assert.deepEqual(posts().at(-1).payload, {profileId, name: 'Work', emailLabel: 'work@example.test', confirm: true});
assert.match($('toast').textContent, /Restored Work/);
assert.equal(button('toast', 'Undo'), undefined);
assert.ok(details());
""")


def test_a_failed_removal_keeps_the_dialog_open_without_undo(node):
    run_page(node, PROFILES + r"""
details().click();
fetchHandler = async path => ({
  ok: path !== '/api/claude-desktop/remove',
  json: async () => path.startsWith('/api/state') ? apiState : {ok: false, message: 'The profile registry could not be saved. Nothing was removed.'},
});
await button('dialog-fields', 'Remove profile').click(); await settle();
assert.equal($('action-dialog').open, true);
assert.equal($('dialog-feedback').hidden, false);
assert.match($('dialog-feedback').textContent, /Nothing was removed/);
assert.match($('toast').className, /ember/);
assert.equal(button('toast', 'Undo'), undefined);
assert.ok(details());
""")


def test_adding_a_removed_email_offers_its_history_by_default(node):
    run_page(node, PROFILES + REMOVED + r"""
await startCreate('Lab', ' lab@example.test '); await settle();
assert.equal(posts().length, 0);
assert.equal($('action-dialog').open, true);
assert.match($('dialog-title').textContent, /Old lab/);
assert.match($('dialog-description').textContent, /same email label.*2026.*as Lab/);
assert.equal($('dialog-submit').textContent, 'Restore history');
assert.doesNotMatch($('dialog-submit').className, /danger/);
assert.equal(document.activeElement, $('dialog-submit'));
fetchHandler = async path => {
  if (path === '/api/claude-desktop/restore') {
    apiState.claudeDesktop.profiles.push({id: 'b'.repeat(32), name: 'Lab', emailLabel: 'lab@example.test'});
  }
  return {ok: true, json: async () => path.startsWith('/api/state') ? apiState : response};
};
await submitDialog(); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/restore']);
assert.deepEqual(posts()[0].payload, {profileId: 'b'.repeat(32), name: 'Lab', emailLabel: 'lab@example.test', confirm: true});
assert.equal($('action-dialog').open, false);
assert.match($('toast').textContent, /Restored Lab/);
""")


def test_start_fresh_creates_an_empty_profile_and_cancel_sends_nothing(node):
    run_page(node, PROFILES + REMOVED + r"""
await startCreate('Lab', 'lab@example.test'); await settle();
assert.match($('dialog-fields').textContent, /keeps the removed profile/);
$('dialog-cancel').click(); await settle();
assert.equal(posts().length, 0);
assert.equal(document.activeElement, button('claude-desktop-profiles', 'New profile'));
await startCreate('Lab', 'lab@example.test'); await settle();
await button('dialog-fields', 'Start fresh').click(); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/create']);
assert.deepEqual(posts()[0].payload, {name: 'Lab', emailLabel: 'lab@example.test', confirm: true});
assert.equal($('action-dialog').open, false);
assert.match($('toast').textContent, /Profile created/);
""")


def test_profiles_removed_without_an_email_match_by_name_and_others_only_by_email(node):
    run_page(node, PROFILES + REMOVED + r"""
await startCreate('PERSONAL', ''); await settle();
assert.equal(posts().length, 0);
assert.match($('dialog-title').textContent, /Personal/);
assert.match($('dialog-description').textContent, /same name/);
$('dialog-cancel').click(); await settle();
await startCreate('Old lab', ''); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/create']);
assert.deepEqual(posts()[0].payload, {name: 'Old lab', confirm: true});
""")


def test_a_name_already_in_use_skips_the_restore_prompt(node):
    run_page(node, PROFILES + REMOVED + r"""
apiState.claudeDesktop.removedProfiles.push({id: 'e'.repeat(32), name: 'Work', emailLabel: '', removedAt: '2026-07-01T10:00:00Z'});
await load();
await startCreate('work', ''); await settle();
await startCreate('Work', 'lab@example.test'); await settle();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/create', '/api/claude-desktop/create']);
assert.doesNotMatch($('dialog-title').textContent, /Restore/);
""")


def test_remove_explains_when_the_profile_is_already_gone(node):
    run_page(node, PROFILES + r"""
details().click();
const remove = button('dialog-fields', 'Remove profile');
apiState.claudeDesktop.profiles = [];
await load();
await remove.click(); await settle();
assert.equal(posts().length, 0);
assert.equal($('action-dialog').open, true);
assert.equal($('dialog-feedback').hidden, false);
assert.match($('dialog-feedback').textContent, /no longer in the saved list/);
""")


def test_an_unreadable_removed_list_is_reported_in_the_panel(node):
    run_page(node, PROFILES + r"""
apiState.claudeDesktop.removedError = 'The removed Claude Desktop profile list is invalid. It has not been overwritten.';
await load();
assert.equal($('claude-desktop-status').hidden, false);
assert.match($('claude-desktop-status').textContent, /removed Claude Desktop profile list is invalid/);
assert.doesNotMatch($('claude-desktop-status').className, /launch-blocked/);
""")


def test_profile_save_locks_the_dialog_and_preserves_failure_feedback(node):
    run_page(node, PROFILES + r"""
details().click();
let finish;
fetchHandler = async path => path === '/api/claude-desktop/update' ? new Promise(resolve => {finish = resolve;}) : {ok: true, json: async () => apiState};
const pending = submitDialog(); await settle();
assert.equal($('dialog-submit').disabled, true);
assert.equal($('dialog-cancel').disabled, true);
assert.equal(button('dialog-fields', 'Remove profile').disabled, true);
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
assert.match($('dialog-description').textContent, /change the active Codex account automatically.*94%/);
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
