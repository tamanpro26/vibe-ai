"""
core/push_notify.py
Web Push subscriptions + fire-alert broadcast for the school heat-monitor.

Real OS-level phone notifications, not a WiFi-radius broadcast: a phone only
receives these once its browser has visited the site and granted
notification permission (see the site's service worker + subscribe flow).
That's a genuine constraint of how push notifications work everywhere --
there's no way for a bare WiFi connection to push a notification to a phone
without either an installed app or an existing browser subscription.
"""
from __future__ import annotations

import asyncio
import json

import aiosqlite
from loguru import logger

from config.settings import settings

# Public key is not sensitive -- VAPID public keys are designed to be
# shipped to the browser (it's how the browser verifies THIS server signed
# the push, the mirror image of how a JWT's public key works). Only the
# private key needs to stay a secret, server-side only.
VAPID_PUBLIC_KEY = "BCldfrcLttHy3Cs4l9n7XrCJfjWlQsg0xF6zk7bR3MNhQSVYHcV0vm9Onbvy8dfhZ7_sQwgBvdw6qV35IfPb204"
VAPID_CLAIMS = {"sub": "mailto:vibeai-demo@example.com"}


class PushStore:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.session_db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    endpoint          TEXT PRIMARY KEY,
                    subscription_json TEXT NOT NULL,
                    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            await db.commit()

    async def add(self, subscription: dict) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO push_subscriptions (endpoint, subscription_json) VALUES (?, ?)",
                (subscription["endpoint"], json.dumps(subscription)),
            )
            await db.commit()

    async def remove(self, endpoint: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
            await db.commit()

    async def all(self) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT subscription_json FROM push_subscriptions")
            rows = await cursor.fetchall()
        return [json.loads(r[0]) for r in rows]


push_store = PushStore()


async def broadcast_alert(title: str, body: str) -> int:
    """
    Sends a Web Push notification to every registered subscriber.
    Returns how many sends succeeded. A dead/expired subscription (404/410
    from the push service) is pruned instead of retried indefinitely.

    pywebpush's webpush() is a blocking (synchronous) HTTP call -- run via
    asyncio.to_thread so a slow push-service response can't stall the event
    loop that's also serving normal chat/API traffic.
    """
    if not settings.vapid_private_key:
        logger.warning("[push] VAPID_PRIVATE_KEY not set -- skipping broadcast")
        return 0

    from pywebpush import WebPushException, webpush

    subs = await push_store.all()
    if not subs:
        return 0
    payload = json.dumps({"title": title, "body": body})
    sent = 0

    async def send_one(sub: dict) -> None:
        nonlocal sent
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=sub,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims=dict(VAPID_CLAIMS),
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                await push_store.remove(sub["endpoint"])
            else:
                logger.warning(f"[push] send failed ({status}): {exc}")

    await asyncio.gather(*(send_one(s) for s in subs), return_exceptions=True)
    logger.info(f"[push] broadcast '{title}' -> {sent}/{len(subs)} delivered")
    return sent
