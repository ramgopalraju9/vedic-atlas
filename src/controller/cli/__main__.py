"""Veda CLI entry-point.

Donor: veda/cli/__main__.py, read in full. Dropped: the `run` subcommand
(followed the code-runner's RUNNER_OUTPUT stream — out of scope), the
`--image`/inline `@path` image-attachment handling in the default chat
path (vision out of scope; `extract_image_refs` lived in the now-dropped
`parser.py`). `--pipe` (stdin-as-extra-context) is kept — that's plain
text piping, unrelated to images.

Dispatches subcommands under one binary so the user types ``veda <verb>``
instead of remembering many tiny scripts. Defaults: bare ``veda`` opens
the REPL when stdin is a TTY; ``veda "<prompt>"`` is one-shot streaming
chat; ``veda server`` runs the FastAPI app.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

from rich.console import Console

from controller.cli.client import CliError, open_client
from controller.cli.logo import render_banner
from controller.cli.commands import cmd_approve, cmd_config, cmd_status
from controller.cli.persona import cmd_persona
from controller.cli.render import stream_to_terminal
from controller.cli.repl import Repl
from controller.cli.server import cmd_server
from core.logging_config import logger


# ---------- argparse setup ----------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="veda",
        description="Veda terminal client. Type `veda` for the REPL or `veda \"<prompt>\"` for a one-shot.",
        add_help=True,
    )
    p.add_argument("--server-url", help="Override server URL (default http://127.0.0.1:8000)")
    p.add_argument("--no-spawn", action="store_true", help="Don't auto-start the server when not reachable")

    sub = p.add_subparsers(dest="cmd", required=False)

    p_hello = sub.add_parser("hello", help="Print the intro banner and exit (no LLM call).")
    p_hello.set_defaults(func=_handle_hello)

    p_server = sub.add_parser("server", help="Run the FastAPI server.")
    p_server.add_argument("--port", type=int, default=None)
    p_server.add_argument("--host", default="127.0.0.1")
    p_server.set_defaults(func=_handle_server)

    p_persona = sub.add_parser("persona", help="Show or set the active persona.")
    p_persona.add_argument("--set", dest="set_to", help="Switch to this persona")
    p_persona.add_argument("--list", action="store_true", help="List all personas")
    p_persona.set_defaults(func=_handle_persona)

    p_approve = sub.add_parser("approve", help="List or resolve pending approval requests.")
    p_approve.add_argument("--list", action="store_true", help="List pending and exit")
    p_approve.add_argument("--id", dest="approve_id", help="Approve a specific request by id")
    p_approve.add_argument("--deny", dest="deny_id", help="Deny a specific request by id")
    p_approve.set_defaults(func=_handle_approve)

    p_config = sub.add_parser("config", help="Toggle yolo / proactivity.")
    p_config.add_argument("key", help="yolo | proactivity")
    p_config.add_argument("value", help="on/off | conservative/medium/chatty")
    p_config.set_defaults(func=_handle_config)

    p_status = sub.add_parser("status", help="One-line snapshot of governance/approval state.")
    p_status.set_defaults(func=_handle_status)

    return p


# ---------- handlers ----------

def _handle_hello(args: argparse.Namespace) -> int:
    console = Console()
    with open_client(server_url=args.server_url) as client:
        if not client.is_up():
            render_banner(persona=None, server_url=client.server_url, brain="?", warm_pool=False, state="alert", console=console)
            console.print("[dim]server not reachable \u2014 `veda server` to start it[/dim]")
            return 0
        try:
            snap = client.get_persona()
            persona = (snap.get("state") or {}).get("persona")
        except Exception:
            persona = None
        try:
            r = client._http.get(f"{client.server_url}/api/governance/status", timeout=2.0)
            brain = (r.json() or {}).get("provider", "?") if r.status_code == 200 else "?"
        except Exception:
            brain = "?"
        render_banner(persona=persona, server_url=client.server_url, brain=brain, warm_pool=True, state="idle", console=console)
    return 0


def _handle_server(args: argparse.Namespace) -> int:
    return cmd_server(port=args.port, host=args.host)


def _handle_persona(args: argparse.Namespace) -> int:
    with open_client(server_url=args.server_url) as client:
        try:
            client.ensure_up(allow_spawn=not args.no_spawn)
        except CliError as e:
            print(str(e), file=sys.stderr)
            return 1
        return cmd_persona(client, set_to=args.set_to, list_all=args.list)


def _handle_approve(args: argparse.Namespace) -> int:
    with open_client(server_url=args.server_url) as client:
        try:
            client.ensure_up(allow_spawn=not args.no_spawn)
        except CliError as e:
            print(str(e), file=sys.stderr)
            return 1
        return cmd_approve(client, list_only=args.list, approve_id=args.approve_id, deny_id=args.deny_id)


def _handle_config(args: argparse.Namespace) -> int:
    with open_client(server_url=args.server_url) as client:
        try:
            client.ensure_up(allow_spawn=not args.no_spawn)
        except CliError as e:
            print(str(e), file=sys.stderr)
            return 1
        return cmd_config(client, args.key, args.value)


def _handle_status(args: argparse.Namespace) -> int:
    with open_client(server_url=args.server_url) as client:
        if not client.is_up():
            Console().print("[dim]server not reachable \u2014 `veda server` to start it[/dim]")
            return 0
        return cmd_status(client)


def _handle_default_chat(args: argparse.Namespace, prompt: str) -> int:
    """One-shot streaming chat — what you get when you type `veda "..."`."""
    console = Console()
    with open_client(server_url=args.server_url) as client:
        try:
            client.ensure_up(allow_spawn=not args.no_spawn)
        except CliError as e:
            console.print(f"[red]{e}[/red]")
            return 1
        try:
            tokens = client.stream_chat(prompt)
            stream_to_terminal(tokens, console=console)
        except KeyboardInterrupt:
            console.print("[dim]^C \u2014 cancelled[/dim]")
            return 130
        except Exception as e:
            console.print(f"[red]chat failed:[/red] {e}")
            return 1
    return 0


def _handle_repl(args: argparse.Namespace) -> int:
    with open_client(server_url=args.server_url) as client:
        try:
            client.ensure_up(allow_spawn=not args.no_spawn)
        except CliError as e:
            print(str(e), file=sys.stderr)
            return 1
        return Repl(client).run()


# ---------- main ----------

def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()

    raw = list(argv if argv is not None else sys.argv[1:])
    known_subs = {"hello", "server", "persona", "approve", "config", "status", "-h", "--help"}

    first_pos = next((a for a in raw if not a.startswith("-")), None)
    is_subcommand = first_pos in known_subs if first_pos else False

    if not raw and sys.stdin.isatty():
        ns, _ = parser.parse_known_args([])
        return _handle_repl(ns)

    if not raw and not sys.stdin.isatty():
        ns, _ = parser.parse_known_args([])
        text = sys.stdin.read().strip()
        if not text:
            parser.print_help()
            return 2
        return _handle_default_chat(ns, text)

    if first_pos and not is_subcommand:
        chat_p = argparse.ArgumentParser(prog="veda", add_help=False)
        chat_p.add_argument("--server-url")
        chat_p.add_argument("--no-spawn", action="store_true")
        chat_p.add_argument("--pipe", action="store_true")
        chat_p.add_argument("prompt", nargs="+")
        ns = chat_p.parse_args(raw)
        prompt = " ".join(ns.prompt)
        if ns.pipe and not sys.stdin.isatty():
            stdin_text = sys.stdin.read().strip()
            if stdin_text:
                prompt = f"{prompt}\n\n{stdin_text}"
        return _handle_default_chat(ns, prompt)

    args = parser.parse_args(raw)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.exception("CLI command failed")
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())