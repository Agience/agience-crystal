"""Persona topology — `slug → endpoint`, owned by the gateway.

Personas self-register their reachable endpoint with the gateway (same `POST
/register` call that pushes their types). For `mcp_tool` dispatch, the gateway
resolves a dispatch target like ``aria.render`` to the persona ``aria`` and looks
up its endpoint here. Mantle holds no persona records, so this map is the
gateway's own source for slug -> endpoint resolution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerRecord:
    slug: str
    endpoint: str               # reachable URL for the persona's MCP surface
    client_id: Optional[str] = None


class Topology:
    def __init__(self) -> None:
        self._by_slug: Dict[str, ServerRecord] = {}

    def register(self, slug: str, endpoint: str, client_id: Optional[str] = None) -> None:
        slug = (slug or "").strip()
        if not slug or not endpoint:
            return
        self._by_slug[slug] = ServerRecord(slug=slug, endpoint=endpoint, client_id=client_id)

    def resolve(self, slug: str) -> Optional[ServerRecord]:
        return self._by_slug.get((slug or "").strip())

    def remove(self, slug: str) -> None:
        self._by_slug.pop((slug or "").strip(), None)

    def known(self) -> List[str]:
        return sorted(self._by_slug.keys())
