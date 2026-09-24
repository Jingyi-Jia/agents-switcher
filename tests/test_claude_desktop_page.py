"""Claude Desktop profile controls exercised against the dashboard's DOM harness."""

from tests.test_web_page_actions import node, run_page


def test_codex_comes_first_and_claude_panels_stay_together(node):
    run_page(node, r"""
await load();
assert.match($('kpis').children[0].textContent, /^Codex · active/);
assert.match($('kpis').children[1].textContent, /^Claude Code · active/);
const group = $('claude-group');
const claudePanels = group.children.filter(node => node.tagName !== '#TEXT');
const columns = group.parent.children.filter(node => node.tagName !== '#TEXT');
assert.equal(claudePanels[0].attributes['aria-labelledby'], 'claude-heading');
assert.equal(claudePanels[1].id, 'claude-desktop-panel');
assert.equal(columns[0].attributes['aria-labelledby'], 'codex-heading');
assert.equal(columns[1].id, 'claude-group');
""")


def test_creation_remains_available_when_launch_checks_fail(node):
    run_page(node, r"""
for (const installed of [false, true]) {
  apiState.claudeDesktop = {available: false, canCreate: true, supported: true, installed,
    running: null, profiles: [], error: installed ? 'Unable to check whether Claude is running' : 'Install official Claude in /Applications/Claude.app'};
  await load();
  const create = button('claude-desktop-actions', 'Create empty profile');
  assert.equal(create.disabled, false);
  assert.equal(button('claude-desktop-actions', 'Open usual Claude (default)').disabled, true);
  assert.match($('claude-desktop-status').textContent, /You can still create empty profiles/);
  create.click();
  nodes('dialog-fields').find(node => node.type === 'text').value = 'Work';
  nodes('dialog-fields').find(node => node.type === 'checkbox').checked = true;
  await submitDialog();
  assert.equal(posts().at(-1).path, '/api/claude-desktop/create');
  assert.deepEqual(posts().at(-1).payload, {name: 'Work', confirm: true});
  assert.equal($('action-dialog').open, false);
}
assert.equal(posts().length, 2);
""")


def test_explicit_creation_capability_is_rechecked_before_submission(node):
    run_page(node, r"""
apiState.claudeDesktop = {available: true, canCreate: true, supported: true, installed: true,
  running: false, profiles: []};
await load();
button('claude-desktop-actions', 'Create empty profile').click();
nodes('dialog-fields').find(node => node.type === 'text').value = 'Work';
nodes('dialog-fields').find(node => node.type === 'checkbox').checked = true;
apiState.claudeDesktop.canCreate = false;
await load();
assert.equal(button('claude-desktop-actions', 'Create empty profile').disabled, true);
await submitDialog();
assert.equal(posts().length, 0);
assert.equal($('action-dialog').open, true);
""")


def test_older_state_hides_the_separate_panel(node):
    run_page(node, r"""
assert.equal($('claude-desktop-panel').hidden, true);
assert.equal(button('claude-actions', 'Create empty profile'), undefined);
assert.equal(button('codex-actions', 'Open'), undefined);
await load();
assert.equal($('claude-desktop-panel').hidden, true);
""")


def test_create_requires_modal_consent_and_only_creates_an_empty_profile(node):
    run_page(node, r"""
apiState.claudeDesktop = {available: true, supported: true, installed: true, experimental: true,
  notice: 'Experimental local profiles', error: null, running: true, profiles: []};
await load();
assert.equal($('claude-desktop-panel').hidden, false);
assert.equal(button('claude-desktop-actions', 'Create empty profile').disabled, false);
assert.equal(button('claude-desktop-actions', 'Open usual Claude (default)').disabled, true);
assert.match($('claude-desktop-profiles').textContent, /No named profiles/);
assert.match($('claude-desktop-notice').textContent, /Experimental local profiles/);
button('claude-desktop-actions', 'Create empty profile').click();
assert.equal(posts().length, 0);
assert.match($('dialog-description').textContent, /macOS\/Linux only.*Signed-in persistence on Mac.*Code\/Cowork.*Claude-in-Chrome pairing.*Sign in to each.*Fully QUIT Claude.*not verified identities.*confirm the selected account/);
const name = nodes('dialog-fields').find(node => node.tagName === 'INPUT' && node.type === 'text');
const consent = nodes('dialog-fields').find(node => node.type === 'checkbox');
assert.equal(name.maxLength, 64);
assert.equal(consent.required, true);
await submitDialog();
assert.equal(posts().length, 0);
name.value = 'Research';
await submitDialog();
assert.equal(posts().length, 0);
consent.checked = true;
name.value = 'x'.repeat(65);
await submitDialog();
assert.equal(posts().length, 0);
name.value = ' Research ';
await submitDialog();
assert.deepEqual(posts().at(-1).payload, {name: 'Research', confirm: true});
assert.equal(posts().at(-1).path, '/api/claude-desktop/create');
assert.equal($('action-dialog').open, false);
assert.match($('toast').textContent, /Empty profile created; sign in through Claude/);
""")


def test_open_named_and_default_require_consent_and_launch_only_when_quit(node):
    run_page(node, r"""
const id = '0123456789abcdef0123456789abcdef';
apiState.claudeDesktop = {available: true, supported: true, installed: true, experimental: true,
  notice: 'Experimental', error: null, running: false, profiles: [{id, name: 'Work'}]};
await load();
const named = button('claude-desktop-profiles', 'Open');
assert.equal(named.attributes['aria-label'], 'Open Claude Desktop profile Work');
named.click();
assert.equal(posts().length, 0);
assert.match($('dialog-description').textContent, /Fully QUIT Claude.*confirm the selected account.*only requests a launch; it does not authenticate or switch an account.*Dock normally opens the usual default profile/);
await submitDialog();
assert.equal(posts().length, 0);
nodes('dialog-fields').find(node => node.type === 'checkbox').checked = true;
await submitDialog();
assert.equal(posts().at(-1).path, '/api/claude-desktop/open');
assert.deepEqual(posts().at(-1).payload, {profileId: id, confirm: true});
assert.match($('toast').textContent, /Launch requested only; confirm the selected account in Claude/);
assert.equal(document.activeElement, button('claude-desktop-actions', 'Check again'));
button('claude-desktop-actions', 'Open usual Claude (default)').click();
assert.equal(posts().length, 1);
nodes('dialog-fields').find(node => node.type === 'checkbox').checked = true;
await submitDialog();
assert.deepEqual(posts().at(-1).payload, {profileId: 'default', confirm: true});
assert.equal(posts().at(-1).path, '/api/claude-desktop/open');
""")


def test_running_unknown_unavailable_and_error_states(node):
    run_page(node, r"""
apiState.claudeDesktop = {available: true, supported: true, installed: true, experimental: true,
  notice: 'Experimental', error: null, running: null, profiles: [{id: 'a'.repeat(32), name: 'Work'}]};
await load();
const create = button('claude-desktop-actions', 'Create empty profile');
const usual = button('claude-desktop-actions', 'Open usual Claude (default)');
assert.equal(create.disabled, false);
assert.equal(usual.disabled, true);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
assert.match($('claude-desktop-status').textContent, /status is unknown/);
apiState.claudeDesktop.running = true;
await button('claude-desktop-actions', 'Check again').click();
assert.match($('claude-desktop-status').textContent, /Fully quit/);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
apiState.claudeDesktop.running = false;
await button('claude-desktop-actions', 'Check again').click();
assert.equal(button('claude-desktop-profiles', 'Open').disabled, false);
apiState.claudeDesktop = {available: false, supported: false, installed: false, experimental: true,
  notice: 'Unavailable', error: 'Detection failed', running: null, profiles: []};
await load();
assert.match($('claude-desktop-status').textContent, /macOS and Linux only.*Detection failed/);
assert.equal(create.disabled, true);
assert.equal(usual.disabled, true);
apiState.claudeDesktop.supported = true;
await load();
assert.match($('claude-desktop-status').textContent, /not installed/);
assert.equal(posts().length, 0);
""")


def test_profile_names_and_server_notices_are_text_not_markup(node):
    run_page(node, r"""
const content = '<img src=x onerror=alert(1)>';
apiState.claudeDesktop = {available: true, supported: true, installed: true, experimental: true,
  notice: content, error: content, running: false, profiles: [{id: 'b'.repeat(32), name: content}]};
await load();
assert.equal(nodes('claude-desktop-panel').some(node => node.tagName === 'IMG'), false);
assert.equal(nodes('claude-desktop-profiles').find(node => node.className === 'name').textContent, content);
assert.equal($('claude-desktop-notice').textContent, content);
assert.match($('claude-desktop-status').textContent, /<img src=x/);
button('claude-desktop-profiles', 'Open').click();
assert.match($('dialog-title').textContent, /<img src=x/);
assert.equal(nodes('action-dialog').some(node => node.tagName === 'IMG'), false);
""")


def test_launch_rechecks_process_state_and_busy_create_blocks_duplicates(node):
    run_page(node, r"""
apiState.claudeDesktop = {available: true, supported: true, installed: true, experimental: true,
  notice: 'Experimental', error: null, running: false, profiles: []};
await load();
button('claude-desktop-actions', 'Open usual Claude (default)').click();
nodes('dialog-fields').find(node => node.type === 'checkbox').checked = true;
apiState.claudeDesktop.running = true;
await load();
await submitDialog();
assert.equal(posts().length, 0);
assert.equal($('action-dialog').open, true);
$('dialog-cancel').click(); await settle();
button('claude-desktop-actions', 'Create empty profile').click();
nodes('dialog-fields').find(node => node.type === 'text').value = 'Lab';
nodes('dialog-fields').find(node => node.type === 'checkbox').checked = true;
let release;
fetchHandler = async path => path === '/api/claude-desktop/create'
  ? new Promise(resolve => { release = resolve; })
  : {ok: true, json: async () => apiState};
const pending = submitDialog();
await settle();
assert.equal(posts().length, 1);
assert.equal($('dialog-submit').disabled, true);
assert.equal($('dialog-cancel').disabled, true);
assert.equal(button('claude-desktop-actions', 'Create empty profile').disabled, true);
await submitDialog();
assert.equal(posts().length, 1);
release({ok: true, json: async () => ({ok: true, message: 'Created', profile: {id: 'c'.repeat(32), name: 'Lab'}})});
await pending;
assert.equal($('action-dialog').open, false);
assert.equal(button('claude-desktop-actions', 'Create empty profile').disabled, false);
""")
