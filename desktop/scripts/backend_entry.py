import runpy
import sys


def smoke_tls() -> int:
    import http.client
    import ssl

    from claude_swap.tls import use_native_tls

    stage = "initialization"
    try:
        import truststore

        use_native_tls()
        if ssl.SSLContext is not truststore.SSLContext:
            raise RuntimeError("native TLS is not active")
        context = ssl.create_default_context()
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise RuntimeError("TLS verification is not enabled")
        for host in ("chatgpt.com", "auth.openai.com", "api.anthropic.com", "platform.claude.com"):
            stage = host
            connection = http.client.HTTPSConnection(host, timeout=10, context=context)
            try:
                connection.request("HEAD", "/")
                connection.getresponse().close()
            finally:
                connection.close()
    except Exception as exc:
        print(f"Frozen helper TLS smoke failed ({stage}: {type(exc).__name__})", file=sys.stderr)
        return 1
    print("Frozen helper TLS smoke passed")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--smoke-tls"]:
        return smoke_tls()
    runpy.run_module("claude_swap.desktop", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
