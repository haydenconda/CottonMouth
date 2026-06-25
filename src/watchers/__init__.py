"""Watcher modules for the CottonMouth backend."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import aiohttp

WATCHER = "ticker"


@dataclass
class WatcherResult:
    """Returned by each watcher run to indicate success/failure."""
    ok: bool = True
    error: str = ""


class BaseWatcher(ABC):
    """Interface that every service watcher implements."""

    name: str = "unknown"

    @abstractmethod
    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        ...
