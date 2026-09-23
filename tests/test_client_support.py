from claude_swap.client_support import CLAUDE_SWITCH_NOTICE
from claude_swap.models import Platform
from claude_swap.switcher import ClaudeAccountSwitcher


def test_cli_followup_explains_desktop_sign_in(temp_home, capsys):
    switcher = ClaudeAccountSwitcher()
    switcher.platform = Platform.LINUX
    switcher._print_switch_followup()

    output = capsys.readouterr().out
    assert "New CLI account" in output
    assert CLAUDE_SWITCH_NOTICE in output


def test_structured_switch_reports_its_client_scope(temp_home):
    switcher = ClaudeAccountSwitcher()
    before = {"number": 1, "email": "before@example.com"}
    after = {"number": 2, "email": "after@example.com"}
    result = switcher._switch_result_from_op(
        {"from": before, "to": after, "warnings": []}, "direct"
    )

    assert result["switched"] is True
    assert result["supportedClient"] == "claude-code"
    assert result["desktopSessionChanged"] is False
    assert result["clientNotice"] == CLAUDE_SWITCH_NOTICE


def test_noop_does_not_claim_desktop_changed(temp_home):
    result = ClaudeAccountSwitcher()._switch_noop(
        strategy="direct", reason="already-active", message="Already active"
    )

    assert result["switched"] is False
    assert result["desktopSessionChanged"] is False
    assert result["clientNotice"] == CLAUDE_SWITCH_NOTICE
