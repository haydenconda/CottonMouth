"""Centralized data paths.

Every piece of mutable state (traces, events, health, investigate queue,
session memory, SQLite dedup/stats) lives under a single data directory so a
container can mount one volume and survive restarts. Defaults to the repo root
for local/macOS use; set ``COTTONMOUTH_DATA_DIR`` (e.g. ``/data``) in the cluster.

``AGENT_TRACES_DIR`` still overrides only the traces location for backward
compatibility with existing configs.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    d = Path(os.environ.get("COTTONMOUTH_DATA_DIR", str(BASE_DIR)))
    d.mkdir(parents=True, exist_ok=True)
    return d


def traces_file() -> Path:
    override = os.environ.get("AGENT_TRACES_DIR", "")
    base = Path(override) if override else data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "traces.jsonl"
