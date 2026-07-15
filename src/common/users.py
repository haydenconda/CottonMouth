"""User accounts, API keys, and per-agent policy-mode overrides.

This is CottonMouth's own control-plane data (who can log in, what they can
do, which agents have had their enforcement mode flipped from the dashboard)
-- distinct from agent trace data, but stored in the same SQLite file
(``agent_state.db`` on the ``/data`` PVC) so it survives pod restarts without
a second volume.

``policy_overrides`` is what makes the admin UI's per-agent enforce/monitor
toggle work without editing the ``agent_policies.json`` ConfigMap by hand: an
override here always wins over the file (see ``src.common.policies.agent_mode``),
and can be cleared to fall back to the file's declared mode.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.common.auth import hash_password
from src.common.state import get_conn, locked


class UserExistsError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_tables() -> None:
    conn = get_conn()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  username TEXT NOT NULL UNIQUE,"
        "  password_hash TEXT NOT NULL,"
        "  role TEXT NOT NULL,"
        "  disabled INTEGER NOT NULL DEFAULT 0,"
        "  created_at TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_keys ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  name TEXT NOT NULL,"
        "  key_hash TEXT NOT NULL UNIQUE,"
        "  role TEXT NOT NULL,"
        "  created_by TEXT,"
        "  created_at TEXT NOT NULL,"
        "  last_used_at TEXT,"
        "  disabled INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS policy_overrides ("
        "  agent_name TEXT PRIMARY KEY,"
        "  mode TEXT NOT NULL,"
        "  updated_by TEXT,"
        "  updated_at TEXT NOT NULL"
        ")"
    )
    conn.commit()


with locked():
    _ensure_tables()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(username: str, password: str, role: str) -> int:
    with locked():
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, hash_password(password), role, _now()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise UserExistsError(username) from None
        return cur.lastrowid


def get_user_by_username(username: str) -> dict | None:
    with locked():
        row = get_conn().execute(
            "SELECT id, username, password_hash, role, disabled FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "username": row[1], "password_hash": row[2],
        "role": row[3], "disabled": bool(row[4]),
    }


def list_users() -> list[dict]:
    with locked():
        rows = get_conn().execute(
            "SELECT id, username, role, disabled, created_at FROM users ORDER BY id"
        ).fetchall()
    return [
        {"id": r[0], "username": r[1], "role": r[2], "disabled": bool(r[3]), "created_at": r[4]}
        for r in rows
    ]


def count_users() -> int:
    with locked():
        row = get_conn().execute("SELECT COUNT(*) FROM users").fetchone()
    return row[0] if row else 0


def update_user(user_id: int, role: str | None, disabled: bool | None, password: str | None) -> bool:
    sets: list[str] = []
    vals: list[object] = []
    if role is not None:
        sets.append("role = ?")
        vals.append(role)
    if disabled is not None:
        sets.append("disabled = ?")
        vals.append(1 if disabled else 0)
    if password:
        sets.append("password_hash = ?")
        vals.append(hash_password(password))
    if not sets:
        return True
    vals.append(user_id)
    with locked():
        conn = get_conn()
        cur = conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        return cur.rowcount > 0


def delete_user(user_id: int) -> bool:
    with locked():
        conn = get_conn()
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def count_admins(exclude_id: int | None = None) -> int:
    """Count enabled admins, optionally excluding one user -- used to stop the
    last admin from disabling/demoting/deleting themselves into a lockout."""
    with locked():
        if exclude_id is None:
            row = get_conn().execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0"
            ).fetchone()
        else:
            row = get_conn().execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                (exclude_id,),
            ).fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def create_api_key(name: str, key_hash: str, role: str, created_by: str) -> int:
    with locked():
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO api_keys (name, key_hash, role, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, key_hash, role, created_by, _now()),
        )
        conn.commit()
        return cur.lastrowid


def get_api_key_by_hash(key_hash: str) -> dict | None:
    with locked():
        row = get_conn().execute(
            "SELECT id, name, role, disabled FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "role": row[2], "disabled": bool(row[3])}


def touch_api_key(key_id: int) -> None:
    with locked():
        conn = get_conn()
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), key_id))
        conn.commit()


def list_api_keys() -> list[dict]:
    with locked():
        rows = get_conn().execute(
            "SELECT id, name, role, created_by, created_at, last_used_at, disabled "
            "FROM api_keys ORDER BY id"
        ).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "role": r[2], "created_by": r[3],
            "created_at": r[4], "last_used_at": r[5], "disabled": bool(r[6]),
        }
        for r in rows
    ]


def revoke_api_key(key_id: int) -> bool:
    with locked():
        conn = get_conn()
        cur = conn.execute("UPDATE api_keys SET disabled = 1 WHERE id = ?", (key_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Policy-mode overrides -- see src.common.policies.agent_mode()
# ---------------------------------------------------------------------------


def set_policy_override(agent_name: str, mode: str, updated_by: str) -> None:
    with locked():
        conn = get_conn()
        conn.execute(
            "INSERT INTO policy_overrides (agent_name, mode, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(agent_name) DO UPDATE SET "
            "  mode = excluded.mode, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (agent_name, mode, updated_by, _now()),
        )
        conn.commit()


def clear_policy_override(agent_name: str) -> bool:
    with locked():
        conn = get_conn()
        cur = conn.execute("DELETE FROM policy_overrides WHERE agent_name = ?", (agent_name,))
        conn.commit()
        return cur.rowcount > 0


def get_policy_override(agent_name: str) -> str | None:
    with locked():
        row = get_conn().execute(
            "SELECT mode FROM policy_overrides WHERE agent_name = ?", (agent_name,)
        ).fetchone()
    return row[0] if row else None


def list_policy_overrides() -> dict[str, dict]:
    with locked():
        rows = get_conn().execute(
            "SELECT agent_name, mode, updated_by, updated_at FROM policy_overrides"
        ).fetchall()
    return {r[0]: {"mode": r[1], "updated_by": r[2], "updated_at": r[3]} for r in rows}
