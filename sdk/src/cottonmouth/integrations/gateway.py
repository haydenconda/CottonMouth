"""Helpers for observing the LiteLLM gateway's own governance decisions.

CottonMouth does NOT duplicate LiteLLM's enforcement (model access, budgets,
rate limits, guardrails — those live on the virtual key / proxy config). Instead,
when the gateway *rejects* a call it returns a recognizable error; this module
classifies that error so CottonMouth can record the gateway's verdict as a
``permission_check`` (the "what was it allowed to do" pillar) without re-deciding
anything.
"""
from __future__ import annotations

# (reason_code, substrings) — matched case-insensitively against the error text.
# Order matters: the first matching reason wins.
_DENIAL_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("budget", ("budget", "exceeded budget", "budgetexceeded", "max_budget", "spend")),
    ("model-access", (
        "not allowed to access model", "not allowed to call model",
        "model not in allowed", "team not allowed to access", "model_access",
        "no access to model", "key not allowed to access model",
    )),
    ("mcp-access", (
        "not allowed to access mcp", "mcp server not allowed",
        "tool not permitted", "not permitted to call tool", "forbidden tool",
        "mcp permission", "no access to mcp", "not allowed to access tool",
    )),
    ("rate-limit", (
        "rate limit", "ratelimiterror", "tpm limit", "rpm limit",
        "rate_limit", "max parallel requests", "too many requests",
    )),
    ("guardrail", ("guardrail", "blocked by guardrail", "content policy", "flagged by")),
    ("auth", (
        "invalid proxy server token", "invalid api key", "authentication error",
        "key not found", "key is blocked", "expired key", "invalid key",
    )),
)


def classify_gateway_denial(error: str) -> tuple[bool, str]:
    """Return ``(is_denial, reason_code)`` for a gateway error string.

    ``is_denial`` is True only when the error reflects a *policy* decision the
    gateway made (budget, model access, rate limit, guardrail, key auth). Plain
    provider/transport failures return ``(False, "")``.
    """
    e = (error or "").lower()
    if not e:
        return False, ""
    for reason, markers in _DENIAL_MARKERS:
        if any(m in e for m in markers):
            return True, reason
    return False, ""


def infer_provider(model: str, given: str = "") -> str:
    """Best-effort provider name from the model string (for display/origin)."""
    if given:
        return given
    m = (model or "").lower()
    if "/" in m:
        return m.split("/", 1)[0]
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if "claude" in m:
        return "anthropic"
    return ""


__all__ = ["classify_gateway_denial", "infer_provider"]
