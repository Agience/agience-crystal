"""Minimal MCP (streamable-http) tool caller for `mcp_tool` dispatch.

Does the small handshake personas expect — initialize → notifications/initialized →
tools/call — forwarding the caller's bearer token so the persona enforces its own
auth. Parses the JSON-RPC result out of a JSON or SSE response.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

_CLIENT_INFO = {"name": "agience-crystal", "version": "0.1.0"}


def _rpc(rid: int, method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def _parse_result(resp: httpx.Response) -> Any:
    ctype = resp.headers.get("content-type", "")
    payload: Optional[dict] = None
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    cand = json.loads(line[5:].strip())
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(cand, dict) and ("result" in cand or "error" in cand):
                    payload = cand
    else:
        try:
            payload = resp.json()
        except Exception:
            payload = None
    if payload is None:
        return {"raw": resp.text}
    if "error" in payload:
        err = payload["error"]
        raise HTTPException(status_code=502, detail=f"MCP tool error: {err}")
    return payload.get("result", payload)


class McpCaller:
    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def call_tool(self, endpoint: str, tool: str, arguments: dict,
                        *, token: Optional[str] = None) -> Any:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            init = await c.post(endpoint, headers=headers, json=_rpc(
                1, "initialize",
                {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": _CLIENT_INFO}))
            init.raise_for_status()
            session = init.headers.get("mcp-session-id")
            h = dict(headers)
            if session:
                h["mcp-session-id"] = session
            await c.post(endpoint, headers=h,
                         json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            resp = await c.post(endpoint, headers=h, json=_rpc(
                2, "tools/call", {"name": tool, "arguments": arguments or {}}))
            resp.raise_for_status()
            return _parse_result(resp)
