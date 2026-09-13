from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Optional

from . import database as db


_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DUMMY_SALT = bytes.fromhex("00" * 16)


def _derive(password: bytes, salt: bytes) -> bytes:
    if hasattr(hashlib, "scrypt"):
        return hashlib.scrypt(
            password,
            salt=salt,
            n=16384,
            r=8,
            p=1,
            dklen=64,
        )
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=64, n=16384, r=8, p=1).derive(password)


_DUMMY_HASH = _derive(b"invalid-password", _DUMMY_SALT)


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 254 or not _EMAIL.fullmatch(normalized):
        raise ValueError("E-mail inválido.")
    return normalized


def _password_digest(password: str, salt: bytes) -> bytes:
    if not password or len(password) > 200:
        raise ValueError("A senha deve ter entre 1 e 200 caracteres.")
    return _derive(password.encode("utf-8"), salt)


def users_exist() -> bool:
    with db.connection() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def user_by_email(email: str) -> Optional[dict]:
    try:
        normalized = _normalize_email(email)
    except ValueError:
        return None
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (normalized,)).fetchone()
    return dict(row) if row else None


def create_user(
    email: str,
    password: str,
    name: str,
    *,
    is_admin: bool = False,
) -> dict:
    normalized = _normalize_email(email)
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 120:
        raise ValueError("O nome deve ter entre 1 e 120 caracteres.")
    salt = secrets.token_bytes(16)
    digest = _password_digest(password, salt)
    user_id = secrets.randbits(63) or 1
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO users(
                id,email,name,password_hash,password_salt,active,is_admin,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                normalized,
                clean_name,
                digest.hex(),
                salt.hex(),
                1,
                int(is_admin),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row)


def verify_credentials(email: str, password: str) -> Optional[dict]:
    user = user_by_email(email)
    if user:
        salt = bytes.fromhex(user["password_salt"])
        expected = bytes.fromhex(user["password_hash"])
    else:
        salt = _DUMMY_SALT
        expected = _DUMMY_HASH
    try:
        actual = _password_digest(password, salt)
    except ValueError:
        actual = b""
    valid = hmac.compare_digest(actual, expected)
    if not user or not valid or not user["active"]:
        return None
    return user


def reset_password(email: str, new_password: str) -> Optional[dict]:
    user = user_by_email(email)
    if not user:
        return None
    salt = secrets.token_bytes(16)
    digest = _password_digest(new_password, salt)
    with db.connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET password_hash=?, password_salt=?, updated_at=?, version=version+1
            WHERE id=?
            """,
            (digest.hex(), salt.hex(), db.now(), user["id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    return user


def disable_user(user_id: int) -> None:
    with db.connection() as conn:
        changed = conn.execute(
            """
            UPDATE users
            SET active=0, updated_at=?, version=version+1
            WHERE id=? AND active=1
            """,
            (db.now(), user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        if getattr(changed, "rowcount", 1) == 0:
            raise ValueError("Usuário não encontrado ou já desativado.")


class ProfileError(ValueError):
    """A self-service account change the caller can fix (field error, stale
    version, taken e-mail). Carries the HTTP status and the fields to flag."""

    def __init__(self, message: str, *, status: int = 422, fields=(), code: str = "invalid_fields"):
        super().__init__(message)
        self.status = status
        self.fields = list(fields)
        self.code = code


def _active_user(conn, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
    if row is None:
        raise ProfileError("Usuário não encontrado.", status=404, code="not_found")
    return dict(row)


def _password_matches(user: dict, password: Optional[str]) -> bool:
    try:
        actual = _password_digest(password or "", bytes.fromhex(user["password_salt"]))
    except ValueError:
        actual = b""
    return hmac.compare_digest(actual, bytes.fromhex(user["password_hash"]))


def _require_current_password(user: dict, password: Optional[str]) -> None:
    if not password:
        raise ProfileError("Informe sua senha atual para confirmar.", fields=["current_password"])
    if not _password_matches(user, password):
        raise ProfileError("Senha atual incorreta.", fields=["current_password"])


def _check_version(user: dict, expected_version: int) -> None:
    if user["version"] != expected_version:
        raise ProfileError(
            "Sua conta foi alterada em outra sessão. Reabra Minha conta e tente de novo.",
            status=409,
            code="version_conflict",
        )


def _bump(conn, user_id: int, expected_version: int, assignments: str, params: tuple) -> dict:
    # The version guard lives in the UPDATE itself, so two concurrent saves can
    # never both win; RETURNING avoids relying on driver-specific rowcount.
    row = conn.execute(
        f"UPDATE users SET {assignments}, updated_at=?, version=version+1 "
        "WHERE id=? AND version=? AND active=1 RETURNING id",
        params + (db.now(), user_id, expected_version),
    ).fetchone()
    if row is None:
        raise ProfileError(
            "Sua conta foi alterada em outra sessão. Reabra Minha conta e tente de novo.",
            status=409,
            code="version_conflict",
        )
    return _active_user(conn, user_id)


def update_own_profile(
    conn,
    user_id: int,
    *,
    name: str,
    email: str,
    current_password: Optional[str],
    expected_version: int,
) -> tuple[dict, bool]:
    """Name and e-mail of the authenticated user, inside the caller's
    transaction. Returns (user, credentials_changed): an e-mail change requires
    the current password and means sessions must be rotated by the caller."""
    user = _active_user(conn, user_id)
    _check_version(user, expected_version)
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 120:
        raise ProfileError("O nome deve ter entre 1 e 120 caracteres.", fields=["name"])
    try:
        normalized = _normalize_email(email)
    except ValueError as exc:
        raise ProfileError(str(exc), fields=["email"]) from exc
    email_changed = normalized != user["email"]
    if email_changed:
        _require_current_password(user, current_password)
        taken = conn.execute(
            "SELECT 1 FROM users WHERE email=? AND id<>?", (normalized, user_id)
        ).fetchone()
        if taken:
            raise ProfileError(
                "Já existe um usuário com este e-mail.", status=409, fields=["email"], code="email_taken"
            )
    updated = _bump(conn, user_id, expected_version, "name=?, email=?", (clean_name, normalized))
    return updated, email_changed


def change_own_password(
    conn,
    user_id: int,
    *,
    current_password: str,
    new_password: str,
    expected_version: int,
) -> dict:
    """Authenticated password change, inside the caller's transaction. Unlike
    reset_password it proves knowledge of the current password and leaves
    session rotation to the caller, so the current browser stays signed in."""
    user = _active_user(conn, user_id)
    _check_version(user, expected_version)
    _require_current_password(user, current_password)
    if len(new_password) < 8 or len(new_password) > 200:
        raise ProfileError("A nova senha deve ter entre 8 e 200 caracteres.", fields=["new_password"])
    salt = secrets.token_bytes(16)
    digest = _password_digest(new_password, salt)
    return _bump(
        conn, user_id, expected_version, "password_hash=?, password_salt=?", (digest.hex(), salt.hex())
    )
