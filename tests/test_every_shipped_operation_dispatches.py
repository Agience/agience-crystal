"""Every operation declared in a SHIPPED type descriptor must actually dispatch.

The other dispatcher tests build their registries inline, and every one of those fixtures
supplies a `dispatch.target`. No shipped descriptor does: all twelve under
`src/types/application/` declare `{"kind": "artifact_crud"}` bare and let the operation name
carry the verb. So the suite was green against a shape the repo's own data never uses, and all
28 real artifact_crud operations answered `400 artifact_crud target 'None'` — measured
2026-08-25, 0 of 28 dispatching.

These tests read the real files rather than a fixture, which is the only reason they would have
caught it. Keep them that way: a fixture here re-arms exactly the defect they exist to hold.
"""
import asyncio
import json
import pathlib

import pytest
from fastapi import HTTPException

from crystal.dispatcher import Dispatcher
from crystal.topology import Topology
from crystal.type_registry import TypeRegistry


class FakeMantle:
    """Records calls and answers everything. Deliberately minimal: this suite is about whether
    an operation REACHES a handler, not about what Mantle does with it."""

    def __init__(self, artifact):
        self.artifact = artifact
        self.calls = []

    async def my_access(self, resource_id, action, *, token=None):
        self.calls.append(("my_access", resource_id, action))
        return True

    async def get_artifact(self, aid, *, token=None, hydrate=False):
        self.calls.append(("get", aid))
        return self.artifact

    async def patch(self, path, body, *, token=None):
        self.calls.append(("patch", path))
        return {"patched": True}

    async def post(self, path, body, *, token=None):
        self.calls.append(("post", path))
        return {"posted": True}

    async def delete(self, path, *, token=None):
        self.calls.append(("delete", path))
        return {"deleted": True}

    async def search(self, body, *, token=None):
        self.calls.append(("search",))
        return {"hits": []}


TYPES_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "types" / "application"

#: The verbs `Dispatcher._artifact_crud` can route. An operation name outside this set must
#: declare an explicit target, and this list is what says so.
CRUD_VERBS = {"create", "add", "read", "update", "delete", "search"}


def _shipped():
    for p in sorted(TYPES_DIR.glob("*/type.json")):
        yield p, json.loads(p.read_text(encoding="utf-8"))


def _crud_ops():
    """Operations that route through `_artifact_crud` — declared as such, or defaulted there.

    An operation with an EMPTY dispatch block belongs here too. `Dispatcher._run_create` already
    treats an absent operation as artifact_crud, so a declared-but-empty one answering
    `500 Unknown dispatch kind ''` made declaring it worse than omitting it. Six shipped
    operations were in that state on 2026-08-25.
    """
    for p, d in _shipped():
        for name, spec in (d.get("operations") or {}).items():
            kind = (spec.get("dispatch") or {}).get("kind") or "artifact_crud"
            if kind == "artifact_crud":
                yield pytest.param(d["content_type"], name, d, id="%s-%s" % (p.parent.name, name))


def test_an_operation_declared_with_no_dispatch_block_is_not_worse_than_omitting_it():
    """The trap that motivated the default: `{"enabled": true}` with no dispatch block.

    Omitting the operation entirely gets artifact_crud from `_run_create`'s own fallback, so a
    declared one must not fail where an absent one succeeds.
    """
    empty = [(d["content_type"], name)
             for _, d in _shipped()
             for name, spec in (d.get("operations") or {}).items()
             if not (spec.get("dispatch") or {}).get("kind")]
    reg = TypeRegistry()
    reg.register({"content_type": "x/y", "operations": {"read": {"enabled": True}}}, server="x")
    op = reg.resolve_operation("x/y", "read")
    assert op is not None and op.kind == "artifact_crud", (
        "an empty dispatch block must resolve to artifact_crud, not %r — %d shipped "
        "operations depend on it" % (op.kind if op else None, len(empty)))


def test_there_are_shipped_descriptors_to_check():
    """Guards the parametrised tests below: an empty glob would make them vacuously green."""
    found = list(_shipped())
    assert len(found) >= 10, "expected the shipped descriptors, found %d" % len(found)


@pytest.mark.parametrize("content_type,op_name,definition", list(_crud_ops()))
def test_a_shipped_artifact_crud_op_dispatches(content_type, op_name, definition):
    reg = TypeRegistry()
    reg.register(definition, server="crystal")
    mantle = FakeMantle({"id": "a1", "content_type": content_type})
    disp = Dispatcher(mantle, reg, Topology())
    try:
        asyncio.run(disp.dispatch("a1", op_name, {}, token="tok"))
    except HTTPException as exc:  # pragma: no cover - the failure we are holding
        pytest.fail("%s.%s did not dispatch: HTTP %s %s"
                    % (content_type, op_name, exc.status_code, exc.detail))


@pytest.mark.parametrize("content_type,op_name,definition", list(_crud_ops()))
def test_an_artifact_crud_op_name_is_a_verb_or_says_otherwise(content_type, op_name, definition):
    """The fix defaults `target` to the operation name, which only works while the name IS a
    verb. A descriptor adding `archive` or `publish` as artifact_crud must name its target;
    without this test that lands as another silent 400."""
    target = ((definition["operations"][op_name].get("dispatch") or {}).get("target")
              or op_name).strip().lower()
    assert target in CRUD_VERBS, (
        "%s.%s resolves to target %r, which Dispatcher._artifact_crud cannot route. "
        "Either rename the operation to a verb or declare an explicit dispatch.target."
        % (content_type, op_name, target))
