from __future__ import annotations

import pytest

from backend import database as db
from backend.organization import (
    ConcurrentUpdateError,
    operating_days,
    save_company_profile,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "organization.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja Mobne')")


def test_operating_days_excludes_sunday_and_exception():
    assert operating_days(
        "2026-09-01",
        "2026-09-08",
        closed_weekdays={6},
        exceptions={"2026-09-07": "closed"},
    ) == 6


def test_operating_days_can_open_a_normally_closed_weekday():
    assert operating_days(
        "2026-09-06",
        "2026-09-06",
        closed_weekdays={6},
        exceptions={"2026-09-06": "open"},
    ) == 1


def test_company_profile_uses_optimistic_version(isolated_db):
    created = save_company_profile(
        1,
        {
            "legal_name": "Mercado do Bairro LTDA",
            "trade_name": "Mercado duBairro",
            "cnpj": "12.345.678/0001-90",
        },
        expected_version=None,
    )
    assert created["version"] == 1

    updated = save_company_profile(
        1,
        {"trade_name": "Mercado duBairro Centro"},
        expected_version=1,
    )
    assert updated["version"] == 2

    with pytest.raises(ConcurrentUpdateError):
        save_company_profile(
            1,
            {"trade_name": "Valor antigo"},
            expected_version=1,
        )

