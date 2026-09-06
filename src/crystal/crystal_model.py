"""The crystal contract — re-exported from its canonical home `prism.crystal_model`.

The contract lives in prism (the host owns the crystal wire-format + junction gate); crystal
depends on prism, so this re-export keeps every `crystal.crystal_model` importer working against
one definition, with no duplication. Apache consumers that must verify a crystal without importing
crystal (prism itself) import `prism.crystal_model` directly."""
from prism.crystal_model import *  # noqa: F401,F403

# Re-exported with redundant aliases, 2026-08-25. The comment here used to read "explicit for
# linters / __all__ parity" and there was no `__all__`, so every name below read as an unused import
# — 8 of them, and the whole of this repository's lint regression that day.
#
# `name as name` is the documented way to say "this is a re-export, not a stray import" (PEP 484 §
# stub conventions, honoured by ruff and mypy). It is used instead of writing an `__all__` here
# because `prism.crystal_model` already defines one: a second list in this file would be a copy that
# drifts, and this module exists precisely so there is ONE definition. The star import above carries
# whatever prism's `__all__` names; these aliases pin the subset importers reach for by name.
from prism.crystal_model import (
    CRYSTAL_CONTENT_TYPE as CRYSTAL_CONTENT_TYPE,
    FACET_DIRECTIONS as FACET_DIRECTIONS,
    canonical_json as canonical_json,
    crystal_sha as crystal_sha,
    validate as validate,
    required_capabilities as required_capabilities,
    activates_on as activates_on,
    capability_reach as capability_reach,
    crystal_artifact as crystal_artifact,
    verify as verify,
)
