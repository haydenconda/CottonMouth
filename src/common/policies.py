"""Agent permission policies (policy-as-data).

``agent_policies.json`` at the repo root is the single source of truth: the
agent enforces these rules at runtime (emitting permission_check spans) and the
backend serves them to the governance UI. Both read the same file from the
shared image, so what the UI shows is exactly what the agent is bound by.

``COTTONMOUTH_POLICIES_FILE`` overrides the path (e.g. when mounting a ConfigMap).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# repo root = .../cottonmouth (this file is src/common/policies.py)
BASE_DIR = Path(__file__).resolve().parents[2]


def policies_file() -> Path:
    override = os.environ.get("COTTONMOUTH_POLICIES_FILE", "")
    return Path(override) if override else BASE_DIR / "agent_policies.json"


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
