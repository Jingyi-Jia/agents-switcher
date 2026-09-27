"""Shared TLS initialization for application entry points."""

from __future__ import annotations


def use_native_tls() -> None:
    """Use the OS trust store while preserving certificate and hostname checks.

    Native verification handles system roots and certificate chains that a
    bundled Python/OpenSSL installation may not resolve. If truststore is
    unavailable, retain the verified stdlib defaults rather than prevent startup.
    Call before creating provider clients or starting background workers.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass
