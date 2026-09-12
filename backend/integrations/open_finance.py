from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Protocol

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class ConsentLink:
    url: str
    jti: str
    expires_at: str


@dataclass(frozen=True)
class AccountBalance:
    account_id: str
    balance_cents: int
    blocked_balance_cents: int
    scheduled_balance_cents: int


@dataclass(frozen=True)
class ExternalTransaction:
    external_id: str
    date: str
    amount_cents: int
    description: str


class OpenFinanceProvider(Protocol):
    """Provider-neutral contract. See docs/integrations/open-finance-provider-evaluation.md
    for the confirmed Stone endpoints StoneOpenFinanceProvider implements against."""

    def create_consent(self, company_id: int, return_url: str) -> ConsentLink: ...
    def list_accounts(self) -> list: ...
    def get_balance(self, account_id: str) -> AccountBalance: ...
    def list_transactions(self, account_id: str, start: date, end: date) -> list: ...
    def revoke_consent(self, consent_id: str) -> None: ...


# --------------------------------------------------------------------------
# Real adapter: Stone's own Open Banking API (not a third-party aggregator).
# Endpoints/schemas below are the ones confirmed in the evaluation doc — no
# guessed field names or invented routes.
# --------------------------------------------------------------------------

_SANDBOX_TOKEN_URL = "https://sandbox-accounts.openbank.stone.com.br/auth/realms/stone_bank/protocol/openid-connect/token"
_PRODUCTION_TOKEN_URL = "https://accounts.openbank.stone.com.br/auth/realms/stone_bank/protocol/openid-connect/token"
_SANDBOX_API_BASE = "https://sandbox-api.openbank.stone.com.br"
_PRODUCTION_API_BASE = "https://api.openbank.stone.com.br"
_SANDBOX_CONSENT_BASE = "https://sandbox.conta.stone.com.br/consentimento"
_PRODUCTION_CONSENT_BASE = "https://conta.stone.com.br/consentimento"
_CONSENT_AUDIENCE = "accounts-hubid@openbank.stone.com.br"


class StoneOpenFinanceProvider:
    def __init__(
        self,
        client_id: str,
        private_key_pem: str,
        redirect_uri: str,
        *,
        sandbox: bool = True,
        user_agent: str = "MercadoDuBairroDashboard/1.0",
        http_client=None,
    ):
        self.client_id = client_id
        self.private_key_pem = private_key_pem
        self.redirect_uri = redirect_uri
        self.sandbox = sandbox
        self.user_agent = user_agent
        self._http = http_client
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    def _client(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=15)
        return self._http

    @property
    def _token_url(self) -> str:
        return _SANDBOX_TOKEN_URL if self.sandbox else _PRODUCTION_TOKEN_URL

    @property
    def _api_base(self) -> str:
        return _SANDBOX_API_BASE if self.sandbox else _PRODUCTION_API_BASE

    @property
    def _consent_base(self) -> str:
        return _SANDBOX_CONSENT_BASE if self.sandbox else _PRODUCTION_CONSENT_BASE

    def _client_assertion(self) -> str:
        now = int(time.time())
        claims = {
            "iss": self.client_id,
            "sub": self.client_id,
            "aud": self._token_url,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 15 * 60,  # Stone rejects an assertion exp beyond 15 minutes.
        }
        return jwt.encode(claims, self.private_key_pem, algorithm="RS256")

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 10:
            return self._token
        response = self._client().post(
            self._token_url,
            data={
                "client_id": self.client_id,
                "grant_type": "client_credentials",
                "client_assertion": self._client_assertion(),
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": self.user_agent},
        )
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + body.get("expires_in", 900)
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token()}", "User-Agent": self.user_agent}

    def create_consent(self, company_id: int, return_url: str) -> ConsentLink:
        now = int(time.time())
        jti = str(uuid.uuid4())
        claims = {
            "type": "consent",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "session_metadata": {"company_id": company_id, "return_url": return_url},
            "iss": self.client_id,
            "aud": _CONSENT_AUDIENCE,
            "jti": jti,
            "iat": now,
            "nbf": now,
            "exp": now + 2 * 60 * 60,  # Stone's consent link expires after 2 hours.
        }
        token = jwt.encode(claims, self.private_key_pem, algorithm="RS256")
        url = f"{self._consent_base}?client_id={self.client_id}&jwt={token}"
        return ConsentLink(
            url=url, jti=jti,
            expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc).isoformat(),
        )

    def list_accounts(self) -> list:
        response = self._client().get(f"{self._api_base}/api/v1/accounts", headers=self._headers())
        response.raise_for_status()
        return response.json().get("data", [])

    def get_balance(self, account_id: str) -> AccountBalance:
        response = self._client().get(
            f"{self._api_base}/api/v1/accounts/{account_id}/balance", headers=self._headers()
        )
        response.raise_for_status()
        body = response.json()
        return AccountBalance(
            account_id=account_id,
            balance_cents=body["balance"],
            blocked_balance_cents=body.get("blocked_balance", 0),
            scheduled_balance_cents=body.get("scheduled_balance", 0),
        )

    def list_transactions(self, account_id: str, start: date, end: date) -> list:
        transactions = []
        cursor = None
        while True:
            params = {
                "start_datetime": f"{start.isoformat()}T00:00:00Z",
                "end_datetime": f"{end.isoformat()}T23:59:59Z",
                "limit": 100,
            }
            if cursor:
                params["after"] = cursor
            response = self._client().get(
                f"{self._api_base}/api/v1/accounts/{account_id}/statement",
                params=params, headers=self._headers(),
            )
            response.raise_for_status()
            body = response.json()
            for item in body.get("data", []):
                signed = item["amount"] if item.get("operation") == "credit" else -item["amount"]
                transactions.append(ExternalTransaction(
                    external_id=str(item["id"]),
                    date=str(item["created_at"])[:10],
                    amount_cents=signed,
                    description=item.get("type", ""),
                ))
            cursor = (body.get("cursor") or {}).get("after")
            if not cursor:
                break
        return transactions

    def revoke_consent(self, consent_id: str) -> None:
        # Stone's public docs (checked in the evaluation doc) don't expose a
        # revocation endpoint — enforcement is local: the service stops
        # syncing this connection. Nothing to call here.
        return None


# --------------------------------------------------------------------------
# Webhook verification: JWE (RSA-OAEP-256 key wrap + A256GCM) then JWS (RS256).
# --------------------------------------------------------------------------

def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decrypt_webhook_body(encrypted_body: str, private_key_pem: str) -> bytes:
    """Decrypts a Stone webhook's compact-serialized JWE body with the
    receiving application's own private key. Returns the plaintext bytes
    (a signed JWT, per Stone's docs — verify it separately)."""
    parts = encrypted_body.split(".")
    if len(parts) != 5:
        raise ValueError("Formato de webhook inválido (esperado JWE compacto de 5 partes).")
    header_b64, encrypted_key_b64, iv_b64, ciphertext_b64, tag_b64 = parts
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    content_key = private_key.decrypt(
        _b64url_decode(encrypted_key_b64),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    aesgcm = AESGCM(content_key)
    iv = _b64url_decode(iv_b64)
    ciphertext = _b64url_decode(ciphertext_b64) + _b64url_decode(tag_b64)
    return aesgcm.decrypt(iv, ciphertext, header_b64.encode())


def verify_webhook_signature(signed_jwt: str, public_key_pem: str) -> dict:
    """Verifies the JWS payload obtained from decrypt_webhook_body() against
    one of Stone's public keys (published at /api/v1/discovery/keys)."""
    return jwt.decode(signed_jwt, public_key_pem, algorithms=["RS256"], options={"verify_aud": False})
