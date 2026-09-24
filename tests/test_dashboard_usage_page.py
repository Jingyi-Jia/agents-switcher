from tests.test_web_page_actions import node, run_page


def test_failed_usage_has_an_explicit_value_and_full_error_detail(node):
    run_page(node, r"""
const error = "Codex needs a fresh login for this account. Run 'codex login' as the affected account, then add the existing login again.";
apiState.codex.accounts[0].error = error;
await load();
const tile = $('kpis').children[0];
assert.equal(tile.children.find(node => node.className === 'value').textContent, 'Unavailable');
const detail = tile.children.find(node => node.className === 'sub');
assert.equal(detail.textContent, error);
assert.equal(detail.title, error);
assert.match($('codex').textContent, /fresh login/);
""")


def test_missing_quota_is_distinct_from_a_reported_zero(node):
    run_page(node, r"""
await load();
assert.equal($('kpis').children[0].children.find(node => node.className === 'value').textContent, 'Not reported');
assert.match($('kpis').children[0].textContent, /provider has not reported quota/);
apiState.codex.accounts[0].windows = [{label: '5h', usedPercent: 100}];
await load();
assert.match($('kpis').children[0].children.find(node => node.className.startsWith('value')).textContent, /^0%/);
""")


def test_active_account_change_moves_the_quota_tile_and_switch_badge_together(node):
    run_page(node, r"""
apiState.codex.accounts[0].error = 'Fresh login required';
apiState.codex.accounts[1].windows = [{label: '5h', usedPercent: 20}];
await load();
assert.match($('kpis').children[0].textContent, /Unavailable/);
apiState.codex.accounts[0].active = false;
apiState.codex.accounts[1].active = true;
await load();
assert.match($('kpis').children[0].textContent, /80%left/);
assert.equal(nodes('codex').find(node => node.attributes['aria-label'] === 'Account 2 is active').disabled, true);
assert.equal(nodes('codex').find(node => node.attributes['aria-label'] === 'Switch to account 1').disabled, false);
""")
