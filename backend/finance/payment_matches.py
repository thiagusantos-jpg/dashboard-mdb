"""Contas que parecem já ter saído da conta.

O extrato importado vira movimento de caixa (backend/integrations/bank_files.py).
Quando um desses débitos bate com o saldo em aberto de uma conta e aconteceu perto do
vencimento, Contas a pagar oferece dar a baixa — mas quem confirma é o sócio, na gaveta
de pagamento de sempre, com todas as validações de payments.record_payment no caminho.
Nada é quitado sozinho.

Só contas avulsas (kind='entry'). Parcela de empréstimo pede a divisão entre principal e
juros, que um débito do extrato não tem como informar — a mesma razão pela qual o acréscimo
por atraso é recusado para empréstimo em payments.record_payment.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import database as db
from . import obligations


# Uma conta paga com alguns dias de atraso continua sendo a mesma conta. Mais largo que os
# 3 dias de bank_files._candidate_cash_events porque lá o alvo é a data do próprio movimento
# importado, e aqui é o vencimento, que o pagamento costuma seguir com alguma folga.
MATCH_WINDOW_DAYS = 5


def _unusable_cash_event_ids(conn) -> set:
    """Movimentos que não podem pagar nada: já estornados, ou já consumidos por um
    pagamento ativo. São os mesmos critérios que payments.record_payment exige no caminho
    do `existing_cash_event_id`, e que o índice parcial
    obligation_payments_active_cash_event_idx (migração 022) garante de verdade."""
    reversed_ids = {
        row["reversed_event_id"]
        for row in conn.execute(
            "SELECT reversed_event_id FROM cash_events WHERE reversed_event_id IS NOT NULL"
        )
    }
    used = {
        row["cash_event_id"]
        for row in conn.execute(
            "SELECT cash_event_id FROM obligation_payments"
            " WHERE cash_event_id IS NOT NULL AND reversed_at IS NULL"
        )
    }
    return reversed_ids | used


def _outflows(conn, company: int, start: date, end: date) -> list:
    """Saídas de caixa da janela. `kind='entry'` deixa de fora transferências e as
    próprias linhas de estorno, exatamente como bank_files._candidate_cash_events."""
    rows = conn.execute(
        """
        SELECT id,cash_account_id,amount_cents,occurred_at,description
        FROM cash_events
        WHERE company=? AND kind='entry' AND amount_cents<0
          AND occurred_at BETWEEN ? AND ?
        ORDER BY occurred_at,id
        """,
        (company, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def suggestions(company: int, *, today: date, include_sensitive: bool = False) -> list:
    """Pares 1 para 1 entre conta aberta e débito ainda não usado.

    A ambiguidade vira silêncio, não palpite: se o mesmo débito serve a duas contas, ou a
    mesma conta tem dois débitos possíveis, nada é sugerido. Dois gastos legítimos de mesmo
    valor e data nunca devem ser fundidos sozinhos — a mesma regra que bank_files já aplica
    ao pré-selecionar uma linha do extrato."""
    bills = [
        item for item in obligations._entry_rows(company)
        if item["open_cents"] > 0 and (include_sensitive or not item["_sensitive"])
    ]
    if not bills:
        return []

    due_dates = [date.fromisoformat(str(bill["due_date"])[:10]) for bill in bills]
    window = timedelta(days=MATCH_WINDOW_DAYS)
    # Nada a procurar depois de hoje: um débito ainda não aconteceu. Sem esse teto, uma
    # conta com vencimento distante faria a consulta varrer um futuro sempre vazio.
    window_end = min(max(due_dates) + window, today)
    if window_end < min(due_dates) - window:
        return []
    with db.connection() as conn:
        unusable = _unusable_cash_event_ids(conn)
        events = [
            event for event in _outflows(conn, company, min(due_dates) - window, window_end)
            if event["id"] not in unusable
        ]
    if not events:
        return []

    # Quem casa com quem, nos dois sentidos: só sobra o par que é único dos dois lados.
    matches: dict = {}
    for bill, due in zip(bills, due_dates):
        matches[bill["key"]] = [
            event for event in events
            if -event["amount_cents"] == bill["open_cents"]
            and abs((date.fromisoformat(event["occurred_at"][:10]) - due).days) <= MATCH_WINDOW_DAYS
        ]
    bills_per_event: dict = {}
    for key, candidates in matches.items():
        for event in candidates:
            bills_per_event.setdefault(event["id"], []).append(key)

    by_key = {bill["key"]: bill for bill in bills}
    items = []
    for key, candidates in matches.items():
        if len(candidates) != 1:
            continue
        event = candidates[0]
        if len(bills_per_event.get(event["id"], [])) != 1:
            continue
        bill = by_key[key]
        items.append({
            # Os mesmos campos que a gaveta de pagamento mostra no resumo (total, já pago
            # e saldo): sem eles a gaveta abriria com "Indisponível" nessas linhas.
            "obligation": {
                "kind": "entry", "id": str(bill["id"]), "version": bill["version"],
                "description": bill["description"], "due_date": bill["due_date"],
                "total_cents": bill["total_cents"], "paid_cents": bill["paid_cents"],
                "open_cents": bill["open_cents"], "status": bill["status"],
            },
            "cash_event": {
                "id": str(event["id"]), "cash_account_id": str(event["cash_account_id"]),
                "occurred_at": event["occurred_at"][:10], "description": event["description"],
                "amount_cents": event["amount_cents"],
            },
        })
    items.sort(key=lambda item: (item["obligation"]["due_date"], item["obligation"]["id"]))
    return items
