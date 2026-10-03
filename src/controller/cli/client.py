"""HTTP + SSE client for the Veda CLI.

Donor: veda/cli/client.py, read in full. Changes:
  - Liveness check moved from `/api/teams/status` (Teams, out of scope)
    to `/api/health` (controller/routes/health.py, Batch 9).
  - `upload_image`/`upload_images` and every `image_paths` parameter
    dropped — vision/image attachments are out of scope; `ChatRequest`/
    `StreamRequest` (schemas/chat.py) no longer have the field at all.
  - `status_snapshot()` drops the `vision`/`teams` probes (routes don't
    exist); keeps `gov`/`yolo`/`proac`.
  - Auto-spawn now launches `uvicorn server:app` directly (this repo's
    composition root) instead of `python -m veda` (the donor's package
    shim).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx

from core.logging_config import logger

_DEFAULT_URL = os.environ.get("VEDA_SERVER_URL", "http://127.0.0.1:8000")


class CliError(RuntimeError):
    """Raised for any CLI-level failure (connection, server error, etc)."""


class VedaClient:
    """Thin synchronous client. Sufficient for argparse-driven CLIs; the
    server itself stays async — we just block on HTTP from the caller."""

    def __init__(self, server_url: str | None = None, timeout: float = 30.0) -> None:
        self.server_url = (server_url or _DEFAULT_URL).rstrip("/")
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass

    # ---------- liveness / spawn ----------

    def is_up(self) -> bool:
        try:
            r = self._http.get(f"{self.server_url}/api/health", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_up(self, *, allow_spawn: bool = True, wait_secs: float = 30.0) -> None:
        """Block until the server responds; spawn it if missing.

        Raises :class:`CliError` if the server can't be reached after the
        wait window (or ``allow_spawn=False`` and no server is running).
        """
        if self.is_up():
            return
        if not allow_spawn:
            raise CliError(
                f"Veda server not reachable at {self.server_url}. "
                f"Start it with `veda server` or drop --no-spawn."
            )
        repo_root = Path(__file__).resolve().parents[3]
        src_dir = repo_root / "src"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")
        host = "127.0.0.1"
        port = "8000"
        log_path = repo_root / "data" / "veda-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "wb")
        kwargs: dict[str, Any] = {
            "stdout": log_file, "stderr": log_file, "stdin": subprocess.DEVNULL,
            # cwd must be src/, not repo_root: the repo also has a root-level
            # server.py (the `veda server` launcher script, no `app` attribute).
            # With cwd=repo_root, python -m uvicorn puts repo_root on
            # sys.path[0] ahead of the PYTHONPATH entry above, so "server:app"
            # resolves to the wrong file ("Attribute 'app' not found in module
            # 'server'"). cwd=src_dir makes "server" unambiguous.
            "env": env, "cwd": str(src_dir),
        }
        if sys.platform == "win32":
            # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP — hide the console
            # and isolate the child from our REPL's Ctrl-C.
            kwargs["creationflags"] = 0x08000000 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        logger.info(f"Veda server not reachable; spawning at {self.server_url}")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server:app", "--host", host, "--port", port],
                **kwargs,
            )
        finally:
            log_file.close()

        deadline = time.monotonic() + wait_secs
        while time.monotonic() < deadline:
            if self.is_up():
                return
            time.sleep(0.5)
        raise CliError(
            f"Spawned Veda server but it didn't come up within {wait_secs}s. "
            f"Check the log at {log_path}."
        )

    # ---------- chat / stream ----------

    def stream_chat(self, message: str, *, from_voice: bool = False, system_context: str = "") -> Iterator[str]:
        """Yield text tokens as they arrive from /api/stream.

        Stops at the ``[DONE]`` sentinel. Caller can break early to cancel
        (the underlying disconnect signals the server to abort the run).
        """
        body: dict[str, Any] = {"message": message, "from_voice": from_voice, "system_context": system_context}

        with self._http.stream(
            "POST", f"{self.server_url}/api/stream", json=body,
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
        ) as resp:
            resp.raise_for_status()
            data_lines: list[str] = []
            for raw in resp.iter_lines():
                line = raw or ""
                if line == "":
                    if not data_lines:
                        continue
                    text = "\n".join(data_lines)
                    data_lines = []
                    if text == "[DONE]":
                        return
                    yield text
                    continue
                if line.startswith(":"):
                    continue  # SSE comment
                if line.startswith("data:"):
                    value = line[5:]
                    if value.startswith(" "):
                        value = value[1:]
                    data_lines.append(value)

    def chat_one_shot(self, message: str, *, from_voice: bool = False, system_context: str = "") -> str:
        """Non-streaming /api/chat — used for single-line responses (status, etc)."""
        r = self._http.post(
            f"{self.server_url}/api/chat",
            json={"message": message, "from_voice": from_voice, "system_context": system_context},
        )
        r.raise_for_status()
        return r.json().get("response", "")

    # ---------- persona ----------

    def get_persona(self) -> dict[str, Any]:
        r = self._http.get(f"{self.server_url}/api/persona")
        r.raise_for_status()
        return r.json()

    def set_persona(self, persona: str | None = None, completed: bool | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if persona is not None:
            body["persona"] = persona
        if completed is not None:
            body["completed"] = completed
        r = self._http.post(f"{self.server_url}/api/persona", json=body)
        r.raise_for_status()
        return r.json()

    # ---------- approvals ----------

    def pending_approvals(self) -> list[dict[str, Any]]:
        r = self._http.get(f"{self.server_url}/api/approval/pending")
        r.raise_for_status()
        return r.json().get("pending", []) or []

    def approve(self, request_id: str) -> None:
        r = self._http.post(f"{self.server_url}/api/approval/{request_id}/approve")
        r.raise_for_status()

    def deny(self, request_id: str) -> None:
        r = self._http.post(f"{self.server_url}/api/approval/{request_id}/deny")
        r.raise_for_status()

    # ---------- config ----------

    def yolo(self, on: bool) -> None:
        suffix = "on" if on else "off"
        r = self._http.post(f"{self.server_url}/api/approval/yolo/{suffix}")
        r.raise_for_status()

    def set_proactivity(self, level: str) -> None:
        r = self._http.post(f"{self.server_url}/api/config/proactivity", json={"level": level})
        r.raise_for_status()

    # ---------- privacy / mic ----------

    def privacy_status(self) -> dict[str, Any]:
        r = self._http.get(f"{self.server_url}/api/privacy/status")
        r.raise_for_status()
        return r.json()

    def voice_status(self) -> dict[str, Any]:
        r = self._http.get(f"{self.server_url}/api/voice/status")
        r.raise_for_status()
        return r.json()

    def set_mute(self, muted: bool) -> dict[str, Any]:
        r = self._http.post(f"{self.server_url}/api/privacy/mute", json={"muted": muted})
        r.raise_for_status()
        return r.json()

    def toggle_mute(self) -> dict[str, Any]:
        r = self._http.post(f"{self.server_url}/api/privacy/mute/toggle")
        r.raise_for_status()
        return r.json()

    # ---------- tool-turn traces ----------

    def recent_traces(self, limit: int = 10) -> list[dict[str, Any]]:
        r = self._http.get(f"{self.server_url}/api/trace", params={"limit": limit})
        r.raise_for_status()
        return r.json().get("traces", []) or []

    # ---------- online tool health ----------

    def lookup_health(self) -> dict[str, Any]:
        r = self._http.get(f"{self.server_url}/api/lookup/health", timeout=30.0)
        r.raise_for_status()
        return r.json()

    # ---------- tasks ----------

    def list_tasks(self, include_done: bool = False) -> list[dict[str, Any]]:
        r = self._http.get(f"{self.server_url}/api/tasks", params={"include_done": include_done})
        r.raise_for_status()
        return r.json().get("tasks", []) or []

    def add_task(self, title: str, notes: str = "") -> dict[str, Any]:
        r = self._http.post(f"{self.server_url}/api/tasks", json={"title": title, "notes": notes})
        r.raise_for_status()
        return r.json()

    def complete_task(self, task_id: int) -> dict[str, Any]:
        r = self._http.post(f"{self.server_url}/api/tasks/{task_id}/complete")
        r.raise_for_status()
        return r.json()

    # ---------- status snapshot ----------

    def status_snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {"server_url": self.server_url}
        for key, path in [
            ("gov", "/api/governance/audit/stats"),
            ("yolo", "/api/approval/yolo"),
            ("proac", "/api/config/proactivity"),
        ]:
            try:
                r = self._http.get(f"{self.server_url}{path}", timeout=4.0)
                out[key] = r.json() if r.status_code == 200 else None
            except Exception:
                out[key] = None
        return out


@contextmanager
def open_client(server_url: str | None = None) -> Iterator[VedaClient]:
    c = VedaClient(server_url=server_url)
    try:
        yield c
    finally:
        c.close()