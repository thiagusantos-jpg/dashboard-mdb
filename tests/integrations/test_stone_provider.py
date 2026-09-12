from __future__ import annotations

import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.integrations.open_finance import (
    StoneOpenFinanceProvider,
    decrypt_webhook_body,
    verify_webhook_signature,
)


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_create_consent_builds_the_documented_url_and_claims(keypair):
    private_pem, public_pem = keypair
    provider = StoneOpenFinanceProvider(
        client_id="client-123", private_key_pem=private_pem,
        redirect_uri="https://app.local/callback", sandbox=True,
    )

    consent = provider.create_consent(company_id=1, return_url="https://app.local/return")

    assert consent.url.startswith("https://sandbox.conta.stone.com.br/consentimento?client_id=client-123&jwt=")
    token = consent.url.split("jwt=")[1]
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_aud": False})
    assert claims["type"] == "consent"
    assert claims["aud"] == "accounts-hubid@openbank.stone.com.br"
    assert claims["redirect_uri"] == "https://app.local/callback"
    assert claims["exp"] - claims["iat"] == 2 * 60 * 60


def test_client_assertion_expires_within_fifteen_minutes(keypair):
    private_pem, public_pem = keypair
    provider = StoneOpenFinanceProvider(
        client_id="client-123", private_key_pem=private_pem, redirect_uri="https://app.local/callback",
    )

    assertion = provider._client_assertion()

    claims = jwt.decode(assertion, public_pem, algorithms=["RS256"], options={"verify_aud": False})
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert claims["iss"] == "client-123"


def test_webhook_decrypt_then_verify_round_trips(keypair):
    app_private_pem, app_public_pem = keypair
    stone_private_pem, stone_public_pem = keypair  # a second keypair would also work; reuse is fine for the test

    # Build the inner signed JWT the way Stone's docs describe (a JWS payload).
    inner_claims = {"event_type": "consent.approved", "target_data": {"account_id": "acc-1"}}
    signed = jwt.encode(inner_claims, stone_private_pem, algorithm="RS256")

    # Wrap it as a compact JWE: RSA-OAEP-256 key wrap + A256GCM content encryption.
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64
    import os

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    content_key = os.urandom(32)
    public_key = serialization.load_pem_public_key(app_public_pem.encode())
    encrypted_key = public_key.encrypt(
        content_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    header = b64url(json.dumps({"alg": "RSA-OAEP-256", "enc": "A256GCM"}).encode())
    iv = os.urandom(12)
    aesgcm = AESGCM(content_key)
    ciphertext_and_tag = aesgcm.encrypt(iv, signed.encode(), header.encode())
    ciphertext, tag = ciphertext_and_tag[:-16], ciphertext_and_tag[-16:]
    compact_jwe = ".".join([header, b64url(encrypted_key), b64url(iv), b64url(ciphertext), b64url(tag)])

    plaintext = decrypt_webhook_body(compact_jwe, app_private_pem)
    assert plaintext.decode() == signed

    verified = verify_webhook_signature(plaintext.decode(), stone_public_pem)
    assert verified["event_type"] == "consent.approved"
    assert verified["target_data"]["account_id"] == "acc-1"
