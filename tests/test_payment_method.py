"""Como cada conta é paga (item 5) e repetir o último lançamento de um credor (item 6).

`payment_method` diz se a conta sai por boleto, Pix, débito automático ou transferência;
`payment_code` guarda a linha digitável ou o Pix copia e cola, sem validar dígito — o sócio
localiza o boleto pelo DDA da Stone e usa o campo para conferir e copiar."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts, entries as entries_module
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.recurrence import generate_occurrences


BOLETO = "34191790010104351004791020150008291070026000"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "payment-method.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post("/api/login", json={"email": "admin@loja.test", "password": "bootstrap-password"})
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def _counterparty(client, name="Requinte Imobiliária"):
    response = client.post("/api/companies/1/finance/counterparties",
                           json={"name": name, "kind": "supplier", "document": ""})
    assert response.status_code == 201, response.text
    return response.json()


def _post_entry(client, **extra):
    body = {
        "account_id": accounts.account_by_key(1, "rent")["id"], "amount_cents": 350_000,
        "competence": "2026-09", "due_date": "2026-09-10", "description": "Aluguel",
    }
    body.update(extra)
    return client.post("/api/companies/1/finance/entries", json=body)


def test_entry_keeps_how_it_is_paid(client):
    created = _post_entry(client, payment_method="boleto", payment_code=BOLETO)

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["payment_method"] == "boleto"
    assert body["payment_code"] == BOLETO
    assert entries_module.get_entry(int(body["id"]))["payment_method"] == "boleto"


def test_payment_method_is_validated(client):
    assert _post_entry(client, payment_method="cheque").status_code == 422
    assert _post_entry(client, payment_method="pix").status_code == 201
    assert _post_entry(client, payment_method="debito_automatico").status_code == 201
    assert _post_entry(client, payment_method="transferencia").status_code == 201


def test_an_open_entry_can_change_how_it_is_paid(client):
    entry = _post_entry(client, payment_method="boleto", payment_code=BOLETO).json()

    patched = client.patch(
        f"/api/companies/1/finance/entries/{entry['id']}",
        json={"expected_version": entry["version"], "payment_method": "debito_automatico", "payment_code": ""},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["payment_method"] == "debito_automatico"
    assert patched.json()["payment_code"] == ""
    assert patched.json()["version"] == entry["version"] + 1


def test_a_recurring_expense_passes_how_it_is_paid_to_every_month(client):
    recurrence = client.post("/api/companies/1/finance/recurrences", json={
        "account_id": accounts.account_by_key(1, "rent")["id"], "amount_cents": 350_000,
        "description": "Aluguel", "start_competence": "2026-09", "due_day": 10,
        "payment_method": "boleto", "payment_code": BOLETO,
    })
    assert recurrence.status_code == 201, recurrence.text

    occurrences = generate_occurrences(int(recurrence.json()["id"]), through_competence="2026-10")

    assert [o["competence"] for o in occurrences] == ["2026-09", "2026-10"]
    assert {o["payment_method"] for o in occurrences} == {"boleto"}
    assert {o["payment_code"] for o in occurrences} == {BOLETO}


def test_last_expense_of_a_counterparty(client):
    supplier = _counterparty(client)
    empty = client.get(f"/api/companies/1/finance/counterparties/{supplier['id']}/last-expense")
    assert empty.status_code == 204, empty.text

    account = accounts.account_by_key(1, "rent")
    for competence, day, amount in [("2026-08", 10, 340_000), ("2026-09", 10, 350_000)]:
        create_entry(EntryCommand(
            company_id=1, account_id=account["id"], counterparty_id=int(supplier["id"]),
            amount_cents=amount, competence=competence, due_date=date.fromisoformat(f"{competence}-{day:02d}"),
            source="manual", external_id=None, description="Aluguel", payment_method="boleto", payment_code=BOLETO,
        ))

    last = client.get(f"/api/companies/1/finance/counterparties/{supplier['id']}/last-expense")

    assert last.status_code == 200, last.text
    body = last.json()
    assert body["amount_cents"] == 350_000
    assert body["competence"] == "2026-09"
    assert str(body["account_id"]) == str(account["id"])
    assert body["payment_method"] == "boleto"
    assert body["description"] == "Aluguel"


def test_a_cancelled_expense_is_not_the_last_one(client):
    supplier = _counterparty(client, "Sabesp")
    account = accounts.account_by_key(1, "water")
    keeper = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], counterparty_id=int(supplier["id"]), amount_cents=9_000,
        competence="2026-08", due_date=date(2026, 8, 20), source="manual", external_id=None, description="Água",
    ))
    cancelled = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], counterparty_id=int(supplier["id"]), amount_cents=99_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual", external_id=None, description="Água errada",
    ))
    client.post(f"/api/companies/1/finance/entries/{cancelled['id']}/cancel",
                json={"expected_version": 1, "reason": "Lançamento em duplicidade"})

    last = client.get(f"/api/companies/1/finance/counterparties/{supplier['id']}/last-expense").json()

    assert last["amount_cents"] == keeper["amount_cents"]
