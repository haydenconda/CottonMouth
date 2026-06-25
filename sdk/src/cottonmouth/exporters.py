from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .spans import Span

log = logging.getLogger("cottonmouth")


class Exporter(Protocol):
    def export(self, span: Span) -> None: ...
    def flush(self) -> None: ...


class JSONLExporter:
    """Writes spans as newline-delimited JSON to a file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def export(self, span: Span) -> None:
        line = json.dumps(span.to_dict(), default=str) + "\n"
        with self._lock:
            with open(self._path, "a") as f:
                f.write(line)

    def flush(self) -> None:
        pass


class NoopExporter:
    """Discards all spans. Used when no exporter is configured."""

    def export(self, span: Span) -> None:
        pass

    def flush(self) -> None:
        pass


class HTTPExporter:
    """Ships spans to a remote CottonMouth collector over HTTP.

    Spans are queued and flushed from a background daemon thread so that the
    instrumented agent never blocks on the network. Batches are POSTed to
    ``{endpoint}/api/spans`` as a JSON array. Designed for multi-process /
    multi-pod deployments where the agent and the collector don't share a disk.

    Uses only the Python standard library so the SDK stays dependency-free.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str = "",
        batch_size: int = 20,
        flush_interval: float = 2.0,
        timeout: float = 5.0,
        max_queue: int = 10_000,
    ) -> None:
        self._url = endpoint.rstrip("/") + "/api/spans"
        self._api_key = api_key
        self._batch_size = max(1, batch_size)
        self._flush_interval = max(0.1, flush_interval)
        self._timeout = timeout
        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._run, name="cottonmouth-http-exporter", daemon=True
        )
        self._worker.start()
        atexit.register(self.flush)

    def export(self, span: Span) -> None:
        try:
            self._queue.put_nowait(span.to_dict())
        except queue.Full:
            log.warning("CottonMouth HTTP exporter queue full, dropping span %s", span.span_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._drain(block=True)
            if batch:
                self._send(batch)

    def _drain(self, block: bool) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        try:
            if block:
                batch.append(self._queue.get(timeout=self._flush_interval))
        except queue.Empty:
            return batch
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _send(self, batch: list[dict[str, Any]]) -> None:
        payload = json.dumps(batch, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(self._url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                resp.read()
        except (urllib.error.URLError, OSError) as exc:
            log.warning("CottonMouth HTTP exporter failed to ship %d spans: %s", len(batch), exc)

    def flush(self) -> None:
        remaining = self._drain(block=False)
        while remaining:
            self._send(remaining)
            remaining = self._drain(block=False)
