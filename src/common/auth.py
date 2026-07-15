"""Authentication primitives: password hashing, session tokens, API keys.

CottonMouth ships its own lightweight auth (no SSO/OIDC dependency yet — see
CORE-10694) so the dashboard and ``/api/*`` aren't wide open once this runs
somewhere reachable beyond a single operator's laptop. Kept dependency-free
(stdlib ``hashlib``/``hmac``/``secrets`` only) to match the rest of the
backend.

Roles are a simple ranked ladder:
    viewer   — read traces/agents/governance/events (default for any login)
    operator — viewer + toggle policy enforcement mode, manage alert config
    admin    — operator + manage users and API keys

Sessions are stateless HMAC-signed cookies (JWT-shaped but hand-rolled — no
new dependency): no server-side session table, so revocation is by TTL
(default 7 days) or by disabling the user account, not by killing a session
id. API keys are the machine-auth equivalent (for the CLI/MCP server/CI):
only a SHA-256 hash is ever stored, the raw key is shown once at creation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

ROLES = ("viewer", "operator", "admin")
_ROLE_RANK = {"viewer": 10, "operator": 20, "admin": 30}


def role_at_least(role: str, minimum: str) -> bool:
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK.get(minimum, 999)


# ---------------------------------------------------------------------------
# Passwords — PBKDF2-HMAC-SHA256, random salt per password, stored as a single
# self-describing string so the iteration count can be bumped later without
# invalidating existing hashes.
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 210_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------------------
# API keys — shown once at creation; only the SHA-256 hash is ever persisted.
# ---------------------------------------------------------------------------


def generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash). Caller shows raw_key once and stores only
    key_hash."""
    raw = "cmk_" + secrets.token_urlsafe(32)
    return raw, hash_api_key(raw)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Session tokens — stateless, HMAC-signed: base64url(json payload) + "." + hex
# HMAC-SHA256 signature. No server-side session store, so a stolen token is
# valid until its ``exp`` — keep TTLs modest (default 7 days) and rely on
# disabling the account to cut off a compromised user.
# ---------------------------------------------------------------------------

_secret_key: bytes | None = None


def _get_secret_key() -> bytes:
    """Resolve the HMAC signing key: explicit env var if set, otherwise a
    value generated once and persisted in the state DB so sessions survive
    pod restarts without requiring a Secret to be wired up for local/dev use.
    Production deployments should set ``COTTONMOUTH_SECRET_KEY`` explicitly
    (from a k8s Secret) so signing survives a full redeploy, not just a
    restart of the same PVC-backed pod.
    """
    global _secret_key
    if _secret_key is not None:
        return _secret_key
    env = os.environ.get("COTTONMOUTH_SECRET_KEY", "").strip()
    if env:
        _secret_key = env.encode()
        return _secret_key
    from src.common import state

    existing = state.get_value("auth", "secret_key")
    if existing:
        _secret_key = existing.encode()
        return _secret_key
    generated = secrets.token_hex(32)
    state.set_value("auth", "secret_key", generated)
    _secret_key = generated.encode()
    return _secret_key


def create_session_token(user_id: int, username: str, role: str, ttl_seconds: int) -> str:
    payload = {
        "uid": user_id,
        "username": username,
        "role": role,
        "exp": int(time.time()) + ttl_seconds,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = hmac.new(_get_secret_key(), body, hashlib.sha256).hexdigest()
    return f"{body.decode()}.{sig}"


def verify_session_token(token: str) -> dict | None:
    try:
        body_str, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(_get_secret_key(), body_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padded = body_str + "=" * (-len(body_str) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
