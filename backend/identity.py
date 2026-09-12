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
