"""Historical credential service names for explicit, copy-only import.

Normal construction never migrates another tool's files or Keychain entries.
Legacy data is accessed only through :mod:`agents_switcher.legacy_import`.
"""

LEGACY_SECURITY_SERVICE = "claude-swap"
LEGACY_KEYRING_SERVICE = "claude-code"
