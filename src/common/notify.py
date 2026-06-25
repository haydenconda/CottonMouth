from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess

log = logging.getLogger("cottonmouth.notify")

_USE_TERMINAL_NOTIFIER: bool | None = None

_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}
_notify_min_severity: str = "warning"


def set_min_severity(level: str) -> None:
    global _notify_min_severity
    _notify_min_severity = level
    log.info("Notification min severity set to: %s", level)


def _should_notify(severity: str) -> bool:
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(_notify_min_severity, 2)


def _find_terminal_notifier() -> str | None:
    """Look for terminal-notifier in PATH or pync's bundled copy."""
    path = shutil.which("terminal-notifier")
    if path:
        return path
    try:
        import pync
        import os
        vendor_dir = os.path.join(os.path.dirname(pync.__file__), "vendor")
        if os.path.isdir(vendor_dir):
            for entry in os.listdir(vendor_dir):
                candidate = os.path.join(
                    vendor_dir, entry,
                    "terminal-notifier.app", "Contents", "MacOS", "terminal-notifier",
                )
                if os.path.isfile(candidate):
                    return candidate
    except ImportError:
        pass
    return None


async def send_notification(
    title: str, message: str, subtitle: str = "", severity: str = "warning",
) -> None:
    """Send a macOS notification if severity meets the minimum threshold."""
    global _USE_TERMINAL_NOTIFIER

    if not _should_notify(severity):
        return

    if _USE_TERMINAL_NOTIFIER is None:
        _USE_TERMINAL_NOTIFIER = _find_terminal_notifier()
        if _USE_TERMINAL_NOTIFIER:
            log.info("Using terminal-notifier at: %s", _USE_TERMINAL_NOTIFIER)
        else:
            log.info("terminal-notifier not found, using osascript fallback")

    if _USE_TERMINAL_NOTIFIER:
        await _notify_terminal_notifier(_USE_TERMINAL_NOTIFIER, title, message, subtitle)
    else:
        await _notify_osascript(title, message, subtitle)


async def _notify_terminal_notifier(
    bin_path: str, title: str, message: str, subtitle: str
) -> None:
    cmd = [bin_path, "-title", title, "-message", message, "-group", "cottonmouth"]
    if subtitle:
        cmd.extend(["-subtitle", subtitle])
    cmd.extend(["-sound", "default"])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning("terminal-notifier failed (rc=%d): %s", proc.returncode, stderr.decode().strip())
    except Exception:
        log.exception("Failed to send notification via terminal-notifier")


async def _notify_osascript(title: str, message: str, subtitle: str) -> None:
    t = title.replace('"', '\\"')
    m = message.replace('"', '\\"')
    s = subtitle.replace('"', '\\"')

    script = f'display notification "{m}" with title "{t}"'
    if s:
        script += f' subtitle "{s}"'

    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning("osascript failed: %s", stderr.decode().strip())
    except Exception:
        log.exception("Failed to send macOS notification")
