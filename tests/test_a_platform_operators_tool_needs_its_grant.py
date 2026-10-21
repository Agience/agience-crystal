"""A persona tool the platform offers as an operator is reached directly only by a caller who may
invoke that operator.

2026-10-21: the platform's operators are granted per account so each can be withheld, but the tool
each runs was one `POST /personas/{slug}/tools/call` away for everyone.
"""
import asyncio

import pytest
from fastapi import HTTPException

from crystal.operator_gate import OperatorGate

OFFERED = [{"slug": "op-reconcile-accounts", "server": "lumen", "tool": "reconcile", "may_invoke": False},
           {"slug": "op-list-my-collections", "server": "sage", "tool": "browse_collections", "may_invoke": True}]


class FakeMantle:
    def __init__(self, items=OFFERED, fail=False):
        self.items, self.fail, self.asked = items, fail, 0

    async def platform_operators(self, *, token=None):
        self.asked += 1
        if self.fail:
            raise RuntimeError("mantle down")
        return self.items


def _check(gate, slug, tool, token="t"):
    asyncio.run(gate.check(slug, tool, token))


def test_a_withheld_operators_tool_is_refused_by_name():
    with pytest.raises(HTTPException) as e:
        _check(OperatorGate(FakeMantle()), "lumen", "reconcile")
    assert e.value.status_code == 403 and "op-reconcile-accounts" in e.value.detail


def test_a_granted_operators_tool_and_a_tool_no_operator_runs_pass():
    gate = OperatorGate(FakeMantle())
    assert asyncio.run(gate.check("sage", "browse_collections", "t")) is None
    assert asyncio.run(gate.check("lumen", "workflow_catalog", "t")) is None


def test_a_persons_own_server_is_left_to_iris():
    mantle = FakeMantle()
    _check(OperatorGate(mantle), "0f9e2f34-2f09-5346-bd67-518a39ab96b9", "reconcile")
    assert mantle.asked == 0


def test_the_answer_is_kept_briefly_per_caller_and_a_withdrawal_arrives_after():
    now = [0.0]
    mantle = FakeMantle(items=[{**OFFERED[1]}])
    gate = OperatorGate(mantle, ttl=30, clock=lambda: now[0])
    _check(gate, "sage", "browse_collections")
    _check(gate, "sage", "browse_collections")
    assert mantle.asked == 1
    _check(gate, "sage", "browse_collections", token="someone-else")
    assert mantle.asked == 2
    mantle.items = [{**OFFERED[1], "may_invoke": False}]
    now[0] = 31
    with pytest.raises(HTTPException):
        _check(gate, "sage", "browse_collections")


def test_the_gate_fails_closed():
    with pytest.raises(HTTPException) as e:
        _check(OperatorGate(FakeMantle(fail=True)), "lumen", "workflow_catalog")
    assert e.value.status_code == 503
