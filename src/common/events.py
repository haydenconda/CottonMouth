from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from src.common.paths import data_dir

log = logging.getLogger("cottonmouth.events")

BASE_DIR = data_dir()
EVENTS_FILE = BASE_DIR / "events.jsonl"
_lock = threading.Lock()

MAX_SUPPORT_LINES = 500
# events.jsonl is normally user-cleared, but we cap it generously so a runaway
# burst (e.g. an infra-failure storm) can never grow it without bound and OOM
# the backend's recent-window reads.
MAX_EVENT_LINES = 50000
# Only pay the line-scan rotation cost once the file is actually large.
_EVENTS_ROTATE_BYTES = 24 * 1024 * 1024
_ROTATION_CHECK_INTERVAL = 50
# Never read more than this from the end of a file when rotating.
_MAX_TAIL_BYTES = 48 * 1024 * 1024
_support_emit_count = 0
_event_emit_count = 0


def emit_event(
    agent: str,
    severity: str,
    title: str,
    message: str,
    source: str = "",
    action_url: str = "",
) -> None:
    """Append a structured event to events.jsonl. Capped at ``MAX_EVENT_LINES``."""
    global _event_emit_count
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
        _event_emit_count += 1
        if _event_emit_count % _ROTATION_CHECK_INTERVAL == 0:
            try:
                oversized = EVENTS_FILE.stat().st_size > _EVENTS_ROTATE_BYTES
            except OSError:
                oversized = False
            if oversized:
                _maybe_rotate(EVENTS_FILE, MAX_EVENT_LINES)


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


def _read_tail_lines(path: Path, max_lines: int, max_bytes: int) -> list[bytes]:
    """Return up to ``max_lines`` trailing lines, reading at most ``max_bytes``."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # drop the partial line at the seek boundary
        return list(deque(f, maxlen=max_lines))


def _maybe_rotate(path: Path, keep: int) -> None:
    """Trim ``path`` to its last ``keep`` lines if it has more, without reading
    the whole file into memory."""
    try:
        # keep+1 lets us cheaply detect "already small enough" while bounding IO.
        tail = _read_tail_lines(path, keep + 1, _MAX_TAIL_BYTES)
        if len(tail) <= keep:
            return
        trimmed = tail[-keep:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as f:
            f.write(b"".join(trimmed))
            f.flush()
        os.replace(tmp, path)
        log.info("Rotated %s: kept last %d lines", path.name, len(trimmed))
    except Exception:
        log.exception("Failed to rotate %s", path.name)
