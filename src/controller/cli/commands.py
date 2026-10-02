"""Power subcommands — approve / config / status.

Donor: veda/cli/commands.py, read in full. Changes:
  - `cmd_run` dropped entirely — it followed the code-runner's
    RUNNER_OUTPUT ambient-event channel, which doesn't exist in this
    build (code agent out of scope; `EventKind.RUNNER_OUTPUT` was
    dropped in Batch 2).
  - `tool_name`/`approval_id` field names -> `action_name`/`request_id`,
    matching the Batch 6 rename (`ApprovalRequest` entity) and the
    Batch 9 approval route's actual response shape.
  - `cmd_status` drops the `vision`/`teams` panel rows — neither route
    exists.
"""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.table import Table

from controller.cli.client import VedaClient


# ---------- approve ----------

def cmd_approve(
    client: VedaClient,
    *,
    list_only: bool = False,
    approve_id: str | None = None,
    deny_id: str | None = None,
) -> int:
    """List pending approvals; or approve / deny a specific one.

    Without flags: interactive — list all and prompt y/n per ask.
    """
    console = Console()

    if approve_id and deny_id:
        console.print("[red]pick either --id or --deny, not both[/red]")
        return 2

    try:
        pending = client.pending_approvals()
    except Exception as e:
        console.print(f"[red]approve list failed:[/red] {e}")
        return 1

    if list_only:
        _print_pending(console, pending)
        return 0

    if approve_id:
        try:
            client.approve(approve_id)
            console.print(f"[green]approved {approve_id}[/green]")
            return 0
        except Exception as e:
            console.print(f"[red]approve failed:[/red] {e}")
            return 1

    if deny_id:
        try:
            client.deny(deny_id)
            console.print(f"[red]denied {deny_id}[/red]")
            return 0
        except Exception as e:
            console.print(f"[red]deny failed:[/red] {e}")
            return 1

    if not pending:
        console.print("[dim]no pending approvals[/dim]")
        return 0

    if not sys.stdin.isatty():
        _print_pending(console, pending)
        console.print("[dim]not a TTY — pass --id <ID> [--deny] to resolve programmatically[/dim]")
        return 0

    for p in pending:
        console.print(
            f"[yellow]{p.get('request_id')}[/yellow]  "
            f"action=[bold]{p.get('action_name')}[/bold]  age={p.get('age_sec')}s\n"
            f"  {p.get('summary')}"
        )
        try:
            ans = input("approve? [y/n/skip] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 130
        if ans == "y":
            client.approve(p["request_id"])
            console.print("[green]approved[/green]")
        elif ans == "n":
            client.deny(p["request_id"])
            console.print("[red]denied[/red]")
    return 0


def _print_pending(console: Console, pending: list[dict[str, Any]]) -> None:
    if not pending:
        console.print("[dim]no pending approvals[/dim]")
        return
    tbl = Table(title="Pending approvals")
    tbl.add_column("id", style="cyan")
    tbl.add_column("action")
    tbl.add_column("age", style="dim")
    tbl.add_column("summary")
    for p in pending:
        tbl.add_row(
            str(p.get("request_id", "")),
            str(p.get("action_name", "")),
            f"{p.get('age_sec', 0)}s",
            (str(p.get("summary", "")))[:80],
        )
    console.print(tbl)


# ---------- config ----------

def cmd_config(client: VedaClient, key: str, value: str) -> int:
    """`veda config yolo on|off` and `veda config proactivity <level>`."""
    console = Console()
    key = (key or "").lower()

    if key == "yolo":
        v = (value or "").lower()
        if v not in ("on", "off"):
            console.print("[red]usage: veda config yolo on|off[/red]")
            return 2
        try:
            client.yolo(v == "on")
            console.print(f"yolo: [bold]{v}[/bold]")
            return 0
        except Exception as e:
            console.print(f"[red]yolo failed:[/red] {e}")
            return 1

    if key == "proactivity":
        v = (value or "").lower()
        if v not in ("conservative", "medium", "chatty"):
            console.print("[red]usage: veda config proactivity conservative|medium|chatty[/red]")
            return 2
        try:
            client.set_proactivity(v)
            console.print(f"proactivity: [bold]{v}[/bold]")
            return 0
        except Exception as e:
            console.print(f"[red]proactivity failed:[/red] {e}")
            return 1

    console.print(f"[red]unknown config key '{key}'[/red] — try yolo or proactivity")
    return 2


# ---------- status ----------

def cmd_status(client: VedaClient) -> int:
    """Compact one-screener of system state — useful in tmux/Polybar."""
    console = Console()
    try:
        snap = client.status_snapshot()
    except Exception as e:
        console.print(f"[red]status failed:[/red] {e}")
        return 1

    tbl = Table(show_header=False, box=None, padding=(0, 1))
    tbl.add_column(style="dim")
    tbl.add_column()

    tbl.add_row("server", str(snap.get("server_url", "?")))

    gov = snap.get("gov") or {}
    if gov:
        tot = gov.get("total", 0)
        rate = gov.get("violation_rate", 0)
        tbl.add_row("gov", f"audited={tot} \u00b7 violation_rate={rate * 100:.1f}%" if isinstance(rate, (int, float)) else f"audited={tot}")

    yolo = snap.get("yolo") or {}
    if yolo:
        tbl.add_row("approval", "AUTO (yolo)" if yolo.get("yolo") else "ASK")

    proac = snap.get("proac") or {}
    if proac:
        tbl.add_row("proactivity", str(proac.get("level", "?")))

    console.print(tbl)
    return 0
