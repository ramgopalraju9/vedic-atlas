# Router strategy: how Veda picks an agent (current state)

Describes what the code does today on branch `release/v1.1`. No changes proposed here.

## 1. What routing does

Every message (typed or spoken) goes to `SupervisorAgent`. Its only routing job is to pick
**one** specialist agent for that message.

| Agent | Handles | Has tools |
|---|---|---|
| `responder` | chat, jokes, advice, explanations, maths, "what do I know about X" | no (default agent) |
| `lookup` | weather, currency, news, prices, scores, web search | `get_weather`, `convert_currency`, `web_search` |
| `tasks` | to-do list: add, list, complete | `tasks` |
| `memory` | save or forget a lasting fact about the user | `remember` |
| `system` | open/close apps, volume, CPU, processes | SystemAgent actions |

## 2. The two routing tools

1. **Keyword rules** (instant, no model): `domain/policies/routing_policy.py`
   - Step A, **triggers**: each tool manifest (`config/tools/*.yaml`) declares regex `triggers`. The agent whose triggers match most wins. Ties go to the earlier-registered agent.
   - Step B, **word overlap**: if no trigger matched, compare the message's words (stopwords removed) with each agent's description. At least 1 shared word wins.
   - If neither matches, the result is "no match".
2. **Router model** (a small LLM call): `service/agent/llm_router.py`
   - One tiny call per message, output forced to `{"agent": "<name>"}` by a JSON schema, so it cannot invent an agent.
   - Prompt: `config/prompts/router.md` (agent list, a rule sheet and dozens of examples).
   - Model: `data/Qwen3-0.6B-Q4_K_M.gguf` (separate from the main model). Set `model_path: null` to use the main model instead.
   - Temperature 0, `num_predict: 16`.

## 3. The three modes (`config/routing.yaml`)

| Mode | Who decides | Model used? |
|---|---|---|
| `keyword` | rules only. No match goes to `responder` | never loaded |
| `model` | router model decides every message. Rules are only the fallback | every message |
| `hybrid` (**active today**) | rules decide **if sure**, otherwise the router model decides | only for unsure messages |

**"Sure" in hybrid mode** means all of these are true:
- the match came from a trigger (not just word overlap),
- exactly **one** agent's triggers matched,
- the sentence has no correction cue ("I didn't ask about...", "forget it", "instead of", "never mind", "stop").

Everything else is "unsure": no match, weak overlap, two agents claimed it ("current tasks" hits both tasks and web search), or a negation.

## 4. Flow (hybrid, as configured)

```
message
  |
  v
keyword rules  --- sure? (1 agent, trigger, no negation) --- yes --> that agent
  |
  no
  v
router model (queue wait <= 30 s, run time <= 8 s)
  |-- answers --> that agent
  |-- no answer (timeout / error / paused / missing model)
        v
     on_failure: keyword --> rules' pick, else responder     (current setting)
     on_failure: chat    --> responder
```

After the agent is chosen: governance may veto the route (`route_to_agent` policy), the choice
is recorded in memory, then the agent runs. A tool agent that decides no tool is needed hands
the turn to `responder`, and any "I did X" claim in that reply is refused if no tool ran.

## 5. Safety valves

| Mechanism | Behaviour |
|---|---|
| Slow-device pause | If the router model **runs** longer than `timeout_sec` (8 s), model routing pauses for **10 minutes**; rules only meanwhile. |
| Queue wait | Time spent waiting behind the main model does **not** trigger the pause. After `queue_wait_sec` (30 s) that one message uses the fallback. |
| Warm-up | At boot the router prompt is evaluated once so the first real message is not the slow one. |
| Visibility | Each message logs `[route] mode=... via=... agent=...`. `GET /api/health` shows mode, whether a router model is loaded, and seconds left of the pause (status becomes `degraded` while paused). |
| Short follow-ups | Messages of 6 words or fewer also get the recent conversation turns in the router prompt ("and in Mumbai?" follows the previous specialist). |
| Order bias fix | `responder` is listed last in the prompt and in the answer enum, because a small model leaned toward the first option. |

## 6. Where it is configured

| Setting | File | Current value |
|---|---|---|
| `mode` | `config/routing.yaml` | `hybrid` |
| `on_failure` | `config/routing.yaml` | `keyword` |
| router model path / ctx / timeouts | `config/routing.yaml` `model:` | Qwen3-0.6B, `n_ctx 2560`, 8 s run, 30 s queue |
| triggers | `config/tools/*.yaml` | per tool |
| agent descriptions (overlap + router prompt) | `config/agents.yaml` | per agent |
| main model profile | `.env` `VEDA_PROFILE` | `qwen3-4b` |
| legacy flag | `config/agents.yaml` `llm_routing` | `false` (deprecated; only acts when `mode: keyword`) |

## 7. Code map

| Piece | File |
|---|---|
| Decision logic (`_pick`) | `service/agent/supervisor.py` |
| Rules, "sure" check | `domain/policies/routing_policy.py` |
| Thin adapter over the rules | `service/agent/router_policy.py` |
| Router model client | `service/agent/llm_router.py` |
| Port | `domain/ports/router_port.py` |
| Router prompt | `config/prompts/router.md` |
| Wiring (`_build_router`) | `server.py` |
| Tests | `tests/test_routing_modes.py`, `tests/test_golden_routing.py`, `tests/test_llm_routing.py`, `tests/test_router_timeouts.py` |
| Golden set (219 lines) | `tests/eval/golden_set.yaml` |

## 8. Observed weaknesses (from reading the code and running the tests)

Not fixes, just facts to decide on later.

1. **A confident rule can be wrong, and hybrid trusts it.** `who are you` matches the web-search trigger `who (won|is|was|are)`, so it is "sure" and goes to `lookup` without the model being asked. The golden test expects `responder`.
2. **Gaps in the rules push work to the model.** `is it hot in Jaipur today`, `is it sunny in Goa`, `is it cloudy outside`, `do I need an umbrella today` match no weather trigger (only `how hot/cold/warm`, `rain`, `windy`...), so in hybrid they depend on the small model.
3. **Rules drifted from the test set.** `is bitcoin going up today` is marked as a model-only case in the golden set, but a rule now matches it. Together with 1 and 2, this is why **6 golden-routing tests fail today** (before and after the v1.1 trim, so unrelated to it).
4. **Negation detection is regex-based.** It catches common phrasings only.
5. **Cost on the Pi.** `agents.yaml` notes the routing call took 30 s+ on a Pi earlier, which is why LLM routing was off for v1. Current hybrid mode relies on the slow-device pause to protect against that; after a timeout, the next 10 minutes of unsure messages use the keyword fallback.
6. **A separate router model plus the main model** means two llama.cpp contexts in RAM; they share one lock, so a routing call can queue behind a long reply.
7. **Only one agent per message.** "Add milk to my list and what's the weather" goes to a single agent.
