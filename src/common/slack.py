from __future__ import annotations

import logging

import aiohttp

from src.common.config import SlackConfig

log = logging.getLogger("cottonmouth.slack")


def slack_headers(cfg: SlackConfig) -> dict[str, str]:
    """Standard Slack API headers. Shared across all Slack callers."""
    return {"Authorization": f"Bearer {cfg.token}", "Content-Type": "application/json"}


async def resolve_channel_map(
    cfg: SlackConfig,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, str]:
    """Resolve all Slack channels the user has joined. Shared by ticker + query router."""
    owns_session = session is None
    if owns_session:
        session = aiohttp.ClientSession()

    mapping: dict[str, str] = {}
    headers = slack_headers(cfg)
    cursor = ""

    try:
        while True:
            params: dict[str, str] = {
                "user": cfg.user_id,
                "types": "public_channel,private_channel,mpim",
                "exclude_archived": "true",
                "limit": "200",
            }
            if cursor:
                params["cursor"] = cursor
            try:
                async with session.get(
                    "https://slack.com/api/users.conversations",
                    headers=headers, params=params,
                ) as resp:
                    data = await resp.json()
            except Exception:
                log.exception("Channel map resolution failed")
                break
            if not data.get("ok"):
                log.error("users.conversations failed: %s", data.get("error"))
                break
            for ch in data.get("channels", []):
                mapping[ch.get("name", ch["id"])] = ch["id"]
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
    finally:
        if owns_session:
            await session.close()

    log.info("Resolved %d channels", len(mapping))
    return mapping


async def merge_priority_channels(
    session: aiohttp.ClientSession,
    cfg: SlackConfig,
    mapping: dict[str, str],
) -> None:
    """Ensure priority channels are in the mapping even if not in users.conversations."""
    if not cfg.priority_channels:
        return
    missing = [ch for ch in cfg.priority_channels if ch not in mapping]
    if not missing:
        return

    headers = slack_headers(cfg)
    raw_ids = [ch for ch in missing if ch.startswith("C") and len(ch) >= 9]
    names_to_find = set(ch for ch in missing if ch not in raw_ids)

    for cid in raw_ids:
        try:
            async with session.get(
                "https://slack.com/api/conversations.info",
                headers=headers, params={"channel": cid},
            ) as resp:
                data = await resp.json()
            if data.get("ok"):
                name = data["channel"].get("name", cid)
                mapping[name] = cid
            else:
                log.warning("conversations.info failed for %s: %s", cid, data.get("error"))
        except Exception:
            log.exception("Failed to resolve channel ID %s", cid)

    if not names_to_find:
        return

    cursor = ""
    while names_to_find:
        params: dict[str, str] = {
            "types": "public_channel,private_channel",
            "exclude_archived": "true", "limit": "200",
        }
        if cursor:
            params["cursor"] = cursor
        try:
            async with session.get(
                "https://slack.com/api/conversations.list", headers=headers, params=params,
            ) as resp:
                data = await resp.json()
        except Exception:
            break
        if not data.get("ok"):
            break
        for ch in data.get("channels", []):
            name = ch.get("name", "")
            if name in names_to_find:
                mapping[name] = ch["id"]
                names_to_find.discard(name)
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break

    for name in names_to_find:
        log.warning("Priority channel '%s' not found", name)


async def discover_joined_channels(
    session: aiohttp.ClientSession,
    cfg: SlackConfig,
) -> dict[str, str]:
    """Full discovery: resolve joined channels then merge priority channels."""
    mapping = await resolve_channel_map(cfg, session=session)
    await merge_priority_channels(session, cfg, mapping)
    return mapping
