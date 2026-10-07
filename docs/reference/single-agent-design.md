# Design: one agent, no router

Status: **in progress.** Decisions are added one at a time. No code written.
Current behaviour is described in `router-strategy.md`.

## Idea

Today a message is first routed (rules, then a small router model) to one of five agents, and a
tool agent then asks the model again which tool to call. The tool decision already exists, so the
routing step is a second decision on top of it.

New shape: the message goes straight to **one agent that holds all the tools**. The model decides,
inside a single generation, whether to answer directly or to call a tool.

```
message --> single agent --(model decides)--> plain reply
                                  \--> tool call --> tool runs --> reply
```

## Decisions made

| # | Decision | Notes |
|---|---|---|
| 1 | Remove the router completely | no rules, no router model, no `routing.yaml` |
| 2 | **One model call for plain chat** | the model either answers or emits a tool call in the same generation; chat does not pay an extra "decide" call |
| 3 | **No forced-retry safety net** | `required_when` forcing is dropped; enforcement comes from strict prompts and tool descriptions |
| 4 | **Keep the false-claim check** | a reply such as "Task added" with no tool call is replaced with "I can't confirm that I did that". Regex only, no model call |
| 5 | **No `system` agent** | open/close app, volume, CPU, processes are removed, not converted |
| 6 | Web search is for live data only | stocks, petrol, gold, silver, other rates and prices; not for things the model already knows |
| 7 | **Qwen's native tool-call format** | the model replies with `<tool_call>{...}</tool_call>` or plain text; with decision 11 LangChain's `bind_tools` / `ChatLlamaCpp` does the wiring and parsing |
| 8 | **Five tools** | `tasks`, `remember`, `get_weather`, `convert_currency`, `web_search` (live data only) |
| 9 | **Qwen3-4B is the supported model** | design, prompts and tests target the 4B. 0.6B / 1.7B profiles stay in the repo but are not guaranteed to call tools reliably |
| 10 | **Replace in place, on a new branch** | branch from `release/v1.1`; no config flag, no two designs side by side |
| 11 | **Build the agent on LangChain** | `create_agent` with skills as tools; the rest of our harness (memory, guardrails, governance, history) is attached to it. LangChain code lives in a `tpa/` adapter behind a port, per the layering rule |
| 12 | **Skills are plain tools, no progressive disclosure for now** | all five tool descriptions are in every prompt; no "load skill" step. Revisit when there are many skills |
| 13 | **Wrap each tool, keep our services** | every LangChain tool calls `SkillRunner` (permissions, rate limits, validators, audit still fire). Our code builds history, saved facts and summaries into the prompt; LangChain runs only the model/tool loop, with no LangChain memory or checkpointer |
| 14 | **Memory is read by pushing it into the prompt** | saved facts always included, relevant summaries found by embedding search before the model call, recent turns, time. Writing stays as tools (`remember`, `tasks`). A `recall_memory` tool is added later only if facts outgrow the prompt |
| 15 | **Prompt order for the prefix cache, and a hard cap on pushed memory** | order: persona, tool descriptions, saved facts, then history / time / message. Facts + recalled summaries get a fixed token budget, set after measuring the real prompt with all five tools (`scripts/measure_prompt_budget.py`). If facts exceed the cap, add a `recall_memory` tool |

## What this removes (to verify with grep before deleting)

- `service/agent/llm_router.py`, `router_policy.py`, `domain/policies/routing_policy.py`, `domain/ports/router_port.py`, `domain/entities/route_decision.py`, `build_route_schema`
- routing modes and `_pick` in `supervisor.py`, `routing_status` in `/api/health`, `warm_router`
- `config/routing.yaml`, `RoutingConfig`, `config/prompts/router.md`, the Qwen3-0.6B router model file requirement
- `service/agent/system.py` and its use of `SystemControlPort` (and the Windows/Linux system adapters if nothing else uses them)
- governance policy `agent_routing.yaml` (it gates `route_to_agent`)
- the routing tests (`test_golden_routing`, `test_routing_modes`, `test_llm_routing`, `test_router_timeouts`); this also clears the 6 failing golden tests

## What stays

`ResponderAgent` prompt pieces (persona, saved facts, recalled summaries, history, voice delivery
note), the tool manifests and skills, `SkillRunner` with its guardrail hooks, the grounding check on
narrated numbers, traces, conversation memory, the false-claim check.

## Open questions (one at a time)

1. ~~Output protocol~~ decided (7). Still to verify with a spike: `llama-cpp-python` 0.3.36 (`create_chat_completion`, used by `LlamaCppClient`) renders tools through the GGUF chat template, but we must parse `<tool_call>` ourselves and detect it in the first streamed tokens so plain text can go to voice immediately. `InferencePort.stream()` would need to carry tool calls, not only text.
2. ~~Tool set~~ decided (8). Still to write: the strict tool descriptions, especially the web_search one ("live data only").
3. `memory` and `tasks` agents become plain tools (default, no decision needed). Confirm by grep that nothing else depends on the agent names.
4. ~~Model floor~~ decided (9).
5. Traces: default is a single agent name `assistant`; `decided` records tool vs no-tool. `veda trace` output stays the same shape.
6. ~~Migration~~ decided (10). Work order below.

## Known risks with decision 11

- `ChatLlamaCpp` tool-call parsing is reported to lag newer chat templates; Qwen3 may need a workaround (custom parser, or a patched chat handler). Verify first.
- Progressive disclosure (loading a skill is itself a tool call, an extra model turn) is NOT used for now (decision 12).
- LangChain's install size and import time on the Pi; our hooks (`SkillRunner`, permissions, governance, audit) must be attached as middleware or tool wrappers.

## Work order (proposed)

1. **Spike (laptop, then Pi):** Qwen3-4B through LangChain `create_agent` + `ChatLlamaCpp` with the five tool definitions. Check: tool call is produced for live-data and task messages, none for chat, `<tool_call>` can be detected in the first streamed tokens, latency on the Pi.
2. New branch `feature/single-agent` from `release/v1.1`.
3. Extend `InferencePort` so a stream can carry either text or a tool call.
4. New single agent: persona + strict tool descriptions + tool loop (call, run through `SkillRunner`, reply) + false-claim check.
5. Wire it in `server.py`; remove the router, routing config, `system` agent and their tests.
6. Replace the golden routing test with a tool-call eval (`tests/eval/golden_set.yaml` and `scripts/eval_tools.py` already exist).
7. Update docs; Pi verification.
