"""Unit tests for the crystal gateway service identity (service-JWT signing).

Guards the `kid`/`iss` the platform trust root expects: the authority manifest
(deploy/init.py) registers the gateway as `crystal` with kid `crystal-1`, so the
gateway signs its service JWTs with those exact values, or Mantle/Origin reject them.
"""
from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from crystal.identity import CrystalIdentity


def _test_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_service_jwt_kid_matches_manifest_anchor():
    """Header kid must be `crystal-1` (matches the manifest JWKS entry init.py writes)."""
    token = CrystalIdentity(_test_pem(), "http://origin:8080").service_jwt("mantle")
    assert jwt.get_unverified_header(token)["kid"] == "crystal-1"


def test_service_jwt_issuer_and_claims():
    """iss=sub=crystal, service principal, and the requested audience."""
    token = CrystalIdentity(_test_pem(), "http://origin:8080").service_jwt("mantle")
    claims = jwt.get_unverified_claims(token)
    assert claims["iss"] == "crystal"
    assert claims["sub"] == "crystal"
    assert claims["aud"] == "mantle"
    assert claims["principal_type"] == "service"


def test_service_jwt_audience_is_per_call():
    ident = CrystalIdentity(_test_pem(), "http://origin:8080")
    assert jwt.get_unverified_claims(ident.service_jwt("origin"))["aud"] == "origin"
    assert jwt.get_unverified_claims(ident.service_jwt("mantle"))["aud"] == "mantle"
