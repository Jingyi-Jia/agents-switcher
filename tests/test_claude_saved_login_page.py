from tests.test_web_page_actions import node, run_page


def test_recovery_guidance_and_action_are_specific_to_current_rejected_claude_login(node):
    run_page(node, r"""
apiState.claude.accounts.forEach(a => { a.sentinel = 're-login needed'; });
await load();
const cards = nodes('claude').filter(n => n.tagName === 'ARTICLE');
assert.match(cards[0].textContent, /Signing in or restarting this app alone does not replace/);
assert.match(cards[1].textContent, /Sign in to this account and organization/);
assert.equal(nodes('claude').filter(n => n.textContent === 'Update saved login' && n.tagName === 'BUTTON').length, 1);
const update = button('claude', 'Update saved login');
assert.equal(update.attributes['aria-label'], 'Update saved login for Claude account 1');
apiState.claude.accounts[0].sentinel = null;
await update.click();
assert.equal(posts().at(-1).path, '/api/claude/login/update');
assert.deepEqual(posts().at(-1).payload, {number: '1', confirm: true});
assert(calls.some(c => c.path === '/api/state?force=1'));
assert.equal(document.activeElement.dataset.focusKey, 'claude:menu:1');
await load(true);
assert.equal(button('claude', 'Update saved login'), undefined);
apiState.codex.accounts[0].sentinel = 're-login needed';
await load(true);
assert.equal(button('codex', 'Update saved login'), undefined);
""")


def test_recovery_action_respects_capability_and_busy_state(node):
    run_page(node, r"""
apiState.claude.accounts[0].sentinel = 're-login needed';
apiState.claude.capabilities = apiState.claude.capabilities.filter(c => c !== 'add');
await load();
assert.equal(button('claude', 'Update saved login').disabled, true);
apiState.claude.capabilities.push('add'); await load();
let finish;
fetchHandler = async (path) => path === '/api/claude/login/update'
  ? new Promise(resolve => { finish = resolve; })
  : {ok: true, json: async () => apiState};
const update = button('claude', 'Update saved login');
const pending = update.click();
assert.equal(update.disabled, true);
await update.click();
assert.equal(posts().length, 1);
finish({ok: false, json: async () => ({ok: false, message: 'The current Claude CLI login does not match this saved account.'})});
await pending;
assert.match($('toast').textContent, /does not match/);
assert.match($('claude').textContent, /re-login needed/);
assert.equal(button('claude', 'Update saved login').disabled, false);
""")
