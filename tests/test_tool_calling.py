import asyncio

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from service.agent.tool_calling import ToolCallingLoop


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

    async def complete(self, self_prompt, prompt, system="", model=None, **kw):
        self.prompts.append(prompt)
        return self._scripted.pop(0)


def _loop(client, runner, max_iterations=3):
    return ToolCallingLoop(
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