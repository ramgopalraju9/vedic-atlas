"""The terminal must never show a blank where Veda answered (a bare "2." used to render as an empty Markdown list item)."""

import io

import pytest
from rich.console import Console

from controller.cli import render


def _show(monkeypatch, tokens):
    monkeypatch.setattr(render, "_is_tty", lambda: True)
    out = io.StringIO()
    console = Console(file=out, force_terminal=True, width=70, color_system=None)
    render.stream_to_terminal(iter(tokens), console=console)
    return out.getvalue()


@pytest.mark.parametrize("tokens,expected", [
    (["2", "."], "2."), (["10."], "10."), (["1+1 is ", "2."], "1+1 is 2."), (["3)"], "3)"), (["- ", "ok"], "- ok"),
    (["Sure", "! 42."], "Sure! 42."), (["[bold]not markup[/bold]"], "[bold]not markup[/bold]"),
])
def test_one_line_answers_are_shown_exactly_as_the_model_said_them(monkeypatch, tokens, expected):
    assert expected in _show(monkeypatch, tokens)


def test_multi_line_and_code_answers_still_render_as_markdown(monkeypatch):
    shown = _show(monkeypatch, ["Steps:\n\n1. first\n2. second\n\n```python\nprint(1)\n```"])
    assert " 1 first" in shown and " 2 second" in shown and " print(1)" in shown   # rendered list + code block


def test_an_empty_stream_still_explains_itself(monkeypatch):
    assert "model returned no content" in _show(monkeypatch, [""])
