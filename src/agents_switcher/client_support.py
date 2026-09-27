"""Authentication boundaries exposed by every account-switching interface."""

CLAUDE_SWITCH_NOTICE = (
    "Switches Claude Code CLI credentials only. Claude Desktop (including its "
    "Code tab) has a separate sign-in and is not switched. To change its account, "
    "sign out and sign in inside Claude Desktop; restarting alone does not "
    "transfer the CLI login. The app/dashboard also offers a separate experimental "
    "Claude Desktop profile launcher; it does not use these CLI accounts."
)

CLAUDE_CLIENT_SCOPE = {
    "supportedClient": "claude-code",
    "desktopSessionChanged": False,
    "clientNotice": CLAUDE_SWITCH_NOTICE,
}
