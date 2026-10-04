# 08 — Tool harness (tasks, weather, currency, web search, remember)

How Veda calls tools reliably on a small quantized local model (Qwen3-4B Q4, CPU, n_ctx 4096).

## Principles
1. **The model does the minimum**: pick a tool, fill 1-3 flat arguments. Code does the rest.
2. **Each prompt carries only what its stage needs** (measured: persona 563 tok vs persona_lite 65 tok; call stage ~340-560 tok).
3. **Output is constrained**: llama.cpp grammar from a JSON schema — no malformed JSON, no unknown tool/argument.
4. **Facts come only from tool results**; replies for weather/currency/tasks (and search) are built from the tool's own data.
5. **One source of truth per tool**: `config/tools/<name>.yaml`.

## Turn flow
```
user text -> Supervisor: manifest `triggers` / keyword rules (instant) -> agent;
              nothing matched -> LLM router (one constrained call, ~2 s warm) -> agent; failure -> chat
  -> ToolAgent (tasks | lookup)
     A. DECIDE   constrained JSON {"calls":[...]}  (persona_lite + this agent's tools + last 2 turns)
        FORCE    `required_when` matched but no call -> retry once with minItems=1
     B. EXECUTE  SkillRunner (permissions / rate limit / validators / audit hooks) — every call logged
     C. REPLY    spoken text from the tool itself (`final`/template) — or a short narrate call, then a
                 grounding check (numbers must appear in the tool result), else the deterministic text
  -> no tool needed -> plain chat (responder); a chat reply that claims an action is refused
  -> TurnTrace written (calls, results, timings, prompt tokens)
```

## Where things live
| Piece | Location |
|---|---|
| Tool manifests (name, params, examples, triggers, required_when, claims, reply_mode, cache TTL, hosts) | `config/tools/*.yaml` -> `schemas/tool_manifest_schema.py` -> `domain/entities/tool_manifest.py` |
| Prompts | `config/prompts/{persona_lite,call_stage,narrate}.md` via `tpa/filestore/file_prompt_store.py` |
| Prompt assembly + token budgets | `service/prompting/prompt_composer.py`, `domain/policies/token_budget_policy.py` |
| JSON schema for constrained decoding | `domain/policies/tool_call_schema.py` (+ `json_schema` on `InferencePort.complete`) |
| Routing | `domain/policies/routing_policy.py` (`AgentProfile.triggers`) |
| Staged turn | `service/agent/tool_turn_runner.py`, `service/agent/tool_agent.py`, `service/agent/tool_use_guard.py` |
| Grounding | `domain/policies/grounding_policy.py` |
| Skills | `service/skills/manifest_skill.py` + `builtin/{tasks,weather,currency,web_search,remember}.py` |
| Online lookups | `service/lookup/{lookup_service,place_resolver,weather_lookup,currency_lookup,search_lookup,ttl_cache}.py` |
| Providers | `tpa/online/providers/{weather,geocode,fx,tavily}.py` via `tpa/online/http_client.py` (allow-list, no redirects, `[http]` log) |
| Traces | `domain/entities/turn_trace.py`, `tpa/persistence/repositories/trace_repository.py`, `GET /api/trace`, `veda trace` |
| Health | `service/lookup/health_service.py`, `GET /api/lookup/health`, `veda doctor` |

## The remember tool
`config/tools/remember.yaml` (owner agent `memory`) keeps lasting facts about the user: preferences, allergies, names,
routines. Actions: `save` (topic + value), `forget` (topic), `list`.

- **Stored as** one line per fact, `favourite sweet: gulab jamun`, in `data/knowledge.json` (the existing
  `KnowledgeBase`; `domain/policies/fact_policy.py` defines the shape). The topic is the key: "my fav sweet",
  "my favorite sweet" and "favourite sweet" are the same topic, so a new value *replaces* the old one and the reply says
  what it replaced.
- **Always in the chat prompt.** `ResponderAgent.build_prompt` includes every saved fact on every turn, then adds
  semantically recalled conversation summaries when relevant (recall now searches summaries only, `config/embedding.yaml`).
  So a question ("what's my favourite sweet?") is answered from the prompt and needs no tool call.
- **Routing.** Statements ("my favourite sweet is X", "remember that I'm allergic to Z", "forget my favourite sweet",
  "what do you remember about me?") match the manifest triggers; looser phrasings ("I only eat vegetarian food") go
  through the LLM router. `required_when` forces a real call for the explicit forms, and `claims` stops a reply such as
  "I'll remember that" when nothing was saved. Replies are templates built from what was written.
- **Limits.** Max 40 facts (`MAX_FACTS`), topic <= 60 and value <= 200 characters. Passwords, PINs, card and account
  numbers are refused. Nothing is saved unless the user said it: no automatic extraction from conversation.
- **Known limit.** The LLM router sometimes sends a *question* about a saved fact ("what is my favourite food?") to
  `memory`; the tool then lists all saved facts, which is grounded but not a direct answer. Measured on the golden set:
  31/32 LLM-routed cases correct, 0 chat messages sent to a tool.

## Adding a tool
1. Write `config/tools/<name>.yaml` (description <= 25 words, <= 6 examples, triggers, required_when).
2. Write a `ManifestSkill` subclass (`run()` only), register it in `server.py`.
3. Add golden-set rows in `tests/eval/golden_set.yaml`; run `python scripts/eval_tools.py --model`.

## Online tools
- Weather: Open-Meteo (place -> coordinates via its geocoder; unknown place -> web-search coordinates, validated; provider down -> labelled-approximate web-search answer).
- Currency: Frankfurter (ECB, ~30 currencies) with open.er-api.com fallback (166 currencies; attribution: https://www.exchangerate-api.com).
- Search: Tavily. Key in `.env` as `TAVILY_API_KEY` (git-ignored). News questions speak the top dated headlines; general questions speak the provider's answer.
- `privacy.online.enabled: false` removes all online tools.

## Operating it
- `veda doctor` — live probe of every provider (geocoding, weather, currency, web_search incl. key presence).
- `veda trace [n]` / `/trace` — what each tool turn really did.
- `python scripts/eval_tools.py [--model]` — golden-set accuracy (routing is also a CI test).
- `python scripts/measure_prompt_budget.py [--speed]` — exact token counts per prompt file.
- Log tags: `[tool-turn] [tool-call] [tool-guard] [lookup] [http] [place] [fx] [health]`.

## Known limits
- Weather is current conditions only (no forecast/rain probability yet).
- Follow-up fragments ("and in Mumbai?") route via the LLM router, which sees the recent turns for messages of <= 6 words; this is not covered by the eval.
- "I need to ..." creates a task by design, so "I need to open chrome" is routed to tasks.
- CPU latency: ~3-15 s per tool turn (decide stage dominates); first request after boot is slower (cold prefix cache).
- **Slow devices (Raspberry Pi).** The LLM router costs ~1-3 s on a laptop but can exceed its 30 s timeout on a Pi
  (about 380 prompt tokens to read, cold). Two safeguards: a timeout makes the router pause itself for 10 minutes
  (rules only, logged as `[route] llm routing took longer than 30s - too slow on this device`), and
  `LlamaCppClient` holds a thread lock around every decode, because a timed-out call's thread keeps running and a
  second decode on the same llama.cpp context crashes the server (the CLI then shows "peer closed connection ...
  incomplete chunked read"). The reply that follows a timed-out call therefore waits for it to finish. On a Pi where
  the router is never fast enough, set `agents.llm_routing: false` in `config/agents.yaml`.
