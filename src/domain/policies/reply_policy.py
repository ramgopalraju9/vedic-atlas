"""ReplyPolicy — how each executed call becomes part of the reply. Pure.

  failed call                         -> FAILED   deterministic failure text; a result is never invented
  template tool, or `final` result    -> SPOKEN   the tool's own sentence, kept verbatim and NEVER sent to the model
  anything else (llm / document)      -> NARRATE  handed to the content stage, which sees only these results

So the number of content decodes does not scale with the number of calls: all NARRATE results share one.
"""

from domain.entities.tool_manifest import REPLY_TEMPLATE

SPOKEN = "spoken"
NARRATE = "narrate"
FAILED = "failed"

FAIL_REPLY = "I couldn't do that: {error}"
UNCONFIRMED_REPLY = "I can't confirm that I did that. Could you say it again?"


def classify_call(*, ok: bool, reply_mode: str, final: bool) -> str:
    if not ok:
        return FAILED
    if reply_mode == REPLY_TEMPLATE or final:
        return SPOKEN
    return NARRATE


def failure_text(error: str | None) -> str:
    return FAIL_REPLY.format(error=error or "blocked")


def spoken_text(spoken: str | None, observation: str) -> str:
    """The sentence for a SPOKEN call: the tool's own text, else the first line of its output."""
    if spoken:
        return spoken
    return observation.splitlines()[0] if observation else "Done."
