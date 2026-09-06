"""`register_persona` returns "stop asking", not "it landed".

Its return value is what the host uses to decide whether to retry on the next backoff tick, so the
three outcomes it collapses onto two return values matter:

  * a push that landed            -> True   (nothing further to do)
  * a push that was refused       -> True   (settled; retrying burns the backoff forever)
  * a push that was inconclusive  -> False  (unreachable / 5xx; try again)

THIS FILE USED TO PIN A TWO-LEG CONTRACT, and the first leg had been dead. `register_persona`
also posted a server record to `POST /servers/register` on Mantle — a plane Mantle deliberately
removed — so every host boot 404'd and swallowed it to a `log.debug` line. The tests passed
throughout because they stubbed `_register_server` and never asked whether its target existed.

Removing it lost nothing, and `test_the_gateway_push_still_carries_the_server_identity` is the
test that says so rather than asserting it: the gateway push already sends
`{slug, endpoint, client_id}`, and crystal's `/register` registers the topology from exactly those
before it registers types.
"""
from __future__ import annotations

import ast
import io
import pathlib

from crystal import persona_registration as pr
from crystal.type_registration import PUSH_FAILED, PUSH_REFUSED


def _call(monkeypatch, *, types):
    """Run `register_persona` with the gateway push stubbed to `types`."""
    monkeypatch.setattr(pr, "push_server_types", lambda *a, **k: types)
    return pr.register_persona(name="probe", role="r", endpoint="http://e",
                               server_file="s.json")


def test_a_push_that_LANDS_is_True(monkeypatch):
    """The control for every other case here: if this did not return True, the `True` outcomes
    below would prove nothing about what True distinguishes."""
    assert _call(monkeypatch, types=1) is True


def test_a_persona_that_SHIPS_NO_TYPES_still_lands(monkeypatch):
    """`0` is "the gateway accepted, there was nothing to register" — not a failure. A persona with
    no `ui/` tree must not be retried for ever."""
    assert _call(monkeypatch, types=0) is True


def test_an_INCONCLUSIVE_push_is_False_so_the_host_RETRIES(monkeypatch):
    """Unreachable or 5xx: a later tick may well succeed.

    Fails if this returns True — the persona would serve unregistered, and nothing would retry."""
    assert _call(monkeypatch, types=PUSH_FAILED) is False


def test_a_REFUSED_push_is_True_because_the_answer_is_SETTLED(monkeypatch):
    """4xx is an answer. Retrying re-asks a settled question and burns the backoff for ever."""
    assert _call(monkeypatch, types=PUSH_REFUSED) is True


def test_TRUE_IS_NOT_A_CLAIM_THAT_THE_PERSONA_IS_REGISTERED(monkeypatch):
    """The distinction the whole contract exists for: `True` means "stop asking", and one of the
    two ways to get it is a push the gateway DECLINED."""
    assert _call(monkeypatch, types=1) is True
    assert _call(monkeypatch, types=PUSH_REFUSED) is True


# ---------------------------------------------------------------------------
# The removal, and the evidence it cost nothing
# ---------------------------------------------------------------------------


def _code_strings(path: pathlib.Path):
    """String literals that are CODE — docstrings skipped, comments never in the AST.

    Read this way on purpose: the module must be free to EXPLAIN in prose that it once posted to
    the removed plane. A check that forbids naming what it removed makes its own rationale
    unwritable."""
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    yield part.value


def test_the_removed_mantle_leg_does_not_come_back():
    """It failed on every host boot and said so only at `debug`. If it returns, it returns silent."""
    src = pathlib.Path(pr.__file__)
    offenders = [s for s in _code_strings(src) if "/servers" in s]
    assert not offenders, "persona_registration targets the removed plane again: %s" % offenders


def test_the_gateway_push_still_carries_the_server_identity():
    """WHY THE LEG COULD BE REMOVED RATHER THAN REPOINTED, asserted against both sides.

    The Mantle leg registered the persona's server record. `push_server_types` sends `slug`,
    `endpoint` and `client_id` to crystal's `/register`, whose handler calls
    `topology.register(...)` with exactly those before touching types — so the identity still
    lands, by the surviving push.

    Reads both files rather than trusting either docstring: this is the claim that made a
    deletion safe, and it is the one worth checking mechanically."""
    from crystal import type_registration as tr

    push_src = io.open(pathlib.Path(tr.__file__), encoding="utf-8").read()
    for key in ('"slug"', '"endpoint"', '"client_id"'):
        assert key in push_src, "the gateway push no longer sends %s" % key
    assert '"/register"' in push_src or "/register" in push_src

    main_src = io.open(pathlib.Path(tr.__file__).with_name("main.py"), encoding="utf-8").read()
    assert "topology.register(" in main_src, (
        "crystal's /register no longer registers the topology — the identity the removed Mantle "
        "leg used to carry would now land nowhere")
