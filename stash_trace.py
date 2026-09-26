import functools
import traceback


def pytest_configure(config):
    from claude_swap.credentials import CredentialStore

    for name in ("_write_unclaimed_credential", "_remove_unclaimed_credential"):
        original = getattr(CredentialStore, name)

        @functools.wraps(original)
        def traced(*args, _original=original, **kwargs):
            try:
                return _original(*args, **kwargs)
            except Exception as error:
                print("STASH_FAILURE", type(error).__name__, "winerror", getattr(error, "winerror", None), flush=True)
                traceback.print_exception(type(error), error, error.__traceback__)
                raise

        setattr(CredentialStore, name, traced)
