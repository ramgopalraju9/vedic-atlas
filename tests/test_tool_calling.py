import asyncio
import json

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from service.agent.tool_calling import ToolCallingLoop
from service.agent.tool_use_guard import TASK_GUARD


class _Registry:
    def get_prompt_descriptions(self, names):
        return "- file_ops: read/write files"


class _Runner:
    def __init__(self):
        self.calls = []

    async def execute_skill(self, ctx, name, **params):
        self.calls.append((name, params))
        return SkillResult(skill_name=name, success=True, output="file contents")


class _Client:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.prompts = []

    async def complete(self, prompt, system="", model=None, **kw):
        self.prompts.append(prompt)
        item = self._scripted.pop(0)
        return item if isinstance(item, str) else json.dumps(item)


def _loop(client, runner, max_iterations=3, guard=None):
    return ToolCallingLoop(
        guard=guard,
        client=client,
        skill_runner=runner,
        skill_registry=_Registry(),
        tool_names=["file_ops", "terminal"],
        max_iterations=max_iterations,
    )


def test_tool_then_final():
    client = _Client([{"tool": "file_ops", "args": {"op": "read"}}, {"final": "done: file contents"}])
    runner = _Runner()
    out = asyncio.run(_loop(client, runner).run(AgentContext(user_message="read it"), "SYS", "read it"))
    assert out == "done: file contents"
    assert runner.calls == [("file_ops", {"op": "read"})]
    assert "OBSERVATION: file contents" in client.prompts[1]


def test_plain_text_is_final():
    client = _Client(["Just a direct answer."])
    out = asyncio.run(_loop(client, _Runner()).run(AgentContext(user_message="hi"), "SYS", "hi"))
    assert out == "Just a direct answer."


def test_unknown_tool_fed_back_then_final():
    client = _Client([{"tool": "nope", "args": {}}, {"final": "ok"}])
    runner = _Runner()
    out = asyncio.run(_loop(client, runner).run(AgentContext(user_message="x"), "sys", "x"))
    assert out == "ok"
    assert runner.calls == []  # unknown tool never executed
    assert "ERROR: tool 'nope'" in client.prompts[1]


def test_max_iterations_cap():
    client = _Client([{"tool": "file_ops", "args": {}}] * 5)  # never returns final
    runner = _Runner()
    out = asyncio.run(_loop(client, runner, max_iterations=2).run(AgentContext(user_message="x"), "sys", "x"))
    assert "tool budget" in out
    assert len(runner.calls) == 2


def _guarded(client, runner):
    return _loop(client, runner, max_iterations=4, guard=TASK_GUARD)


def test_guard_nudges_claim_without_call_then_accepts_real_call():
    client = _Client([
        {"final": "Task added: bring vegies."},  # claimed, never called
        {"tool": "file_ops", "args": {"op": "add"}},
        {"final": "Added it."},
    ])
    runner = _Runner()
    ctx = AgentContext(user_message="I need to bring vegies")
    out = asyncio.run(_guarded(client, runner).run(ctx, "SYS", "I need to bring vegies"))
    assert out == "Added it."
    assert len(runner.calls) == 1
    assert "rejected because no tool call was made" in client.prompts[1]


def test_guard_blocks_persistent_false_claim():
    client = _Client([{"final": "Task added: milk."}, {"final": "Task added: milk."}])
    runner = _Runner()
    out = asyncio.run(_guarded(client, runner).run(AgentContext(user_message="add milk"), "SYS", "add milk"))
    assert "couldn't update your tasks" in out
    assert runner.calls == []


def test_guard_lets_plain_chat_through():
    client = _Client([{"final": "Doing well, thanks!"}])
    out = asyncio.run(_guarded(client, _Runner()).run(AgentContext(user_message="how are you"), "SYS", "how are you"))
    assert out == "Doing well, thanks!"


def test_guard_intent_without_call_passes_after_one_nudge():
    # "I got a headache" looks like an intent phrase but is just chat.
    client = _Client([{"final": "Sorry to hear that."}, {"final": "Sorry to hear that."}])
    msg = "I got to say I did enjoy lunch"
    out = asyncio.run(_guarded(client, _Runner()).run(AgentContext(user_message=msg), "SYS", msg))
    assert out == "Sorry to hear that."


def test_guard_rejects_success_claim_after_failed_call():
    class _FailRunner(_Runner):
        async def execute_skill(self, ctx, name, **params):
            self.calls.append((name, params))
            return SkillResult(skill_name=name, success=False, error="No open task matches 'x'")

    client = _Client([
        {"tool": "file_ops", "args": {}},
        {"final": "Done, marked it as done."},
        {"final": "Done, marked it as done."},
    ])
    out = asyncio.run(_guarded(client, _FailRunner()).run(AgentContext(user_message="x"), "SYS", "x"))
    assert "couldn't update your tasks" in out
