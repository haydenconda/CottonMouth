from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from src.common.paths import data_dir

log = logging.getLogger("cottonmouth.events")

BASE_DIR = data_dir()
EVENTS_FILE = BASE_DIR / "events.jsonl"
_lock = threading.Lock()

MAX_SUPPORT_LINES = 500
_ROTATION_CHECK_INTERVAL = 50
_support_emit_count = 0


def emit_event(
    agent: str,
    severity: str,
    title: str,
    message: str,
    source: str = "",
    action_url: str = "",
) -> None:
    """Append a structured event to events.jsonl. Events grow unbounded until cleared by the user."""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "severity": severity,
        "title": title,
        "message": message,
        "source": source,
        "action_url": action_url,
    }
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with _lock:
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()


def rotate_jsonl_files() -> None:
    """Rotate support files (queries/responses) but NOT events — events are user-cleared."""
    global _support_emit_count
    _support_emit_count += 1
    if _support_emit_count % _ROTATION_CHECK_INTERVAL != 0:
        return
    for name in ("queries.jsonl", "responses.jsonl"):
        path = BASE_DIR / name
        if path.exists():
            _maybe_rotate(path, MAX_SUPPORT_LINES)


def _maybe_rotate(path: Path, keep: int) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) <= keep:
            return
        trimmed = lines[-keep:]
        path.write_text("".join(trimmed), encoding="utf-8")
        log.info("Rotated %s: %d → %d lines", path.name, len(lines), len(trimmed))
    except Exception:
        log.exception("Failed to rotate %s", path.name)
