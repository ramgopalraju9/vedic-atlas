"""Interactive REPL for the Veda CLI.

Donor: veda/cli/repl.py, read in full. Dropped entirely: `/img add|list|
clear`, `/paste` (clipboard image staging via Pillow), and inline `@path`
image-reference extraction in `_send_chat` — all image-attachment
features, out of scope (vision is dropped; `ChatRequest`/`StreamRequest`
no longer carry `image_paths`). Approval field names updated to
`request_id`/`action_name` (Batch 6 rename).

Slash-commands (``:cmd``) handle non-chat operations; bare text becomes a
chat turn. Ctrl-C cancels an in-flight stream; Ctrl-D / ``:quit`` exits.
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML
from rich.console import Console

from controller.cli.client import VedaClient
from controller.cli.commands import render_doctor, render_traces
from controller.cli.logo import render_banner, status_line
from controller.cli.render import stream_to_terminal


_HELP = """
[bold]Slash commands[/bold]  ([dim]use [cyan]/[/cyan] or [cyan]:[/cyan] — both work[/dim])
  /help              show this help
  /quit \u00b7 /exit      leave the REPL (Ctrl-D works too)
  /clear             clear the screen + scrollback
  /persona           print current persona
  /persona <name>    switch persona (developer / scrum / architect / manager)
  /status            one-line status snapshot
  /yolo on \u00b7 /yolo off
                     toggle approval-mode (auto vs ask)
  /approve           list pending approvals (resolve interactively)
  /mute \u00b7 /unmute    close / open the microphone
  /listen            show mic state (muted / listening / turns)
  /doctor            check the online tools (weather, search, currency)
  /trace [n]         show what the tools actually did on the last n turns
  /task              list tasks · /task add <title> · /task done <id>
"""


class Repl:
    def __init__(self, client: VedaClient, console: Console | None = None) -> None:
        self.client = client
        self.console = console or Console()
        self.history = InMemoryHistory()
        self.session: PromptSession[str] = PromptSession(history=self.history)
        self.persona: str | None = None
        self.brain: str = "?"

    def run(self) -> int:
        self._refresh_meta()
        render_banner(
            persona=self.persona, server_url=self.client.server_url, brain=self.brain,
            warm_pool=True, state="idle", console=self.console,
        )
        while True:
            try:
                self.console.print(status_line(persona=self.persona, brain=self.brain))
                line = self.session.prompt(HTML("<ansicyan><b>veda</b></ansicyan> > "))
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                return 0
            line = (line or "").strip()
            if not line:
                continue
            if line.startswith(("/", ":")):
                if not self._handle_slash(line[1:].strip()):
                    return 0
                continue
            self._send_chat(line)

    # ---------- slash commands ----------

    def _handle_slash(self, body: str) -> bool:
        """Return False to exit the REPL, True to keep going."""
        head, _, rest = body.partition(" ")
        cmd = head.lower()
        rest = rest.strip()

        if cmd in ("quit", "exit"):
            return False
        if cmd == "help":
            self.console.print(_HELP)
            return True
        if cmd == "clear":
            self.console.clear()
            return True
        if cmd == "persona":
            self._cmd_persona(rest)
            return True
        if cmd == "status":
            self._cmd_status()
            return True
        if cmd == "yolo":
            self._cmd_yolo(rest)
            return True
        if cmd == "approve":
            self._cmd_approve()
            return True
        if cmd in ("mute", "unmute"):
            self._cmd_mute(cmd == "mute")
            return True
        if cmd == "listen":
            self._cmd_listen()
            return True
        if cmd == "task":
            self._cmd_task(rest)
            return True
        if cmd == "trace":
            try:
                render_traces(self.console, self.client.recent_traces(int(rest) if rest.strip().isdigit() else 5))
            except Exception as e:
                self.console.print(f"[red]trace failed:[/red] {e}")
            return True
        if cmd == "doctor":
            try:
                render_doctor(self.console, self.client.lookup_health())
            except Exception as e:
                self.console.print(f"[red]doctor failed:[/red] {e}")
            return True
        self.console.print(f"[red]unknown command :{cmd}[/red] \u2014 try :help")
        return True

    def _cmd_persona(self, name: str) -> None:
        try:
            if not name:
                snap = self.client.get_persona()
                state = snap.get("state", {}) or {}
                self.console.print(
                    f"persona: [bold]{state.get('persona') or 'unset'}[/bold] "
                    f"\u00b7 completed: {state.get('completed')}"
                )
                return
            r = self.client.set_persona(persona=name, completed=True)
            self.persona = (r.get("state") or {}).get("persona")
            self.console.print(f"[green]persona set to {self.persona}[/green]")
        except Exception as e:
            self.console.print(f"[red]persona failed:[/red] {e}")

    def _cmd_status(self) -> None:
        try:
            snap = self.client.status_snapshot()
            self.console.print(snap)
        except Exception as e:
            self.console.print(f"[red]status failed:[/red] {e}")

    def _cmd_yolo(self, arg: str) -> None:
        on = arg.strip().lower() == "on"
        try:
            self.client.yolo(on)
            self.console.print(f"yolo: [bold]{'on' if on else 'off'}[/bold]")
        except Exception as e:
            self.console.print(f"[red]yolo failed:[/red] {e}")

    def _cmd_approve(self) -> None:
        try:
            pending = self.client.pending_approvals()
        except Exception as e:
            self.console.print(f"[red]approve failed:[/red] {e}")
            return
        if not pending:
            self.console.print("[dim]no pending approvals[/dim]")
            return
        for p in pending:
            self.console.print(
                f"[yellow]{p.get('request_id')}[/yellow] "
                f"action=[bold]{p.get('action_name')}[/bold] "
                f"age={p.get('age_sec')}s \u2014 {p.get('summary')}"
            )
            ans = self.session.prompt(HTML("approve? <ansiyellow>[y/n/skip]</ansiyellow> "))
            if ans.strip().lower() == "y":
                self.client.approve(p["request_id"])
                self.console.print("[green]approved[/green]")
            elif ans.strip().lower() == "n":
                self.client.deny(p["request_id"])
                self.console.print("[red]denied[/red]")

    def _cmd_mute(self, muted: bool) -> None:
        try:
            priv = self.client.set_mute(muted)
        except Exception as e:
            self.console.print(f"[red]{'mute' if muted else 'unmute'} failed:[/red] {e}")
            return
        if priv.get("muted", True):
            self.console.print("[bold red]MIC CLOSED[/bold red] (muted)")
        else:
            self.console.print("[bold green]MIC OPEN[/bold green] (listening)")

    def _cmd_listen(self) -> None:
        try:
            priv = self.client.privacy_status()
        except Exception as e:
            self.console.print(f"[red]status failed:[/red] {e}")
            return
        try:
            voice = self.client.voice_status()
        except Exception:
            voice = None
        state = (
            "[bold red]MIC CLOSED[/bold red] (muted)" if priv.get("muted", True)
            else "[bold green]MIC OPEN[/bold green] (listening)"
        )
        self.console.print(state)
        detail = f"[dim]source={priv.get('source', '?')}"
        if voice and voice.get("available"):
            detail += f" \u00b7 running={str(voice.get('running', False)).lower()} \u00b7 turns={voice.get('turns', 0)}"
        self.console.print(detail + "[/dim]")

    def _cmd_task(self, rest: str) -> None:
        parts = rest.split()
        action = parts[0].lower() if parts else "list"
        args = parts[1:]
        try:
            if action == "add":
                title = " ".join(args).strip()
                if not title:
                    self.console.print("[red]usage: /task add <title>[/red]")
                    return
                t = self.client.add_task(title)
                self.console.print(f"[green]added[/green] #{t.get('id')}: {t.get('title')}")
            elif action == "done":
                if not args:
                    self.console.print("[red]usage: /task done <id>[/red]")
                    return
                self.client.complete_task(int(args[0]))
                self.console.print(f"[green]completed[/green] #{args[0]}")
            else:
                tasks = self.client.list_tasks()
                if not tasks:
                    self.console.print("[dim]no pending tasks[/dim]")
                    return
                for t in tasks:
                    mark = "[x]" if t.get("done") else "[ ]"
                    self.console.print(f"#{t.get('id')} {mark} {t.get('title')}")
        except Exception as e:
            self.console.print(f"[red]task failed:[/red] {e}")

    # ---------- chat ----------

    def _send_chat(self, message: str) -> None:
        try:
            tokens = self.client.stream_chat(message)
            stream_to_terminal(tokens, console=self.console)
        except KeyboardInterrupt:
            self.console.print("[dim]^C \u2014 cancelled[/dim]")
        except Exception as e:
            self.console.print(f"[red]chat failed:[/red] {e}")

    # ---------- helpers ----------

    def _refresh_meta(self) -> None:
        try:
            snap = self.client.get_persona()
            self.persona = (snap.get("state") or {}).get("persona")
        except Exception:
            self.persona = None
        try:
            r = self.client._http.get(f"{self.client.server_url}/api/governance/status", timeout=2.0)
            self.brain = (r.json() or {}).get("provider", "?") if r.status_code == 200 else "?"
        except Exception:
            self.brain = "?"