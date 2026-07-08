"""Agent permission policies (policy-as-data).

``agent_policies.json`` at the repo root is the single source of truth: the
agent enforces these rules at runtime (emitting permission_check spans) and the
backend serves them to the governance UI. Both read the same file from the
shared image, so what the UI shows is exactly what the agent is bound by.

``COTTONMOUTH_POLICIES_FILE`` overrides the path (e.g. when mounting a
ConfigMap). That ConfigMap is optional (see deploy/k8s/backend.yaml) — if it
isn't created, the mounted path won't exist. In that case we fall back to the
image's bundled copy rather than silently serving an empty policy document
(which would report every agent as an unconfigured "enforce").
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# repo root = .../cottonmouth (this file is src/common/policies.py)
BASE_DIR = Path(__file__).resolve().parents[2]
_BUNDLED_POLICIES_FILE = BASE_DIR / "agent_policies.json"


def policies_file() -> Path:
    override = os.environ.get("COTTONMOUTH_POLICIES_FILE", "")
    if not override:
        return _BUNDLED_POLICIES_FILE
    path = Path(override)
    # The override normally points at an optional ConfigMap mount. If that
    # ConfigMap wasn't created, the path won't exist -- fall back to the
    # bundled copy instead of silently going policy-less.
    return path if path.exists() else _BUNDLED_POLICIES_FILE


def load_policies() -> dict[str, Any]:
    """Return the full policy document, or an empty skeleton if missing."""
    path = policies_file()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": "0", "agents": {}}


def agent_policy(agent_name: str) -> dict[str, Any]:
    return load_policies().get("agents", {}).get(agent_name, {})


def rule_values(agent_name: str, rule_id: str) -> list[str]:
    for rule in agent_policy(agent_name).get("rules", []):
        if rule.get("id") == rule_id:
            return list(rule.get("values", []))
    return []


def agent_mode(agent_name: str, default: str = "enforce") -> str:
    """Return the enforcement mode for an agent's policy.

    ``"enforce"`` blocks denied actions; ``"monitor"`` (shadow mode) records the
    verdict but lets the action proceed so compliance can be measured before
    turning on enforcement. Falls back to the document-level ``default_mode``,
    then ``default``.
    """
    doc = load_policies()
    ap = doc.get("agents", {}).get(agent_name, {})
    mode = ap.get("mode") or doc.get("default_mode") or default
    return mode if mode in ("enforce", "monitor") else default
