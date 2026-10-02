"""EgressPolicy — the pure allow/deny decision for an outbound network call.

New, PS-mandatory (REQ-M-06, REQ-M-09). No donor equivalent — VEDA had no
network allow-list concept. This is the function service/privacy/
egress_guard.py calls before letting any tpa/online provider make a
request; the transport-layer enforcement itself lives in tpa (a later
batch), this file only decides.

Two independent conditions must both hold: the host must be on the
allow-list, AND the payload must not carry audio (belt-and-braces against
REQ-M-06, in case a future provider ever tried to attach a recording to
a request for any reason).
"""

from domain.value_objects.egress_target import EgressTarget

_ALWAYS_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1"})  # local inference server


def is_allowed(target: EgressTarget, allow_list: frozenset[str]) -> bool:
    """True if this destination may be contacted."""
    return target.host in _ALWAYS_ALLOWED_HOSTS or target.host in allow_list


def is_audio_payload(content_type: str) -> bool:
    """True if a request body's content type indicates audio — always denied."""
    return content_type.lower().startswith("audio/")