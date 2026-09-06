"""Event-driven describer driver.

Subscribes to Mantle's `/events` change-feed as a system consumer (authenticated
with the crystal service identity) and, when an artifact whose content_type
declares a `describer` changes, asks the dispatcher to fire that describer rooted
to the change's actor. This covers changes that did not pass through the gateway
(direct Mantle writes); gateway-mediated changes are already described inline, and
a shared dedup in the dispatcher prevents double-firing + write-back loops.

Best-effort: the connection retries on drop; failures never affect request paths.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import websockets

logger = logging.getLogger("agience.crystal.events")

_CHANGE_EVENTS = ("artifact.created", "artifact.updated")


def mantle_ws_url(mantle_uri: str) -> str:
    base = mantle_uri.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/events"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/events"
    return base + "/events"


class EventDescriberDriver:
    def __init__(self, ws_url: str, identity, dispatcher, reconnect_delay: float = 5.0) -> None:
        self.ws_url = ws_url
        self.identity = identity
        self.dispatcher = dispatcher
        self.reconnect_delay = reconnect_delay
        self._stop = False

    async def run(self) -> None:
        while not self._stop:
            try:
                await self._session()
            except Exception as exc:
                logger.warning("event-driver session ended: %s", exc)
            if self._stop:
                break
            await asyncio.sleep(self.reconnect_delay)

    async def _session(self) -> None:
        token = self.identity.service_jwt("mantle")
        async with websockets.connect(
            self.ws_url, additional_headers={"Authorization": f"Bearer {token}"}
        ) as ws:
            await ws.send(json.dumps({
                "op": "subscribe", "id": "describers",
                "filter": {"event_names": list(_CHANGE_EVENTS)},
            }))
            logger.info("event-driven describers: subscribed at %s", self.ws_url)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if "event" in msg:
                    await self._handle(msg)

    async def _handle(self, msg: dict) -> None:
        if msg.get("event") not in _CHANGE_EVENTS:
            return
        payload = msg.get("payload") or {}
        art = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else payload
        if not isinstance(art, dict):
            return
        content_type = art.get("content_type") or ""
        artifact_id = art.get("id") or art.get("root_id")
        if not content_type or not artifact_id:
            return
        # The describer runs as the operator-rooted system principal (no user
        # impersonation) — the change's actor is not needed to authorize it.
        try:
            await self.dispatcher.fire_describer_for_resource(content_type, artifact_id)
        except Exception as exc:  # best-effort
            logger.warning("event describer dispatch failed for %s: %s", artifact_id, exc)

    def stop(self) -> None:
        self._stop = True
