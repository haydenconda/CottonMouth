from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.watchers import ticker_agent
from src import query_router
from src.api import start_api

handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(
    logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")
)
handler.setLevel(logging.INFO)
logging.root.addHandler(handler)
logging.root.setLevel(logging.INFO)

log = logging.getLogger("cottonmouth")

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
RELOAD_CHECK_INTERVAL = 5

UV_BIN = os.environ.get("UV_BIN", "uv")


def _snapshot_mtimes() -> dict[str, float]:
    """Snapshot modification times for all .py files under src/."""
    mtimes: dict[str, float] = {}
    for p in SRC_DIR.rglob("*.py"):
        try:
            mtimes[str(p)] = p.stat().st_mtime
        except OSError:
            pass
    return mtimes


def _restart_self() -> None:
    """Re-exec the process in-place for a code reload."""
    log.info("Re-exec'ing process (PID %d) for code reload", os.getpid())
    os.chdir(str(BASE_DIR))
    uv_path = shutil.which(UV_BIN) or UV_BIN
    os.execv(uv_path, [uv_path, "run", "--project", str(BASE_DIR), "python", "-m", "src.main"])


async def _watch_for_code_changes(shutdown_event: asyncio.Event) -> None:
    """Poll source files for changes and re-exec to pick up new code."""
    baseline = _snapshot_mtimes()
    log.info("Code watcher active — monitoring %d source files", len(baseline))

    while not shutdown_event.is_set():
        await asyncio.sleep(RELOAD_CHECK_INTERVAL)
        current = _snapshot_mtimes()

        changed = []
        for path, mtime in current.items():
            if path not in baseline or baseline[path] < mtime:
                changed.append(Path(path).name)

        if changed:
            log.info("Source files changed: %s — restarting...", ", ".join(changed))
            _restart_self()
            return


def _reload_enabled() -> bool:
    """Code-reload re-execs the process via uv — great for local dev, but
    undesirable inside a container. Enabled by default, disabled when
    COTTONMOUTH_DISABLE_RELOAD is truthy (set in the container image)."""
    return os.environ.get("COTTONMOUTH_DISABLE_RELOAD", "").lower() not in ("1", "true", "yes")


async def _main() -> None:
    log.info("CottonMouth starting (PID %d)", os.getpid())

    shutdown_event = asyncio.Event()

    tasks = [
        asyncio.create_task(ticker_agent.run(), name="ticker"),
        asyncio.create_task(query_router.run(), name="query-router"),
        asyncio.create_task(start_api(), name="api-server"),
    ]
    if _reload_enabled():
        tasks.append(
            asyncio.create_task(_watch_for_code_changes(shutdown_event), name="code-watcher")
        )

    def _shutdown(sig: signal.Signals) -> None:
        log.info("Received %s, shutting down...", sig.name)
        shutdown_event.set()
        for t in tasks:
            t.cancel()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results):
        if isinstance(result, asyncio.CancelledError):
            log.info("Task %s cancelled", task.get_name())
        elif isinstance(result, Exception):
            log.error("Task %s failed: %s", task.get_name(), result)

    log.info("CottonMouth stopped")


def run() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    run()
