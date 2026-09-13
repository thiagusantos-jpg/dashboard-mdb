"""Functional equivalence before optimizing (task C7): resolving every budget
key in one query must give exactly what resolve_parameter gives key by key,
with the same scope precedence (product > category > store > general) and
effective-date rules; and skipping the default-account seed when it is
already complete must still restore a missing default."""
from __future__ import annotations

import pytest

from backend import database as db
from backend.finance import accounts
from backend.finance.accounts import list_accounts, resolve_parameter, set_parameter


COMPANY = 1


@pytest.fixture
def params_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "budget-resolution.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    list_accounts(COMPANY)


def test_batched_resolution_matches_key_by_key_resolution(params_db):
    set_parameter(COMPANY, "budget:electricity", 10_000, "2026-01-01")
    set_parameter(COMPANY, "budget:electricity", 12_000, "2026-09-01")               # newer general value wins
    set_parameter(COMPANY, "budget:electricity", 15_000, "2026-08-01", store=5)      # store scope beats general
    set_parameter(COMPANY, "budget:rent", 300_000, "2026-01-01", effective_to="2026-08-31")  # expired before Sept
    set_parameter(COMPANY, "budget:water", 4_000, "2026-10-01")                       # not effective yet
    set_parameter(COMPANY, "budget:internet", 9_000, "2025-01-01")
    set_parameter(COMPANY, "goal:margin", 30, "2026-01-01")                           # not a budget key
    keys = [f"budget:{name}" for name in ("electricity", "rent", "water", "internet", "cleaning")]

    for store in (None, 5, 7):
        batched = accounts.resolve_parameters(COMPANY, keys, "2026-09-01", store=store)
        one_by_one = {key: resolve_parameter(COMPANY, key, "2026-09-01", store=store) for key in keys}
        assert batched == one_by_one, store

    assert accounts.resolve_parameters(COMPANY, keys, "2026-09-01", store=5)["budget:electricity"] == 15_000
    assert accounts.resolve_parameters(COMPANY, keys, "2026-09-01")["budget:electricity"] == 12_000


def test_skipped_seed_still_restores_a_missing_default_account(params_db):
    with db.connection() as conn:
        conn.execute("DELETE FROM finance_accounts WHERE company=? AND system_key='uniforms'", (COMPANY,))

    keys = {row["system_key"] for row in list_accounts(COMPANY)}

    assert "uniforms" in keys
    assert len([k for k in keys if k]) == len(accounts.DEFAULT_ACCOUNTS)
