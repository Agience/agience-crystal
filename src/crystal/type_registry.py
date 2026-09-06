"""Type registry — `content_type → operations`, owned by the gateway.

Personas **self-register** their type definitions with the gateway (`POST /register`);
the gateway holds the live registry. **Mantle never sees types** — it stores a
`content_type` label and nothing more. A pushed type definition is::

    { "content_type": "application/vnd.agience.xyz+json",
      "operations": { "<op>": { "dispatch": {"kind": ..., "target": ...},
                                "requires_grant": ..., "emits": [...], "audit": ... } },
      "context_schema": { ... },
      "describer": { "kind": "mcp_tool", "target": "<persona>.<tool>" } }
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def normalize_ct(content_type: str) -> str:
    ct = (content_type or "").strip().lower()
    if ";" in ct:
        ct = ct.split(";", 1)[0].strip()
    return ct


@dataclass(frozen=True)
class OperationSpec:
    name: str
    kind: str                      # dispatch.kind: mcp_tool | native | artifact_crud
    target: Optional[str]          # dispatch.target — "persona.tool", the flat form
    #: The referential form every shipped mcp_tool descriptor actually uses. A value may be a
    #: literal ("lumen") or a `$.`-rooted path resolved per call against the artifact and the
    #: request body — the tool can be named by the CALLER (`$.body.name`) or by the artifact's
    #: own content (`$.context.tool_name`), which a flat `target` cannot express.
    server_ref: Optional[str] = None
    tool_ref: Optional[str] = None
    arguments: Any = None          # dict of name -> literal-or-path, or a single path
    requires_grant: Optional[str] = None
    emits: List[dict] = field(default_factory=list)
    audit: bool = False


@dataclass
class TypeDefinition:
    content_type: str
    operations: Dict[str, OperationSpec]
    describer: Optional[dict]
    server: Optional[str]          # slug of the persona that registered this type
    raw: dict


def parse_type_def(definition: dict, *, server: Optional[str] = None) -> Optional[TypeDefinition]:
    """Build a TypeDefinition from a pushed type def, or None if malformed."""
    if not isinstance(definition, dict):
        return None
    target = normalize_ct(str(definition.get("content_type") or ""))
    if not target:
        return None

    ops: Dict[str, OperationSpec] = {}
    for name, spec in (definition.get("operations") or {}).items():
        if not isinstance(spec, dict):
            continue
        dispatch = spec.get("dispatch") or {}
        ops[name] = OperationSpec(
            name=name,
            # An absent operation already defaults to artifact_crud in
            # `Dispatcher._run_create`, so a DECLARED one with an empty dispatch block
            # must not be worse than not declaring it at all — it was, answering
            # `500 Unknown dispatch kind ''` on 6 shipped operations. A kind that is
            # present but unrecognised still fails loudly; only an absent one defaults.
            kind=str(dispatch.get("kind") or "artifact_crud"),
            # An operation whose name IS its verb needs no `target`, and no shipped
            # descriptor writes one: all 12 declare `{"kind": "artifact_crud"}` bare.
            # Without this default every one of them reached `_artifact_crud` with
            # target=None and answered 400. An explicit target still wins, so a name
            # that is not a verb (`commit` -> `update`) keeps mapping as declared.
            target=dispatch.get("target") or name,
            server_ref=dispatch.get("server_ref"),
            tool_ref=dispatch.get("tool_ref"),
            arguments=dispatch.get("arguments"),
            requires_grant=spec.get("requires_grant"),
            emits=list(spec.get("emits") or []),
            audit=bool(spec.get("audit")),
        )
    return TypeDefinition(
        content_type=target,
        operations=ops,
        describer=definition.get("describer"),
        server=server,
        raw=definition,
    )


class TypeRegistry:
    """In-memory `content_type → TypeDefinition`, populated by persona registration."""

    def __init__(self) -> None:
        self._cache: Dict[str, TypeDefinition] = {}

    def register(self, definition: dict, *, server: Optional[str] = None) -> bool:
        td = parse_type_def(definition, server=server)
        if td is None:
            logger.warning("Ignored malformed type def from server=%s", server)
            return False
        self._cache[td.content_type] = td
        return True

    def resolve(self, content_type: str) -> Optional[TypeDefinition]:
        return self._cache.get(normalize_ct(content_type))

    def resolve_operation(self, content_type: str, op_name: str) -> Optional[OperationSpec]:
        td = self.resolve(content_type)
        return td.operations.get(op_name) if td else None

    def describer_for(self, content_type: str) -> Optional[dict]:
        td = self.resolve(content_type)
        return td.describer if td else None

    def remove_server(self, server: str) -> int:
        """Drop all types registered by a server (e.g. on re-register/deregister)."""
        drop = [ct for ct, td in self._cache.items() if td.server == server]
        for ct in drop:
            del self._cache[ct]
        return len(drop)

    def invalidate(self, content_type: Optional[str] = None) -> None:
        if content_type is None:
            self._cache.clear()
        else:
            self._cache.pop(normalize_ct(content_type), None)

    def known(self) -> List[str]:
        return sorted(self._cache.keys())
