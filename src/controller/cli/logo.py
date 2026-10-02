"""ASCII intro banner for the Veda CLI.

Donor: veda/cli/logo.py, copied verbatim except the `VERSION` import path.
Rendered with :mod:`rich` so colors degrade cleanly when piped. The state
glyph (cyan idle / amber thinking / rose alert) mirrors the avatar dais in
the Angular UI — same visual language, terminal-flavored.

On Windows terminals without UTF-8 the block-letter title degrades to a
plain-ASCII fallback so we never print mojibake.
"""

from __future__ import annotations

from typing import Literal

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich import box

from core.constants import VERSION

State = Literal["idle", "thinking", "speaking", "alert"]

_STATE_GLYPHS: dict[State, tuple[str, str, str]] = {
    # (glyph, label, color)
    "idle":     ("\u25cd", "idle",     "cyan"),
    "thinking": ("\u25cc", "thinking", "yellow"),
    "speaking": ("\u25cc", "speaking", "magenta"),
    "alert":    ("\u25cf", "alert",    "red"),
}

# ANSI Shadow figlet style — the canonical "I am a terminal app" block letters.
# Six rows tall, ~50 cols wide. Falls back to plain block ASCII on cp1252.
_TITLE_UTF8 = [
    "\u2588\u2588\u2557   \u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2557 ",
    "\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557",
    "\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551",
    "\u255a\u2588\u2588\u2557 \u2588\u2588\u2554\u255d\u2588\u2588\u2554\u2550\u2550\u255d  \u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2551",
    " \u255a\u2588\u2588\u2588\u2588\u2554\u255d \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2551  \u2588\u2588\u2551",
    "  \u255a\u2550\u2550\u2550\u2554\u255d  \u255a\u2550\u2550\u2550\u2550\u2550\u2550\u255d\u255a\u2550\u2550\u2550\u2550\u2550\u255d \u255a\u2550\u2551  \u255a\u2550\u255d",
]

_TITLE_ASCII = [
    "##   ## ######## ######    #####  ",
    "##   ## ##       ##  ##   ##   ## ",
    "##   ## #####    ##  ##  ######### ",
    " ## ##  ##       ##  ##  ##     ## ",
    "  ###   ######## ######  ##     ## ",
]

# Smooth gradient across cyan -> blue -> magenta — six rows so each one gets a
# distinct shade. Reads as a single coherent thing rather than a noisy rainbow.
_TITLE_GRADIENT = [
    "bold #00d7ff",   # cyan
    "bold #00afff",
    "bold #5fafff",
    "bold #875fff",
    "bold #af5fff",
    "bold #d75fff",   # magenta
]


def _is_utf8(console: Console) -> bool:
    enc = (getattr(console, "encoding", None) or "").lower().replace("-", "")
    return enc in ("utf8", "utf16", "utf32")


def _state_indicator(state: State) -> Text:
    """Glyph + label, e.g. ``idle``. Used inside the status panel."""
    glyph, label, color = _STATE_GLYPHS[state]
    out = Text()
    out.append(glyph, style=f"bold {color}")
    out.append(f"  {label}", style=f"{color}")
    return out


def _render_title(console: Console) -> Group:
    """6-row block-letter VEDA with a cyan->magenta gradient. Falls back to
    plain ASCII art on non-UTF-8 terminals."""
    rows = _TITLE_UTF8 if _is_utf8(console) else _TITLE_ASCII
    lines: list[Text] = []
    for i, row in enumerate(rows):
        style = _TITLE_GRADIENT[i] if i < len(_TITLE_GRADIENT) else _TITLE_GRADIENT[-1]
        lines.append(Text(row, style=style))
    tagline = Text.from_markup(f"  [dim]personal AI companion \u00b7 v{VERSION}[/dim]")
    return Group(*lines, Text(""), tagline)


def render_banner(
    *,
    persona: str | None,
    server_url: str,
    brain: str,
    warm_pool: bool,
    state: State = "idle",
    console: Console | None = None,
) -> None:
    """Print the intro banner. Cheap; no LLM call, no network.

    Persona is optional — pre-onboarding it shows ``(unset)`` and a hint.
    """
    c = console or Console()

    persona_str = persona or "[dim](unset)[/dim]"
    warm = "on" if warm_pool else "off"

    state_row = _state_indicator(state)
    persona_brain = Text.from_markup(f"   persona: [bold]{persona_str}[/bold]   \u00b7   brain: [bold]{brain}[/bold]")
    server_row = Text.from_markup(f"   server:  [link]{server_url}[/link]")
    warm_row = Text.from_markup(f"   warm-pool: [bold]{warm}[/bold]")

    status = Group(Text("  ").append(state_row), persona_brain, server_row, warm_row)

    status_panel = Panel(
        status, box=box.HEAVY, border_style="cyan",
        title="[bold cyan]status[/bold cyan]", title_align="left",
        padding=(0, 1), expand=False,
    )

    c.print()
    c.print(_render_title(c))
    c.print()
    c.print(status_panel)
    c.print()
    c.print(Text.from_markup("   [dim]/help for commands  \u00b7  type a message  \u00b7  Ctrl-D to quit[/dim]"))
    c.print()


def status_line(*, persona: str | None, brain: str) -> Text:
    """Compact status line shown above the REPL prompt each turn.

    Format: ``[persona: developer · brain: builtin]``. Donor also showed
    a staged-image count here; dropped along with image attachments.
    """
    parts: list[str] = [f"persona: {persona or 'unset'}", f"brain: {brain}"]
    body = "  \u00b7  ".join(parts)
    return Text.from_markup(f"[dim]\\[{body}][/dim]")