from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from src.common.config import SlackConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.slack import discover_joined_channels, slack_headers as _headers
from src.common.state import has_seen, mark_seen, prune_old
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.slack")

_ratelimit_until: float = 0.0


def _msg_url(team_id: str, channel_id: str, ts: str, thread_ts: str = "") -> str:
    if thread_ts:
        return f"slack://channel?team={team_id}&id={channel_id}&message={thread_ts}&thread_ts={ts}"
    return f"slack://channel?team={team_id}&id={channel_id}&message={ts}"


class SlackWatcher(BaseWatcher):
    name = "slack"

    def __init__(self, cfg: SlackConfig, channel_map: dict[str, str]) -> None:
        self.cfg = cfg
        self.channel_map = channel_map
        self._first_tick = True

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        lookback = 3600 if self._first_tick else 0
        self._first_tick = False
        try:
            await _run_slack(session, self.cfg, self.channel_map, firehose_lookback=lookback)
        except Exception as exc:
            log.exception("Slack watcher failed")
            return WatcherResult(ok=False, error=str(exc))
        return WatcherResult()

    async def refresh_channels(self, session: aiohttp.ClientSession) -> None:
        self.channel_map = await discover_joined_channels(session, self.cfg)
        log.info("Refreshed channel list: %d channels", len(self.channel_map))


async def _check_dms(session: aiohttp.ClientSession, cfg: SlackConfig) -> list[dict]:
    alerts: list[dict] = []
    params: dict[str, str] = {"types": "im", "limit": "50"}
    async with session.get(
        "https://slack.com/api/conversations.list",
        headers=_headers(cfg), params=params,
    ) as resp:
        data = await resp.json()
    if not data.get("ok"):
        log.error("DM list failed: %s", data.get("error"))
        return alerts

    oldest = str(time.time() - 120)
    for conv in data.get("channels", []):
        cid = conv["id"]
        async with session.get(
            "https://slack.com/api/conversations.history",
            headers=_headers(cfg),
            params={"channel": cid, "oldest": oldest, "limit": "10"},
        ) as resp:
            hist = await resp.json()
        if not hist.get("ok"):
            continue
        for msg in hist.get("messages", []):
            if msg.get("user") == cfg.user_id:
                continue
            msg_id = f"dm-{cid}-{msg.get('ts', '')}"
            if has_seen(WATCHER, msg_id):
                continue
            mark_seen(WATCHER, msg_id)
            text = msg.get("text", "")[:200]
            alerts.append({
                "type": "DM", "text": text, "user": msg.get("user", "?"),
                "channel_id": cid, "ts": msg.get("ts", ""),
            })
    return alerts


async def _check_channel(
    session: aiohttp.ClientSession, cfg: SlackConfig, channel_name: str, channel_id: str,
) -> list[dict] | None:
    alerts: list[dict] = []
    oldest = str(time.time() - cfg.poll_interval - 5)

    async with session.get(
        "https://slack.com/api/conversations.history",
        headers=_headers(cfg),
        params={"channel": channel_id, "oldest": oldest, "limit": "20"},
    ) as resp:
        data = await resp.json()
    if not data.get("ok"):
        error = data.get("error", "")
        if error == "ratelimited":
            return None
        log.warning("history for %s failed: %s", channel_name, error)
        return alerts

    for msg in data.get("messages", []):
        ts = msg.get("ts", "")
        msg_id = f"ch-{channel_id}-{ts}"
        if has_seen(WATCHER, msg_id):
            continue
        mark_seen(WATCHER, msg_id)

        text = msg.get("text", "")
        if f"<@{cfg.user_id}>" in text:
            alerts.append({
                "type": "mention", "channel": channel_name,
                "text": text[:200], "user": msg.get("user", "?"),
                "channel_id": channel_id, "ts": ts,
            })

        if msg.get("reply_count", 0) > 0 and msg.get("latest_reply"):
            async with session.get(
                "https://slack.com/api/conversations.replies",
                headers=_headers(cfg),
                params={"channel": channel_id, "ts": ts, "limit": "5"},
            ) as resp:
                thread = await resp.json()
            if not thread.get("ok"):
                continue
            thread_users = {m.get("user") for m in thread.get("messages", [])}
            if cfg.user_id in thread_users:
                latest = thread["messages"][-1]
                if latest.get("user") != cfg.user_id:
                    reply_id = f"reply-{channel_id}-{latest.get('ts', '')}"
                    if not has_seen(WATCHER, reply_id):
                        mark_seen(WATCHER, reply_id)
                        alerts.append({
                            "type": "thread_reply", "channel": channel_name,
                            "text": latest.get("text", "")[:200],
                            "user": latest.get("user", "?"),
                            "channel_id": channel_id,
                            "ts": latest.get("ts", ""),
                            "thread_ts": ts,
                        })
    return alerts


async def _check_channel_firehose(
    session: aiohttp.ClientSession, cfg: SlackConfig,
    channel_name: str, channel_id: str,
    user_cache: dict[str, str], lookback: int = 0,
) -> None:
    window = lookback if lookback else cfg.poll_interval + 5
    oldest = str(time.time() - window)

    async with session.get(
        "https://slack.com/api/conversations.history",
        headers=_headers(cfg),
        params={"channel": channel_id, "oldest": oldest, "limit": "30"},
    ) as resp:
        data = await resp.json()
    if not data.get("ok"):
        log.warning("firehose history for %s failed: %s", channel_name, data.get("error"))
        return

    for msg in data.get("messages", []):
        ts = msg.get("ts", "")
        uid = msg.get("user", "")
        fire_id = f"fire-{channel_id}-{ts}"
        if has_seen(WATCHER, fire_id):
            continue
        mark_seen(WATCHER, fire_id)

        if uid and uid not in user_cache:
            user_cache[uid] = await _resolve_user_name(session, cfg, uid)

        sender = user_cache.get(uid, "Someone")
        text = msg.get("text", "")[:200]
        url = _msg_url(cfg.team_id, channel_id, ts)
        is_mention = f"<@{cfg.user_id}>" in msg.get("text", "")
        sev = "warning" if is_mention else "info"

        emit_event(WATCHER, sev, f"#{channel_name}", f"{sender}: {text}",
                   source="slack-channel", action_url=url)


async def _resolve_user_name(
    session: aiohttp.ClientSession, cfg: SlackConfig, user_id: str,
) -> str:
    async with session.get(
        "https://slack.com/api/users.info",
        headers=_headers(cfg), params={"user": user_id},
    ) as resp:
        data = await resp.json()
    if data.get("ok"):
        return data["user"].get("real_name", data["user"].get("name", user_id))
    return user_id


async def _check_mentions_via_search(
    session: aiohttp.ClientSession, cfg: SlackConfig, channel_map: dict[str, str],
) -> list[dict]:
    alerts: list[dict] = []
    if not cfg.token.startswith("xoxp-"):
        return alerts

    headers = _headers(cfg)
    query = f"<@{cfg.user_id}>"
    params = {"query": query, "sort": "timestamp", "sort_dir": "desc", "count": "20"}

    try:
        async with session.get(
            "https://slack.com/api/search.messages", headers=headers, params=params,
        ) as resp:
            data = await resp.json()
    except Exception:
        log.exception("Slack search.messages failed")
        return alerts

    if not data.get("ok"):
        error = data.get("error", "")
        if error != "ratelimited":
            log.warning("search.messages failed: %s", error)
        return alerts

    reverse_map = {v: k for k, v in channel_map.items()}

    for match in data.get("messages", {}).get("matches", []):
        ts = match.get("ts", "")
        channel_info = match.get("channel", {})
        cid = channel_info.get("id", "")
        channel_name = reverse_map.get(cid, channel_info.get("name", "unknown"))

        msg_id = f"search-mention-{cid}-{ts}"
        if has_seen(WATCHER, msg_id):
            continue
        mark_seen(WATCHER, msg_id)

        text = match.get("text", "")[:200]
        user = match.get("user", match.get("username", "?"))

        alerts.append({
            "type": "mention", "channel": channel_name,
            "text": text, "user": user,
            "channel_id": cid, "ts": ts,
        })

    return alerts


async def _run_slack(
    session: aiohttp.ClientSession, cfg: SlackConfig,
    channel_map: dict[str, str], firehose_lookback: int = 0,
) -> None:
    global _ratelimit_until
    if time.monotonic() < _ratelimit_until:
        return

    all_alerts: list[dict] = []
    dm_alerts = await _check_dms(session, cfg)
    all_alerts.extend(dm_alerts)

    mention_alerts = await _check_mentions_via_search(session, cfg, channel_map)
    all_alerts.extend(mention_alerts)

    firehose_set = set(cfg.firehose_channels)
    priority_set = set(cfg.priority_channels)

    user_cache_fh: dict[str, str] = {}
    for name in firehose_set:
        cid = channel_map.get(name)
        if not cid:
            continue
        await _check_channel_firehose(session, cfg, name, cid, user_cache_fh, lookback=firehose_lookback)
        await asyncio.sleep(1.5)

    for name in priority_set:
        cid = channel_map.get(name)
        if not cid:
            continue
        ch_alerts = await _check_channel(session, cfg, name, cid)
        if ch_alerts is None:
            _ratelimit_until = time.monotonic() + 120
            log.warning("Slack rate limited — backing off 120s")
            break
        all_alerts.extend(ch_alerts)
        await asyncio.sleep(1.5)

    user_cache: dict[str, str] = {}
    for alert in all_alerts:
        uid = alert.get("user", "")
        if uid and uid not in user_cache:
            user_cache[uid] = await _resolve_user_name(session, cfg, uid)

    for alert in all_alerts:
        sender = user_cache.get(alert.get("user", ""), "Someone")
        atype = alert["type"]
        text = alert["text"]
        cid = alert.get("channel_id", "")
        msg_ts = alert.get("ts", "")

        if atype == "DM":
            title = "Slack DM"
            subtitle = f"From {sender}"
            url = _msg_url(cfg.team_id, cid, msg_ts) if cid and msg_ts else ""
            await send_notification(title, text, subtitle=subtitle, severity="warning")
            emit_event(WATCHER, "info", title, text, source="slack-dm", action_url=url)
        elif atype == "mention":
            title = f"Slack #{alert['channel']}"
            subtitle = f"{sender} mentioned you"
            url = _msg_url(cfg.team_id, cid, msg_ts) if cid and msg_ts else ""
            await send_notification(title, text, subtitle=subtitle, severity="warning")
            emit_event(WATCHER, "warning", title, f"{subtitle}: {text}", source="slack-mention", action_url=url)
        elif atype == "thread_reply":
            title = f"Slack #{alert['channel']}"
            subtitle = f"{sender} replied in your thread"
            url = _msg_url(cfg.team_id, cid, msg_ts, thread_ts=alert.get("thread_ts", "")) if cid and msg_ts else ""
            await send_notification(title, text, subtitle=subtitle, severity="info")
            emit_event(WATCHER, "info", title, f"{subtitle}: {text}", source="slack-thread", action_url=url)

    prune_old(WATCHER, keep_latest=1000)
