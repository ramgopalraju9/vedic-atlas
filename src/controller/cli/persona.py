"""`veda persona` subcommand implementation.

Donor: veda/cli/persona.py, copied verbatim except the client import path.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from controller.cli.client import VedaClient


def cmd_persona(client: VedaClient, *, set_to: str | None, list_all: bool) -> int:
    console = Console()
    try:
        snap = client.get_persona()
    except Exception as e:
        console.print(f"[red]persona request failed:[/red] {e}")
        return 1

    state = snap.get("state") or {}
    catalog = snap.get("catalog") or []

    if list_all:
        tbl = Table(title="Personas", show_lines=False)
        tbl.add_column("id", style="cyan")
        tbl.add_column("label")
        tbl.add_column("proactivity", style="dim")
        tbl.add_column("tagline")
        for p in catalog:
            tbl.add_row(p["id"], p["label"], p["proactivity"], p["tagline"])
        console.print(tbl)
        return 0

    if set_to is None:
        cur = state.get("persona") or "unset"
        completed = state.get("completed", False)
        console.print(f"current persona: [bold]{cur}[/bold]")
        console.print(f"onboarding complete: {completed}")
        return 0

    try:
        r = client.set_persona(persona=set_to, completed=True)
    except Exception as e:
        console.print(f"[red]set persona failed:[/red] {e}")
        return 1
    new_state = r.get("state") or {}
    console.print(f"[green]persona set to {new_state.get('persona')}[/green] (proactivity seeded)")
    return 0