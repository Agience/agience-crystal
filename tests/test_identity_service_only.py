"""A valid signature from a trust anchor is not by itself a platform service.

`verify_service_token` gates `POST /register`, which repoints a persona slug at an arbitrary URL.
Accepting any RS256 token signed by any trust anchor would accept this one too: `origin` is a
trust anchor and Origin signs ordinary end-user access tokens, so every signed-up user would hold
a token the gateway called a platform service, and re-registering a persona would forward every
user's delegation JWT to the attacker's endpoint.

The separating claim is `principal_type: "service"`, which platform services stamp
(`CrystalIdentity.service_jwt`, `origin.service_identity`) and no end-user token carries.
"""
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk
from jose import jwt

from crystal.identity import verify_service_token


@pytest.fixture(scope="module")
def anchor():
    """A real RSA trust anchor named `origin`, shaped like the authority manifest's JWKS."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    jwks = {"keys": [jose_jwk.construct(pub_pem, algorithm="RS256").to_dict()]}
    return pem, {"origin": jwks}


def _mint(pem, **claims):
    now = int(time.time())
    base = {"iss": "https://origin.test", "sub": "user-123", "aud": "agience",
            "iat": now, "exp": now + 300}
    base.update(claims)
    return jwt.encode(base, pem, algorithm="RS256")


def test_an_ordinary_end_user_token_is_not_a_service(anchor):
    """The escalation. This token is genuinely signed by a trust anchor — that is the point."""
    pem, anchors = anchor
    assert verify_service_token(_mint(pem), anchors) is False


def test_a_user_token_cannot_buy_its_way_in_with_other_claims(anchor):
    """`principal_type` is the check; nothing else a user controls substitutes for it."""
    pem, anchors = anchor
    for claims in (
        {"principal_type": "user"},
        {"principal_type": "delegation"},
        {"principal_type": "mcp_client"},
        {"roles": ["platform:admin"]},
        {"aud": "crystal"},
        {"iss": "mantle"},
    ):
        assert verify_service_token(_mint(pem, **claims), anchors) is False, claims


def test_a_genuine_service_token_is_accepted(anchor):
    """The positive control: without it, the rejections above would pass even with the function
    stubbed out."""
    pem, anchors = anchor
    tok = _mint(pem, principal_type="service", sub="mantle", iss="mantle")
    assert verify_service_token(tok, anchors) is True


def test_an_unsigned_or_foreign_token_is_rejected(anchor):
    """Signature is still necessary: the claim check is additional to it, not a replacement for it."""
    _pem, anchors = anchor
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    assert verify_service_token("not.a.jwt", anchors) is False
    assert verify_service_token(_mint(other_pem, principal_type="service"), anchors) is False


def test_no_anchors_means_no_token_verifies(anchor):
    pem, _anchors = anchor
    assert verify_service_token(_mint(pem, principal_type="service"), {}) is False
