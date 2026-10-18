"""The personas, by name: what each serves, its `ui://` views, and its tools.

A content type names the persona that serves its viewer (`ui.resource_server`, e.g. `seraph`) and
the view (`ui.resource_uri`, `ui://seraph/vnd.agience.key.html`). The client reads that view, and
the view calls tools, on that persona. Crystal holds the only `slug -> endpoint` map (`Topology`,
filled by `POST /register`), so it is the one place a name becomes a reachable MCP surface.

2026-10-18: the client resolved a persona's name to an MCP-server artifact and dispatched
`resources_read` / `invoke` on it. No such artifact exists, the `resources_read` op pointed at a
Mantle primitive that was removed, and every `vnd.agience.*` viewer said its server was not running.

The caller's bearer token is forwarded, so the persona enforces its own access, as for `mcp_tool`
dispatch. A persona that is not registered, or does not answer, is reported as not running (503).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class PersonaGateway:
    def __init__(self, topology, mcp) -> None:
        self.topology = topology
        self.mcp = mcp

    def _endpoint(self, slug: str) -> str:
        rec = self.topology.resolve(slug)
        if rec is None:
            raise HTTPException(status_code=503,
                                detail=f"The {slug} server is not running (not registered with the gateway)")
        return rec.endpoint

    async def _call(self, slug: str, method: str, params: dict, token: Optional[str]) -> Any:
        endpoint = self._endpoint(slug)
        try:
            return await self.mcp.request(endpoint, method, params, token=token)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise HTTPException(status_code=status, detail=f"{slug} refused {method}")
            raise HTTPException(status_code=502, detail=f"{slug} answered {method} with {status}")
        except httpx.TransportError as exc:
            logger.warning("persona %s unreachable for %s: %s", slug, method, exc)
            raise HTTPException(status_code=503, detail=f"The {slug} server is not running")

    async def read_resource(self, slug: str, uri: str, token: Optional[str]) -> Dict[str, Any]:
        result = await self._call(slug, "resources/read", {"uri": uri}, token)
        contents = (result or {}).get("contents") or []
        if not contents:
            raise HTTPException(status_code=404, detail=f"{slug} has no resource {uri}")
        c = contents[0]
        return {"uri": c.get("uri", uri), "name": c.get("name") or uri,
                "mimeType": c.get("mimeType"), "text": c.get("text"), "blob": c.get("blob")}

    async def call_tool(self, slug: str, name: str, arguments: dict, token: Optional[str]) -> Any:
        return await self._call(slug, "tools/call", {"name": name, "arguments": arguments or {}}, token)

    async def describe(self, slug: str, token: Optional[str]) -> Dict[str, Any]:
        """What the persona serves, in the client's server-info shape; `status: down` when it does
        not answer, so one persona never hides the others."""
        info: Dict[str, Any] = {"server": slug, "name": slug, "tools": [], "resources": [],
                                "prompts": [], "status": "ok"}
        try:
            tools = await self._call(slug, "tools/list", {}, token)
            resources = await self._call(slug, "resources/list", {}, token)
        except HTTPException as exc:
            return {**info, "status": "down", "error": exc.detail}
        info["tools"] = [{"name": t.get("name"), "description": t.get("description"),
                          "input_schema": t.get("inputSchema")}
                         for t in (tools or {}).get("tools") or [] if t.get("name")]
        info["resources"] = [{"id": r.get("uri"), "kind": "resource", "uri": r.get("uri"),
                              "title": r.get("title") or r.get("name"),
                              "content_type": r.get("mimeType")}
                             for r in (resources or {}).get("resources") or [] if r.get("uri")]
        return info

    async def list(self, token: Optional[str]) -> List[Dict[str, Any]]:
        return list(await asyncio.gather(*(self.describe(s, token) for s in sorted(self.topology.known()))))
