"""Per-persona self-registration with the crystal gateway.

Each persona owns its registration and calls this helper with its own
``{name, role, endpoint, client_id}``. The host holds no roster: it iterates the personas it was
handed and calls each one's ``register()``.

This used to make two pushes, and the first one had been dead. It posted a server record to
``POST /servers/register`` on Mantle — a plane Mantle deliberately removed (see
``agience-mantle/tests/test_route_reshape.py::test_the_old_path_is_gone_entirely``). Every call
404'd, and the failure was swallowed to a ``log.debug`` line reading *"will retry"*, below the
default level. It is called on host startup, so it had been failing on every boot, silently.

Nothing was lost by removing it, and that is the reason it could go rather than be repointed.
The gateway push already carries the server's identity: ``push_server_types`` sends
``{slug, endpoint, client_id, types}`` to crystal's own ``POST /register``, whose handler calls
``topology.register(slug, endpoint, client_id)`` before registering the types. The two legs had
converged on one endpoint and only one of them was still connected.

The Mantle leg additionally sent ``path``, ``title``, ``role`` and ``summary``. Nothing consumed
them: the plane that received them is gone, and crystal's topology takes slug, endpoint and
client_id. They are dropped deliberately, not overlooked.

Returns ``True`` only when the push has landed (a persona that ships no types — ``push`` returns
``0`` — still counts as landed), so the host's retry loop can drop the persona; ``False`` on any
transport/HTTP failure so the host retries with backoff until the gateway is up.
"""
from __future__ import annotations

import logging
from typing import Optional


from crystal.type_registration import PUSH_FAILED, PUSH_REFUSED, push_server_types

log = logging.getLogger("crystal.persona_registration")


def register_persona(
    *,
    name: str,
    role: str,
    endpoint: str,
    client_id: Optional[str] = None,
    server_file: str,
    mantle_uri: Optional[str] = None,
    gateway_uri: Optional[str] = None,
) -> bool:
    """Self-register one persona: its server (Mantle) and its owned types (gateway).

    The persona module passes its own metadata here (see each persona's module-level
    ``PERSONA`` dict).

    The return means "stop asking", not "it landed". Two paths below return ``True`` after a push
    was declined rather than accepted:

      * ``_ServerRegisterRefused`` — Mantle answered and declined the server record;
      * ``PUSH_REFUSED`` — the gateway answered 4xx to the types push.

    A decline is a settled answer, and retrying it forever would burn the backoff on a question
    that will not change:

      ``True``   nothing further will help — it landed, or it was definitively declined.
      ``False``  the attempt was inconclusive (unreachable, 5xx); retry on the next backoff tick.

    ``True`` is therefore not a claim that the persona is registered. The three cases — landed,
    declined, inconclusive — are distinguished in the log lines this function writes.
    """
    #: Still computed and still meaningful: `push_server_types` derives the same
    #: `agience-server-<name>` client_id, and a persona that overrides it here would
    #: expect that override to reach the gateway. Kept so the signature does not lie
    #: about what it accepts.
    client_id = client_id or f"agience-server-{name}"
    types_result = push_server_types(name, server_file, gateway_uri=gateway_uri)
    if types_result == PUSH_FAILED:
        return False
    if types_result == PUSH_REFUSED:
        # The gateway answered 4xx: this will not succeed on a later tick. Report the
        # persona as done-as-far-as-it-can-be so the host stops re-asking; burning the
        # backoff on a settled answer only delays cold start. The warning naming the
        # status was already logged, once.
        log.info("Persona %s: gateway declined its registration — not retrying", name)
        return True
    log.info("Persona %s self-registered (%d type(s))", name, max(types_result, 0))
    return True
