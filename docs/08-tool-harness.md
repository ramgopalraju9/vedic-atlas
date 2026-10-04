# 08 — Tool harness (tasks, weather, currency, web search)

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
| Skills | `service/skills/manifest_skill.py` + `builtin/{tasks,weather,currency,web_search}.py` |
| Online lookups | `service/lookup/{lookup_service,place_resolver,weather_lookup,currency_lookup,search_lookup,ttl_cache}.py` |
| Providers | `tpa/online/providers/{weather,geocode,fx,tavily}.py` via `tpa/online/http_client.py` (allow-list, no redirects, `[http]` log) |
| Traces | `domain/entities/turn_trace.py`, `tpa/persistence/repositories/trace_repository.py`, `GET /api/trace`, `veda trace` |
| Health | `service/lookup/health_service.py`, `GET /api/lookup/health`, `veda doctor` |

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
