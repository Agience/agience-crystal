"""Publish each server's type definitions to Mantle, so its indexer can read their `offer`.

Crystal holds the type registry. One part of a type matters where Crystal is not: a type's
`offer` (which fields give its artifacts their title, description and tags) decides what
Mantle indexes. So every time a persona registers, and once for the builtin types at startup,
Crystal sends that server's definitions to `PUT /type-definitions/{server}` under its own service
identity. Mantle stores them system-owned and reindexes the artifacts of any type whose offer
changed.

Publishing never blocks or fails a registration: without it Mantle indexes every type under the
default offer, which is what it did before types could say otherwise.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Mantle may still be starting when Crystal is (a restart of both). Retries cover that window.
ATTEMPTS = 5
RETRY_DELAY_S = 6.0


async def publish_types(mantle, identity, server: str, definitions: List[dict], *,
                        attempts: int = ATTEMPTS,
                        retry_delay: float = RETRY_DELAY_S) -> Optional[dict]:
    """Send ``server``'s definitions to Mantle. Returns Mantle's answer, or ``None``."""
    if identity is None:
        logger.info("type publish: no Crystal identity; Mantle indexes %s's types under the "
                    "default offer", server)
        return None
    for attempt in range(1, attempts + 1):
        try:
            result = await mantle.put(f"/type-definitions/{server}", {"types": definitions},
                                      token=identity.service_jwt("mantle"))
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                logger.warning("type publish for %s refused by Mantle (%s)", server, status)
                return None
            if attempt == attempts:
                logger.warning("type publish for %s failed after %d attempts: %s",
                               server, attempts, exc)
                return None
            await asyncio.sleep(retry_delay)
            continue
        if result.get("refused"):
            logger.warning("type publish for %s: Mantle refused %s", server, result["refused"])
        logger.info("type publish for %s: stored %s, unchanged %s, archived %s, reindexing %s",
                    server, result.get("stored"), result.get("unchanged"),
                    result.get("archived"), result.get("offer_changed") or "nothing")
        return result
    return None
