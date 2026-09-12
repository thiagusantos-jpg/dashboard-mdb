from __future__ import annotations

import pytest

from backend import database as db
from backend.integrations.records import (
    ExternalRecordConflict,
    external_record_count,
    list_quarantine,
    upsert_external_record,
)


COMPANY = 1


@pytest.fixture
def records_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "records.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def test_replay_is_noop(records_db):
    a = upsert_external_record(COMPANY, "ofx", 1, "fitid-123", "1", {"amount": -100})
    b = upsert_external_record(COMPANY, "ofx", 1, "fitid-123", "1", {"amount": -100})

    assert a["id"] == b["id"]
    assert external_record_count(COMPANY) == 1


def test_same_key_with_different_payload_is_quarantined(records_db):
    upsert_external_record(COMPANY, "ofx", 1, "fitid-123", "1", {"amount": -100})

    with pytest.raises(ExternalRecordConflict):
        upsert_external_record(COMPANY, "ofx", 1, "fitid-123", "1", {"amount": -999})

    assert external_record_count(COMPANY) == 1
    quarantined = list_quarantine(COMPANY)
    assert len(quarantined) == 1
    assert quarantined[0]["external_id"] == "fitid-123"


def test_different_versions_are_distinct_records(records_db):
    upsert_external_record(COMPANY, "ofx", 1, "fitid-123", "1", {"amount": -100})
    upsert_external_record(COMPANY, "ofx", 1, "fitid-123", "2", {"amount": -100, "corrected": True})

    assert external_record_count(COMPANY) == 2


def test_hash_ignores_key_order(records_db):
    a = upsert_external_record(COMPANY, "ofx", 1, "fitid-9", "1", {"amount": -50, "memo": "x"})
    b = upsert_external_record(COMPANY, "ofx", 1, "fitid-9", "1", {"memo": "x", "amount": -50})

    assert a["id"] == b["id"]
    assert external_record_count(COMPANY) == 1
