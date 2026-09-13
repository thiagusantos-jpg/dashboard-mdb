from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, identity, permissions, security
from backend.finance import accounts, ledger, loans
from backend.finance.entries import EntryCommand, create_entry, settle_entry


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "obligations-api.sqlite3")
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


def _partial_entry():
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    settle_entry(entry["id"], 40_000, paid_at=date(2026, 9, 12))
    return entry


def _two_installment_loan():
    return loans.create_loan(
        1, lender="Banco Local", purpose="Capital de giro",
        principal_cents=200_000, net_disbursement_cents=195_000,
        installments=[
            {"number": 1, "due_date": "2026-09-10", "principal_cents": 100_000, "interest_cents": 5_000},
            {"number": 2, "due_date": "2026-10-10", "principal_cents": 100_000, "interest_cents": 5_000},
        ],
        start_date="2026-09-01",
    )


def _login_as(test_client, email, password):
    other = TestClient(api.app)
    login = other.post("/api/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    other.headers["x-csrf-token"] = login.json()["csrf"]
    return other


def _create_viewer(email="visualizador@loja.test", password="senha-segura-1"):
    user = identity.create_user(email, password, "Visualizador", is_admin=False)
    permissions.grant_role(user["id"], 1, "viewer")
    return user


# --- Basic listing / pagination --------------------------------------------


def test_list_obligations_returns_shared_contract_shape(client):
    _partial_entry()
    _two_installment_loan()

    response = client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert set(body.keys()) == {"items", "total", "open_cents", "next_cursor"}
    item = body["items"][0]
    assert set(item.keys()) == {
        "key", "kind", "id", "version", "description", "due_date", "competence",
        "total_cents", "paid_cents", "open_cents", "status", "source", "loan_id",
        "number", "count", "allowed_actions",
    }
    assert item["key"] in (item["kind"] + ":" + item["id"],)
    assert isinstance(item["id"], str)


def test_list_obligations_paginates_with_limit(client):
    _partial_entry()
    _two_installment_loan()

    first = client.get("/api/companies/1/finance/obligations", params={"limit": 1})
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3
    assert body["next_cursor"] is not None

    second = client.get(
        "/api/companies/1/finance/obligations",
        params={"limit": 1, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    assert second.json()["total"] == 3
    assert second.json()["items"][0]["key"] != body["items"][0]["key"]


# --- Filters -----------------------------------------------------------------


def test_search_and_combinable_filters_via_query_params(client):
    _partial_entry()
    _two_installment_loan()

    response = client.get("/api/companies/1/finance/obligations", params={"q": "aluguel"})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1

    response = client.get(
        "/api/companies/1/finance/obligations",
        params={
            "kind": "loan_installment",
            "status": "open",
            "due_from": "2026-10-01",
            "due_to": "2026-10-31",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["due_date"] == "2026-10-10"


def test_invalid_kind_filter_is_rejected(client):
    response = client.get("/api/companies/1/finance/obligations", params={"kind": "bogus"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_fields"


# --- Invalid cursor ----------------------------------------------------------


def test_invalid_cursor_returns_422_with_shared_error_shape(client):
    _partial_entry()
    response = client.get(
        "/api/companies/1/finance/obligations", params={"cursor": "not-a-real-cursor!!"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_fields"
    assert "cursor" in detail["fields"]


# --- Read-only viewer: no write actions offered -----------------------------


def test_read_only_viewer_has_no_write_allowed_actions(client):
    _partial_entry()
    _create_viewer()
    viewer_client = _login_as(client, "visualizador@loja.test", "senha-segura-1")

    response = viewer_client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        assert "pay" not in item["allowed_actions"]
        assert "edit_description" not in item["allowed_actions"]
        assert "details" in item["allowed_actions"]

    # Compare with the admin's view of the exact same data: it does offer them.
    admin_view = client.get("/api/companies/1/finance/obligations").json()
    assert any("pay" in item["allowed_actions"] for item in admin_view["items"])


def test_viewer_without_finance_read_gets_403(client):
    user = identity.create_user("estranho@loja.test", "senha-segura-2", "Estranho", is_admin=False)
    # No role granted at all for company 1.
    outsider = _login_as(client, "estranho@loja.test", "senha-segura-2")
    response = outsider.get("/api/companies/1/finance/obligations")
    assert response.status_code == 403


# --- Sensitive gating applies to rows AND totals ----------------------------


def test_sensitive_rows_and_totals_hidden_from_manager_without_sensitive_read(client):
    sensitive_account = accounts.account_by_key(1, "salaries")
    create_entry(EntryCommand(
        company_id=1, account_id=sensitive_account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 15), source="manual",
        external_id=None, description="Folha de pagamento",
    ))
    _partial_entry()

    manager = identity.create_user("gerente@loja.test", "senha-segura-3", "Gerente", is_admin=False)
    permissions.grant_role(manager["id"], 1, "manager")  # finance.write but not finance.sensitive.read
    manager_client = _login_as(client, "gerente@loja.test", "senha-segura-3")

    response = manager_client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["open_cents"] == 60_000
    assert all("Folha" not in item["description"] for item in body["items"])

    # An administrator (global admin, sees everything) still sees both.
    admin_body = client.get("/api/companies/1/finance/obligations").json()
    assert admin_body["total"] == 2


# --- IDs stay strings, even past JS's 2**53 safe-integer limit -------------


def test_ids_are_stringified_in_json_response(client, monkeypatch):
    from backend.finance import entries as entries_module

    big_id = 9_007_199_254_740_993  # 2**53 + 1
    monkeypatch.setattr(entries_module, "_new_id", lambda: big_id)
    account = accounts.account_by_key(1, "rent")
    create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=10_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))

    response = client.get("/api/companies/1/finance/obligations")
    assert response.status_code == 200, response.text
    item = next(i for i in response.json()["items"] if i["key"] == f"entry:{big_id}")
    assert item["id"] == str(big_id)
    # Raw response text must never carry the bare numeric id (would prove a
    # raw JSON number slipped through and lost precision in JS JSON.parse).
    assert f'"id":{big_id}' not in response.text


# --- Detail endpoint ---------------------------------------------------------


def test_get_obligation_detail_entry(client):
    entry = _partial_entry()
    response = client.get(f"/api/companies/1/finance/obligations/entry/{entry['id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == f"entry:{entry['id']}"
    assert body["open_cents"] == 60_000


def test_get_obligation_detail_preserves_paid_installment_history(client):
    loan = _two_installment_loan()
    installment_id = loans.loan_position(loan["id"])["installments"][0]["id"]
    # Task B3 made the legacy POST /loans/installments/{id}/payments route
    # always reject (no cash link) — use the module function directly to
    # set up this fixture's "already paid" installment, same as before B3
    # this test relied on the (now-removed) success path of that route.
    loans.pay_installment_legacy_unsafe(
        installment_id, principal_cents=100_000, interest_cents=5_000,
        paid_at=date(2026, 9, 10),
    )
    response = client.get(f"/api/companies/1/finance/obligations/loan_installment/{installment_id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paid"


def test_get_obligation_detail_404_for_unknown_kind(client):
    response = client.get("/api/companies/1/finance/obligations/bogus/1")
    assert response.status_code == 404


def test_get_obligation_detail_404_for_missing_id(client):
    response = client.get("/api/companies/1/finance/obligations/entry/999999999999")
    assert response.status_code == 404


def test_get_obligation_detail_404_for_sensitive_item_without_permission(client):
    sensitive_account = accounts.account_by_key(1, "salaries")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=sensitive_account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 15), source="manual",
        external_id=None, description="Folha de pagamento",
    ))
    manager = identity.create_user("gerente2@loja.test", "senha-segura-4", "Gerente", is_admin=False)
    permissions.grant_role(manager["id"], 1, "manager")
    manager_client = _login_as(client, "gerente2@loja.test", "senha-segura-4")

    response = manager_client.get(f"/api/companies/1/finance/obligations/entry/{entry['id']}")
    assert response.status_code == 404


# --- POST .../obligations/{kind}/{id}/payments (task B3) -------------------


def test_pay_entry_via_new_payments_endpoint(client):
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    bank = ledger.create_account(1, "Banco X", "bank")

    response = client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        json={
            "amount_cents": 40_000, "paid_at": "2026-09-12", "expected_version": 1,
            "cash_account_id": bank["id"],
        },
        headers={"Idempotency-Key": "test-api-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["obligation"]["open_cents"] == 60_000
    assert body["cash_event_id"] is not None
    assert isinstance(body["payment_id"], str)

    # Replaying the same key returns the same payment, not a second one.
    replay = client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        json={
            "amount_cents": 40_000, "paid_at": "2026-09-12", "expected_version": 1,
            "cash_account_id": bank["id"],
        },
        headers={"Idempotency-Key": "test-api-1"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["payment_id"] == body["payment_id"]


def test_pay_entry_without_idempotency_key_header_is_rejected(client):
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    bank = ledger.create_account(1, "Banco X", "bank")

    response = client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        json={
            "amount_cents": 40_000, "paid_at": "2026-09-12", "expected_version": 1,
            "cash_account_id": bank["id"],
        },
    )
    assert response.status_code == 422, response.text


def test_pay_loan_installment_via_new_payments_endpoint(client):
    loan = _two_installment_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    bank = ledger.create_account(1, "Banco X", "bank")

    response = client.post(
        f"/api/companies/1/finance/obligations/loan_installment/{installment['id']}/payments",
        json={
            "amount_cents": 105_000, "paid_at": "2026-09-10", "expected_version": 1,
            "cash_account_id": bank["id"], "principal_cents": 100_000, "interest_cents": 5_000,
        },
        headers={"Idempotency-Key": "test-api-loan-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["obligation"]["status"] == "paid"
    assert body["obligation"]["open_cents"] == 0


def test_pay_sensitive_entry_requires_sensitive_read_permission(client):
    sensitive_account = accounts.account_by_key(1, "salaries")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=sensitive_account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 15), source="manual",
        external_id=None, description="Folha de pagamento",
    ))
    bank = ledger.create_account(1, "Banco X", "bank")
    manager = identity.create_user("gerente3@loja.test", "senha-segura-5", "Gerente", is_admin=False)
    permissions.grant_role(manager["id"], 1, "manager")  # finance.write but not finance.sensitive.read
    manager_client = _login_as(client, "gerente3@loja.test", "senha-segura-5")

    response = manager_client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        json={
            "amount_cents": 50_000, "paid_at": "2026-09-15", "expected_version": 1,
            "cash_account_id": bank["id"],
        },
        headers={"Idempotency-Key": "sensitive-1"},
    )
    assert response.status_code == 403, response.text


def test_pay_obligation_stale_version_returns_409(client):
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    bank = ledger.create_account(1, "Banco X", "bank")

    response = client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        json={
            "amount_cents": 40_000, "paid_at": "2026-09-12", "expected_version": 99,
            "cash_account_id": bank["id"],
        },
        headers={"Idempotency-Key": "test-api-stale"},
    )
    assert response.status_code == 409, response.text


# --- GET .../obligations/{kind}/{id} lists its payments (task C2) ------------


def test_obligation_detail_lists_payments_for_reversal(client):
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    bank = ledger.create_account(1, "Banco X", "bank")
    paid = client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        json={
            "amount_cents": 40_000, "paid_at": "2026-09-12", "expected_version": 1,
            "cash_account_id": bank["id"],
        },
        headers={"Idempotency-Key": "detail-payments-1"},
    )
    assert paid.status_code == 200, paid.text
    payment_id = paid.json()["payment_id"]
    version = paid.json()["obligation"]["version"]

    detail = client.get(f"/api/companies/1/finance/obligations/entry/{entry['id']}")
    assert detail.status_code == 200, detail.text
    payments = detail.json()["payments"]
    assert [p["id"] for p in payments] == [payment_id]
    assert payments[0]["amount_cents"] == 40_000
    assert payments[0]["paid_at"] == "2026-09-12"
    assert payments[0]["reversed_at"] is None
    assert "idempotency_key" not in payments[0]
    assert "response_json" not in payments[0]

    undone = client.post(
        f"/api/companies/1/finance/payments/{payment_id}/reverse",
        json={"reason": "Valor lançado errado", "reversed_at": "2026-09-13", "expected_version": version},
        headers={"Idempotency-Key": "detail-payments-undo-1"},
    )
    assert undone.status_code == 200, undone.text
    after = client.get(f"/api/companies/1/finance/obligations/entry/{entry['id']}").json()
    assert after["payments"][0]["reversed_at"] == "2026-09-13"
    assert after["payments"][0]["reversal_reason"] == "Valor lançado errado"
    assert after["open_cents"] == 100_000
