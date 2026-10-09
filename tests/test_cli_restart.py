"""`veda` start: reuse a running Veda server, wait for one that is still starting, start one otherwise;
--restart stops the running one first.

Process handling is mocked; the live behaviour was checked against real servers.
Run: pytest tests/test_cli_restart.py
"""

import argparse

import pytest

from controller.cli import client as client_module
from controller.cli.client import CliError, VedaClient


@pytest.fixture(autouse=True)
def _isolated_pid_file(tmp_path, monkeypatch):
    """Never read or write the real data/veda-server.pid (a Veda server may be running on this machine)."""
    monkeypatch.setattr(client_module, "_pid_file", lambda port: tmp_path / f"veda-server-{port}.pid")
    return tmp_path / "veda-server-8000.pid"


class _Proc:
    def __init__(self, cmdline, pid=4242, name="python.exe"):
        self._cmd, self.pid, self._name = cmdline, pid, name
        self.terminated = False

    def cmdline(self):
        return self._cmd

    def name(self):
        return self._name

    def wait(self, timeout=None):
        if not self.terminated:
            import psutil
            raise psutil.TimeoutExpired(timeout)

    def children(self, recursive=True):
        return []

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


@pytest.mark.parametrize("cmd,expected", [
    (["python", "-m", "uvicorn", "server:app", "--port", "8000"], True),
    (["C:/x/veda.exe", "server"], True),
    (["python", "-m", "controller.cli", "server"], True),
    (["python", "-m", "http.server", "8000"], False),
    (["nginx", "-g", "daemon off;"], False),
    ([], False),
])
def test_only_recognisable_veda_servers_are_stoppable(cmd, expected):
    assert VedaClient._is_veda_server(_Proc(cmd)) is expected


def _client(listener):
    c = VedaClient(server_url="http://127.0.0.1:8000")
    c._listener = lambda: listener
    c._http.post = lambda *a, **k: None  # graceful shutdown request
    return c


def test_nothing_running_is_a_no_op():
    assert _client(None).stop_server() is None


def test_foreign_process_on_the_port_is_refused_and_left_alone():
    other = _Proc(["python", "-m", "http.server", "8000"], pid=7)
    with pytest.raises(CliError) as err:
        _client(other).stop_server()
    assert "not a Veda server" in str(err.value) and not other.terminated


def test_a_veda_server_that_ignores_graceful_shutdown_is_terminated():
    veda = _Proc(["python", "-m", "uvicorn", "server:app"], pid=99)
    assert _client(veda).stop_server(wait_secs=0.1) == 99
    assert veda.terminated


def test_ensure_up_restart_stops_first_and_reports_the_old_pid(monkeypatch):
    c = VedaClient(server_url="http://127.0.0.1:8000")
    order = []
    c.stop_server = lambda **k: order.append("stop") or 321
    ups = iter([False, True])  # down after the stop, up after the spawn
    c.is_up = lambda: next(ups)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: order.append("spawn"))
    assert c.ensure_up(restart=True, wait_secs=2) == 321
    assert order == ["stop", "spawn"]


def test_no_spawn_never_restarts():
    c = VedaClient(server_url="http://127.0.0.1:8000")
    c.stop_server = lambda **k: pytest.fail("must not stop a server when spawning is disabled")
    c.is_up = lambda: True
    assert c.ensure_up(allow_spawn=False, restart=True) is None


def test_spawn_uses_the_port_from_the_server_url(monkeypatch):
    c = VedaClient(server_url="http://127.0.0.1:8123")
    c.stop_server = lambda **k: None
    ups = iter([False, True])
    c.is_up = lambda: next(ups)
    seen = {}
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **k: seen.setdefault("cmd", cmd))
    c.ensure_up(restart=False, wait_secs=2)
    assert seen["cmd"][seen["cmd"].index("--port") + 1] == "8123"


# ---- reuse / wait / start ---------------------------------------------------------------------------------------------

def test_a_running_server_is_continued_not_restarted_or_spawned(monkeypatch):
    c = VedaClient(server_url="http://127.0.0.1:8000")
    c.stop_server = lambda **k: pytest.fail("a running server must be left alone")
    c.is_up = lambda: True
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("must not spawn a second server"))
    assert c.ensure_up(restart=False) is None and c.how == "reused"


def test_restart_reports_it_restarted(monkeypatch):
    c = VedaClient(server_url="http://127.0.0.1:8000")
    c.stop_server = lambda **k: 55
    ups = iter([False, True])
    c.is_up = lambda: next(ups)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    assert c.ensure_up(restart=True, wait_secs=2) == 55 and c.how == "restarted"


def test_a_server_that_is_still_loading_is_waited_for_and_no_second_one_is_started(monkeypatch, _isolated_pid_file):
    _isolated_pid_file.write_text("4321")
    c = VedaClient(server_url="http://127.0.0.1:8000")
    monkeypatch.setattr(client_module.VedaClient, "_starting_pid", lambda self: 4321)
    monkeypatch.setattr(client_module.VedaClient, "_died", staticmethod(lambda proc, pid: False))
    ups = iter([False, False, True])
    c.is_up = lambda: next(ups)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("a server is already starting; do not start another"))
    c.ensure_up(wait_secs=5)
    assert c.how == "waited"


def test_the_pid_of_a_spawned_server_is_remembered_so_the_next_veda_waits_for_it(monkeypatch, _isolated_pid_file):
    class Spawned:
        pid = 777

        def poll(self):
            return None

    c = VedaClient(server_url="http://127.0.0.1:8000")
    ups = iter([False, True])
    c.is_up = lambda: next(ups)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: Spawned())
    c.ensure_up(wait_secs=2)
    assert c.how == "started" and _isolated_pid_file.read_text() == "777"


def test_a_stale_pid_file_is_ignored():
    c = VedaClient(server_url="http://127.0.0.1:8000")
    assert c._starting_pid() is None                                  # no file
    client_module._pid_file(8000).write_text("not a number")
    assert c._starting_pid() is None
    client_module._pid_file(8000).write_text("2147483000")               # no such process
    assert c._starting_pid() is None


def test_a_server_that_dies_while_starting_shows_the_real_error_at_once(monkeypatch, tmp_path):
    class Dead:
        pid = 1

        def poll(self):
            return 1

    log = tmp_path / "veda-server.log"
    log.write_text("line1\nValueError: model file not found\n")
    c = VedaClient(server_url="http://127.0.0.1:8000")
    c.is_up = lambda: False
    c._spawn = lambda root, path: Dead()
    monkeypatch.setattr(VedaClient, "_log_tail", staticmethod(lambda p, lines=8: log.read_text()))
    with pytest.raises(CliError, match="(?s)stopped while starting.*model file not found"):
        c.ensure_up(wait_secs=30)


def test_the_wait_is_long_enough_for_a_model_load_and_configurable(monkeypatch):
    c = VedaClient(server_url="http://127.0.0.1:8000")
    c.is_up = lambda: False
    c._spawn = lambda root, path: type("P", (), {"pid": 5, "poll": lambda self: None})()
    monkeypatch.setenv("VEDA_START_TIMEOUT", "0.6")
    with pytest.raises(CliError, match="did not answer within 0s.*keep waiting"):
        c.ensure_up()
    monkeypatch.delenv("VEDA_START_TIMEOUT")
    import inspect
    assert "240" in inspect.getsource(VedaClient.ensure_up)


def test_with_no_spawn_nothing_is_started_or_waited_for():
    c = VedaClient(server_url="http://127.0.0.1:8000")
    c.is_up = lambda: False
    with pytest.raises(CliError, match="not reachable"):
        c.ensure_up(allow_spawn=False)


def test_plain_veda_never_restarts_unless_asked(monkeypatch):
    from controller.cli.__main__ import _wants_restart

    monkeypatch.delenv("VEDA_RESTART_ON_START", raising=False)
    ns = lambda **kw: argparse.Namespace(**{"no_spawn": False, **kw})   # noqa: E731
    assert _wants_restart(ns()) is False
    assert _wants_restart(ns(restart=True)) is True
    assert _wants_restart(ns(restart=True, no_spawn=True)) is False
    assert _wants_restart(ns(restart=True, no_restart=True)) is False
    monkeypatch.setenv("VEDA_RESTART_ON_START", "1")
    assert _wants_restart(ns()) is True
