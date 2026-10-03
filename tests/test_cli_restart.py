"""`veda` start: stop any existing Veda server, then spawn a fresh one.

Process handling is mocked; the live behaviour was checked against real servers.
Run: pytest tests/test_cli_restart.py
"""

import pytest

from controller.cli.client import CliError, VedaClient


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
