import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from claude_swap.codex import autoswitch
from claude_swap.codex.autoswitch import Action, AutoDecision
from claude_swap.web import cli, tray
from claude_swap.web.server import DashboardState
from tests.test_provider_actions import Codex


def test_tray_quit_waits_for_owned_auto_tick_and_closes_server(tmp_path, monkeypatch):
    state = DashboardState(codex_switcher=Codex(tmp_path / "codex"))
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    clear = Mock()
    servers = []
    serve = cli.serve

    def start(*args, **kwargs):
        server, url = serve(*args, **kwargs)
        servers.append(server)
        return server, url

    def tick(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return AutoDecision(Action.HOLD, "holding")

    def run(*args, on_quit, **kwargs):
        state.actions.auto.configure("codex", "live", confirm=True)
        assert entered.wait(2)
        auto_worker = state.actions.auto._workers["codex"][0]
        quitter = threading.Thread(target=lambda: (on_quit(), closed.set()))
        quitter.start()
        try:
            assert not closed.wait(0.05)
        finally:
            release.set()
            quitter.join(3)
        assert closed.is_set()
        assert not auto_worker.is_alive()

    monkeypatch.setattr(cli, "_build_state", lambda: state)
    monkeypatch.setattr(cli, "live_dashboard_url", lambda: None)
    monkeypatch.setattr(cli, "publish_url", Mock())
    monkeypatch.setattr(cli, "clear_url", clear)
    monkeypatch.setattr(cli, "serve", start)
    monkeypatch.setattr(tray, "available_backend", lambda: "pystray")
    monkeypatch.setattr(tray, "run", run)
    monkeypatch.setattr(autoswitch, "run_once", tick)

    cli.tray_command(["--port", "0"])

    assert servers[0].socket.fileno() == -1
    assert state.actions.auto.status("codex")["mode"] == "stopped"
    clear.assert_called_once_with()


@pytest.mark.parametrize("existing", [None, "http://127.0.0.1:8765/"])
def test_tray_without_owned_server_only_closes_its_own_state(existing, monkeypatch):
    state, clear = Mock(), Mock()
    monkeypatch.setattr(cli, "_build_state", lambda: state)
    monkeypatch.setattr(cli, "live_dashboard_url", lambda: existing)
    monkeypatch.setattr(cli, "clear_url", clear)
    monkeypatch.setattr(cli, "serve", Mock(side_effect=OSError("unavailable")))
    monkeypatch.setattr(tray, "available_backend", lambda: "pystray")
    monkeypatch.setattr(tray, "run", Mock(side_effect=RuntimeError("backend stopped")))

    with pytest.raises(SystemExit):
        cli.tray_command([])

    state.close.assert_called_once_with()
    clear.assert_not_called()


def test_rumps_cleans_up_before_native_process_termination(monkeypatch):
    events = []

    class NativeExit(BaseException):
        pass

    class App:
        def __init__(self, *args, **kwargs):
            self.menu = []

        def run(self):
            next(item for item in self.menu if getattr(item, "label", "") == "Quit").callback(None)

    class Timer:
        def __init__(self, *args):
            pass

        def start(self):
            pass

        def stop(self):
            events.append("timer stopped")

    def terminate():
        events.append("native exit")
        raise NativeExit()

    monkeypatch.setitem(sys.modules, "rumps", SimpleNamespace(
        App=App, Timer=Timer, separator=None,
        MenuItem=lambda label, callback=None: SimpleNamespace(label=label, callback=callback),
        quit_application=terminate,
    ))
    with pytest.raises(NativeExit):
        tray.run(
            lambda: tray.TrayModel("demo", "demo", ()), lambda *args: None,
            lambda: None, backend="rumps", on_quit=lambda: events.append("closed"),
        )

    assert events.index("timer stopped") < events.index("closed") < events.index("native exit")
    assert events.count("closed") == 1


def test_pystray_joins_its_quota_poller_before_cleanup(monkeypatch):
    polled = threading.Event()
    calls = []

    def refresh():
        calls.append("refresh")
        if len(calls) > 1:
            polled.set()
        return tray.TrayModel("demo", "demo", ())

    class Icon:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            assert polled.wait(2)

        def update_menu(self):
            pass

    menu = Mock(side_effect=lambda *args: args)
    menu.SEPARATOR = None
    monkeypatch.setitem(sys.modules, "pystray", SimpleNamespace(
        Icon=Icon, Menu=menu, MenuItem=lambda *args, **kwargs: None,
    ))
    monkeypatch.setattr(tray, "_icon_image", lambda *_: None)
    before = set(threading.enumerate())

    def cleanup():
        assert set(threading.enumerate()) == before
        calls.append("closed")

    tray.run(refresh, lambda *args: None, lambda: None, backend="pystray", interval=0.01, on_quit=cleanup)
    assert calls[-1] == "closed"
