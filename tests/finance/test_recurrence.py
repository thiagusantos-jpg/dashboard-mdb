from __future__ import annotations

import pytest

from backend import database as db
from backend.finance.accounts import list_accounts
from backend.finance.recurrence import create_recurrence, generate_occurrences


@pytest.fixture
def recurrence(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "recurrence.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    account = next(row for row in list_accounts(1) if row["system_key"] == "rent")
    return create_recurrence(
        company=1,
        account_id=account["id"],
        description="Aluguel",
        amount_cents=250_000,
        start_competence="2026-08",
        due_day=10,
    )


def test_recurrence_generation_is_idempotent(recurrence):
    first = generate_occurrences(recurrence["id"], through_competence="2026-10")
    second = generate_occurrences(recurrence["id"], through_competence="2026-10")

    assert [entry["competence"] for entry in first] == [
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    assert second == []
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM financial_entries WHERE recurrence_id=?",
            (recurrence["id"],),
        ).fetchone()["total"] == 3
