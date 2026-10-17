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

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: The `server` a builtin type is recorded under.
BUILTIN_SERVER = "crystal"


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
    """In-memory `content_type → TypeDefinition`.

    Two sources, one lookup. Personas register the types they own (`register`, replaced wholesale
    when a persona re-registers). The gateway's builtin types — the collection, the workspace,
    the base types others name in `inherits` — are loaded once at startup (`load_builtin`) and
    kept apart, so a persona re-registering never removes them; a type a persona registers under
    the same content type takes precedence over the builtin.

    `inherits` is resolved here. A type answers for the operations it declares, and for each one
    it does not declare, for the first of its `inherits` (in order, depth first) that does — so a
    workspace, which declares only `create`, reads, updates and deletes as a collection does.
    """

    #: How deep an `inherits` chain is followed. Shipped chains are one or two links; this only
    #: stops a malformed definition from walking forever.
    MAX_INHERITANCE_DEPTH = 8

    def __init__(self) -> None:
        self._cache: Dict[str, TypeDefinition] = {}
        self._builtin: Dict[str, TypeDefinition] = {}

    def register(self, definition: dict, *, server: Optional[str] = None) -> bool:
        td = parse_type_def(definition, server=server)
        if td is None:
            logger.warning("Ignored malformed type def from server=%s", server)
            return False
        self._cache[td.content_type] = td
        return True

    def load_builtin(self, root: Path) -> int:
        """Load every `<top>/<sub>/type.json` under ``root`` as a builtin type (`_wildcard` names
        the category, `<top>/*`). Returns how many loaded; a missing ``root`` loads none."""
        if not root.is_dir():
            logger.warning("No builtin types at %s: inheritance resolves among persona types only",
                           root)
            return 0
        n = 0
        for path in sorted(root.glob("*/*/type.json")):
            top, sub = path.parent.parent.name, path.parent.name
            try:
                definition = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                logger.warning("Skipping unreadable builtin type at %s", path)
                continue
            if not isinstance(definition, dict):
                continue
            definition.setdefault("content_type", f"{top}/{'*' if sub == '_wildcard' else sub}")
            td = parse_type_def(definition, server=BUILTIN_SERVER)
            if td is not None:
                self._builtin[td.content_type] = td
                n += 1
        logger.info("Loaded %d builtin type(s) from %s", n, root)
        return n

    def resolve(self, content_type: str) -> Optional[TypeDefinition]:
        ct = normalize_ct(content_type)
        return self._cache.get(ct) or self._builtin.get(ct)

    def _lineage(self, content_type: str) -> List[TypeDefinition]:
        """The type, then each type it inherits from, depth first in declared order, each once."""
        out: List[TypeDefinition] = []
        seen = set()

        def walk(ct: str, depth: int) -> None:
            ct = normalize_ct(ct)
            if ct in seen or depth > self.MAX_INHERITANCE_DEPTH:
                return
            seen.add(ct)
            td = self.resolve(ct)
            if td is None:
                return
            out.append(td)
            parents = td.raw.get("inherits") if isinstance(td.raw, dict) else None
            for parent in parents if isinstance(parents, list) else []:
                if isinstance(parent, str):
                    walk(parent, depth + 1)

        walk(content_type, 0)
        return out

    def operations_of(self, content_type: str) -> Dict[str, OperationSpec]:
        """Every operation the type answers for, its own and inherited; its own win."""
        ops: Dict[str, OperationSpec] = {}
        for td in reversed(self._lineage(content_type)):
            ops.update(td.operations)
        return ops

    def resolve_operation(self, content_type: str, op_name: str) -> Optional[OperationSpec]:
        for td in self._lineage(content_type):
            op = td.operations.get(op_name)
            if op is not None:
                return op
        return None

    def describer_for(self, content_type: str) -> Optional[dict]:
        td = self.resolve(content_type)
        return td.describer if td else None

    def definitions_from(self, server: str) -> List[dict]:
        """The definitions ``server`` contributed, as it sent them (the builtins for
        :data:`BUILTIN_SERVER`)."""
        source = self._builtin if server == BUILTIN_SERVER else self._cache
        return [td.raw for _ct, td in sorted(source.items()) if td.server == server]

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
        return sorted(set(self._cache) | set(self._builtin))
