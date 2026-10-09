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


def _print_final(c: Console, full: str) -> None:
    """Print the finished answer. Veda's replies are plain spoken sentences, so a one-line reply is printed as plain text:
    Markdown reads a bare "2." or "10." (an answer to a sum) as an EMPTY numbered-list item and shows nothing at all.
    Multi-line or fenced replies still get Markdown (code, lists), and if that renders to nothing the plain text is shown."""
    text = full.strip()
    if "\n" in text or "```" in text:
        with c.capture() as captured:
            c.print(Markdown(full))
        if captured.get().strip():
            c.print(Markdown(full))
            return
    c.print(Text(text))


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
        _print_final(c, full)
    else:
        c.print(Text(_EMPTY_HINT, style="yellow"))   # not markup: the hint starts with "[" and Rich would swallow it
    c.print()
    return full