from __future__ import annotations

import sqlite3
import threading

from src.common.paths import data_dir

DB_PATH = data_dir() / "agent_state.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen ("
        "  watcher TEXT NOT NULL,"
        "  item_id TEXT NOT NULL,"
        "  PRIMARY KEY (watcher, item_id)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv ("
        "  watcher TEXT NOT NULL,"
        "  key TEXT NOT NULL,"
        "  value TEXT NOT NULL,"
        "  PRIMARY KEY (watcher, key)"
        ")"
    )
    conn.commit()
    _conn = conn
    return conn


def has_seen(watcher: str, item_id: str) -> bool:
    with _lock:
        row = _get_conn().execute(
            "SELECT 1 FROM seen WHERE watcher = ? AND item_id = ?",
            (watcher, item_id),
        ).fetchone()
    return row is not None


def mark_seen(watcher: str, item_id: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO seen (watcher, item_id) VALUES (?, ?)",
            (watcher, item_id),
        )
        conn.commit()


def get_value(watcher: str, key: str, default: str = "") -> str:
    with _lock:
        row = _get_conn().execute(
            "SELECT value FROM kv WHERE watcher = ? AND key = ?",
            (watcher, key),
        ).fetchone()
    return row[0] if row else default


def set_value(watcher: str, key: str, value: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO kv (watcher, key, value) VALUES (?, ?, ?)",
            (watcher, key, value),
        )
        conn.commit()


def prune_old(watcher: str, keep_latest: int = 500) -> None:
    """Keep only the most recent `keep_latest` seen items per watcher."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "DELETE FROM seen WHERE watcher = ? AND rowid NOT IN ("
            "  SELECT rowid FROM seen WHERE watcher = ? ORDER BY rowid DESC LIMIT ?"
            ")",
            (watcher, watcher, keep_latest),
        )
        conn.commit()
