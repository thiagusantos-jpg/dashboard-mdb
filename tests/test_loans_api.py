from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import loans


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "loans-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post(
            "/api/login",
            json={"email": "admin@loja.test", "password": "bootstrap-password"},
        )
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def create_loan(client):
    return client.post(
        "/api/companies/1/finance/loans",
        json={
            "lender": "Banco Local",
            "purpose": "Capital de giro",
            "principal_cents": 90_000_00,
            "net_disbursement_cents": 88_500_00,
            "start_date": "2026-09-01",
            "installments": [
                {"number": 1, "due_date": "2026-10-10", "principal_cents": 30_000_00, "interest_cents": 600},
                {"number": 2, "due_date": "2026-11-10", "principal_cents": 30_000_00, "interest_cents": 600},
                {"number": 3, "due_date": "2026-12-10", "principal_cents": 30_000_00, "interest_cents": 600},
            ],
        },
    )


def test_create_and_list_loan(client):
    created = create_loan(client)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["principal_cents"] == 90_000_00
    assert len(body["installments"]) == 3

    listed = client.get("/api/companies/1/finance/loans")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_pay_installment_via_api_without_cash_link_is_rejected(client):
    # Task B3: this legacy route's payload has no way to link the payment to
    # a real cash movement, so it now always rejects with 409 and points the
    # caller at the new POST /obligations/loan_installment/{id}/payments
    # flow (backend/finance/payments.py::record_payment) instead of silently
    # recording an untracked payment. See test_obligations_api.py for the
    # new route's success path.
    created = create_loan(client)
    installment_id = created.json()["installments"][0]["id"]

    paid = client.post(
        f"/api/companies/1/finance/loans/installments/{installment_id}/payments",
        json={"principal_cents": 30_000_00, "interest_cents": 600, "paid_at": "2026-10-10"},
    )
    assert paid.status_code == 409, paid.text
    assert paid.json()["detail"] == "Atualize a página para registrar a conta de pagamento."


def test_loan_from_another_company_is_not_visible(client):
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(2,'Loja 2')")
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]

    response = client.get(f"/api/companies/2/finance/loans/{loan_id}")
    assert response.status_code == 404


def test_create_loan_rejects_principal_sum_mismatch(client):
    response = client.post(
        "/api/companies/1/finance/loans",
        json={
            "lender": "Banco Local",
            "purpose": "Capital de giro",
            "principal_cents": 90_000_00,
            "net_disbursement_cents": 88_500_00,
            "start_date": "2026-09-01",
            "installments": [
                {"number": 1, "due_date": "2026-10-10", "principal_cents": 40_000_00, "interest_cents": 600},
            ],
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()["detail"]
    assert body["code"] == "invalid_fields"
    assert "installments" in body["fields"]


def test_patch_loan_updates_lender_and_purpose(client):
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]
    version = created.json()["loan"]["version"]

    patched = client.patch(
        f"/api/companies/1/finance/loans/{loan_id}",
        json={"expected_version": version, "lender": "Novo Banco", "purpose": "Reforma"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["loan"]["lender"] == "Novo Banco"
    assert patched.json()["loan"]["purpose"] == "Reforma"


def test_patch_loan_rejects_stale_version(client):
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]
    version = created.json()["loan"]["version"]

    first = client.patch(
        f"/api/companies/1/finance/loans/{loan_id}",
        json={"expected_version": version, "lender": "Novo Banco"},
    )
    assert first.status_code == 200, first.text

    stale = client.patch(
        f"/api/companies/1/finance/loans/{loan_id}",
        json={"expected_version": version, "lender": "Outro Banco"},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "conflict"


def test_patch_loan_rejects_value_bearing_field(client):
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]
    version = created.json()["loan"]["version"]

    response = client.patch(
        f"/api/companies/1/finance/loans/{loan_id}",
        json={"expected_version": version, "principal_cents": 123_00},
    )
    assert response.status_code == 422, response.text
    body = response.json()["detail"]
    assert body["code"] == "invalid_fields"
    assert "principal_cents" in body["fields"]


def test_cancel_loan_without_movement_succeeds(client):
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]

    response = client.post(
        f"/api/companies/1/finance/loans/{loan_id}/cancel",
        json={"reason": "Contrato não utilizado"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["loan"]["status"] == "cancelled"


def test_cancel_loan_with_payment_is_rejected(client):
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]
    installment_id = created.json()["installments"][0]["id"]

    loans.pay_installment_legacy_unsafe(
        installment_id, principal_cents=30_000_00, interest_cents=600,
        paid_at=date(2026, 10, 10),
    )

    response = client.post(
        f"/api/companies/1/finance/loans/{loan_id}/cancel",
        json={"reason": "Tentativa inválida"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "conflict"


def test_disbursement_via_api_requires_idempotency_key(client):
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]

    response = client.post(
        f"/api/companies/1/finance/loans/{loan_id}/disbursements",
        json={"amount_cents": 88_500_00, "disbursed_at": "2026-09-01"},
    )
    assert response.status_code == 422, response.text


def test_disbursement_via_api_creates_cash_movement(client):
    created = create_loan(client)
    loan_id = created.json()["loan"]["id"]

    account = client.post(
        "/api/companies/1/finance/cash-accounts",
        json={"name": "Banco", "kind": "bank"},
    ).json()

    response = client.post(
        f"/api/companies/1/finance/loans/{loan_id}/disbursements",
        json={
            "amount_cents": 88_500_00,
            "disbursed_at": "2026-09-01",
            "cash_account_id": account["id"],
        },
        headers={"Idempotency-Key": "disb-api-1"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["cash_event_id"] is not None

    balance = client.get("/api/companies/1/finance/cash-balance")
    assert balance.json()["balance_cents"] == 88_500_00

    # Replay with the same key returns the same result and does not double
    # the cash balance.
    replay = client.post(
        f"/api/companies/1/finance/loans/{loan_id}/disbursements",
        json={
            "amount_cents": 88_500_00,
            "disbursed_at": "2026-09-01",
            "cash_account_id": account["id"],
        },
        headers={"Idempotency-Key": "disb-api-1"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json() == response.json()
    balance_after_replay = client.get("/api/companies/1/finance/cash-balance")
    assert balance_after_replay.json()["balance_cents"] == 88_500_00
