"""Review 15: Crystal takes an Origin token only from the manifest's issuer, the same check prism
makes (`verify_origin_jwt`): a token signed by a key the manifest trusts but carrying another
issuer (a staging Origin's) is refused."""
from __future__ import annotations

from crystal import caller_token as C


def test_crystal_checks_the_issuer_claim_as_prism_does(monkeypatch):
    from prism.trust import authority_trust as at

    asked = {}

    def verify_jwt(token, *, expected_issuer_service, expected_audience=None,
                   expected_issuer_claim=None):
        asked["iss"] = expected_issuer_claim
        if expected_issuer_claim != "https://origin.test":
            raise ValueError("issuer")
        return {"sub": "u", "iss": "https://origin.test"}

    class _Manifest:
        issuer = "https://origin.test"

    monkeypatch.setattr(at, "verify_jwt", verify_jwt)
    monkeypatch.setattr(at, "get_authority_manifest", lambda: _Manifest())
    assert C._verify_origin("a.b.c") == {"sub": "u", "iss": "https://origin.test"}
    assert asked["iss"] == "https://origin.test"
