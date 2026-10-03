# Exception Handling

`src/exceptions/` defines the single custom exception type used across the
whole codebase, plus the four global FastAPI handlers that turn it (and
every other kind of exception) into one uniform response shape.

## Philosophy

A single custom exception (`AppException`) carries a `code` — a
`core.enums.ExceptionCode` member — that does two jobs at once: it selects
the HTTP status via a lookup table, and it's echoed back to the client in
the JSON body so callers can branch on `error.code` programmatically, not
just on the HTTP status number. Framework-level exceptions (FastAPI's
`RequestValidationError`, Pydantic's `ValidationError`) are normalized into
the *exact same* response envelope rather than left in FastAPI's default
error shape. Anything not explicitly anticipated falls through to a
generic, detail-free 500 — a deliberate defense against leaking internals
to the client — while still being fully logged server-side with a
traceback.

## `AppException` (`exceptions/exception.py`)

```python
AppException(class_name: str, code: str, error_message: ErrorMessage | None = None,
             message: str | None = None, **kwargs)
```

The message comes either from the explicit `message` kwarg, or by
formatting `error_message.value.format(**kwargs)` — e.g. raising with
`error_message=ErrorMessage.INFERENCE_TIMEOUT, timeout=120` renders "Local
model timed out after 120s" without every call site needing to hand-format
its own string. `class_name` and `code` are stored on the instance;
`__str__` returns `self.message`.

## `ExceptionCode` (`core/enums.py`) and its HTTP status mapping

The docstring on `ExceptionCode` states the intent directly: "One-to-one
with an HTTP status — see `exceptions/handlers.py` for the mapping table."

| `ExceptionCode` | HTTP Status |
|---|---|
| `VALIDATION_ERROR` | 422 |
| `NOT_FOUND` | 404 |
| `INFERENCE_ERROR` | 500 |
| `TIMEOUT_ERROR` | 504 |
| `SYSTEM_ERROR` | 500 |
| `CONFIG_ERROR` | 500 |
| `AGENT_ROUTING_ERROR` | 500 |
| `SKILL_EXECUTION_ERROR` | 500 |
| `SKILL_NOT_FOUND` | 404 |
| `PERMISSION_DENIED` | 403 |
| `RATE_LIMITED` | 429 |
| `APPROVAL_TIMEOUT` | 408 |
| `APPROVAL_REJECTED` | 403 |
| `EGRESS_DENIED` | 403 |
| `TOOL_UNAVAILABLE` | 503 |
| `COMMAND_BLOCKED` | *(no entry — falls through to 500)* |
| `PATH_BLOCKED` | *(no entry — falls through to 500)* |

`TOOL_UNAVAILABLE` is raised as `exceptions.exception.ToolUnavailableError` (an `AppException`
subclass) when an online tool's provider can't answer — HTTP error, timeout, rate limit or a missing
API key. It carries `status` (the upstream HTTP status, if any) and `not_configured`, so the
user-facing sentence can distinguish "it isn't set up" from "it didn't respond" and callers can
treat 404/422 as "unsupported input" rather than an outage. Tools never surface the exception to
the user: `ManifestSkill` converts it into a failed `SkillResult` with a plain spoken sentence.

`ErrorMessage` (also in `core/enums.py`) holds the ~15 templated message
strings consumed via `AppException`'s `error_message=...` + `**kwargs`
path, e.g. `ErrorMessage.EGRESS_DENIED = "Outbound call to '{host}' denied — not on the allow-list"`.

## Global handlers (`exceptions/handlers.py`)

`register_exception_handlers(app)` is called once in `server.py`,
immediately after `FastAPI(...)` construction, and registers four handlers:

1. **`RequestValidationError`** (FastAPI's own) → always 422. Flattens the
   full list of Pydantic validation errors into one or more `AppError`
   objects (`code=err["type"]`, `message=f"{err['msg']} - {location}"`
   where `location` is the dotted field path). This is itself a fix from
   the donor behavior, which collapsed a multi-error list down to just the
   first error.
2. **`AppException`** → status via `_STATUS_BY_CODE.get(exc.code, 500)`
   (table above). Donor behavior was everything-is-500 regardless of
   semantic meaning; this mapping is the correction.
3. **Pydantic `ValidationError`** → same flattening/422 logic as #1.
4. **Catch-all `Exception`** → always 500, generic message `"An
   unexpected error occurred"`, full traceback logged via
   `logger.error(..., exc_info=True)` but never sent to the client.

Individual routes can still `raise HTTPException(...)` directly for
route-local semantic errors (a missing resource, a bad enum value) — those
bypass all four handlers above and go through FastAPI's native
`HTTPException` handling, which has its own default JSON shape
(`{"detail": "..."}`), not the `AppResponse` envelope.

## The response envelope (`schemas/schemas.py`)

Every one of the four global handlers returns a `JSONResponse` whose body
is:

```python
AppResponse(error=AppError(code=..., message=...)).model_dump(mode="json", exclude_none=True)
```

```python
class AppError(BaseModel):
    code: str
    message: str

class AppResponse(BaseModel, Generic[T]):
    response: Optional[T] = None
    error: Optional[AppError] = None
```

The docstring states this envelope is the house style for *every* route —
success responses populate `response` and leave `error` as `None`; error
responses do the reverse. A client can reliably check `if body["error"] is
not None` regardless of which endpoint it called or whether the failure
came from a custom `AppException`, a validation error, or an unexpected
bug.

## Practical guidance

- **Raising a domain/business-rule failure** (permission denied, rate
  limited, egress blocked, approval rejected/timed-out, a skill/agent
  name not found): raise `AppException(class_name=..., code=ExceptionCode.X,
  error_message=ErrorMessage.Y, **template_kwargs)`. It will land in the
  client as the correct HTTP status with a structured, parseable body.
- **Raising a route-local "this specific request doesn't make sense"
  failure** (bad query param, resource ID not found for *this* route
  only): `raise HTTPException(status_code=..., detail=...)` is fine and is
  what the existing routes do — just be aware the response shape differs
  from the `AppException` path (plain `{"detail": ...}`, not
  `AppResponse`).
- **Letting something bubble up unhandled** is safe — it will always
  resolve to a 500 with a safe, non-leaking message, and the real
  exception is still fully logged server-side. It is never a silent
  failure.
