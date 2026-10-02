"""Streaming output renderer — TTY-aware.

Donor: veda/cli/render.py, read in full. Hint text genericized: the
donor blamed an expired claude/copilot CLI session specifically; this
build has no cloud CLI to blame, so the message points at the local
inference backend instead.

In a TTY: token-by-token live update with a final markdown re-render so
code fences / lists / headings look right.
In a pipe: raw text dumped as-is so callers can compose with shell tools.
"""

from __future__ import annotations

import sys
from typing import Iterable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

_EMPTY_HINT = (
    "[the model returned no content. usually means the local inference "
    "backend (ollama/llama.cpp) isn't running or isn't reachable — check "
    "the server log for details.]"
)


def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def stream_to_terminal(tokens: Iterable[str], *, console: Console | None = None) -> str:
    """Render an SSE token stream live, then re-render as markdown.

    Returns the full accumulated text. When the stream completes with no
    content, prints an actionable hint rather than silently producing
    nothing.
    """
    c = console or Console()
    if not _is_tty():
        full = ""
        for tok in tokens:
            full += tok
            sys.stdout.write(tok)
            sys.stdout.flush()
        if not full.strip():
            sys.stdout.write(_EMPTY_HINT + "\n")
        else:
            sys.stdout.write("\n")
        return full

    full = ""
    text = Text()
    with Live(text, console=c, refresh_per_second=24, transient=True) as live:
        for tok in tokens:
            full += tok
            text.append(tok)
            live.update(text)

    if full.strip():
        c.print(Markdown(full))
    else:
        c.print(f"[yellow]{_EMPTY_HINT}[/yellow]")
    c.print()
    return full