from __future__ import annotations

from typing import Optional

from .. import database as db, models
from .accounts import list_accounts, resolve_parameter


def management_result(company: int, period: str, store: Optional[int] = None) -> dict:
    sales = db.dataset(company, "sales", period)
    totals = (
        models.summarize(sales["payload"]["receipts"])["totals"]
        if sales
        else {"revenue": 0, "cost": 0, "unknown": 0}
    )
    revenue = int(totals["revenue"])
    cogs = None if totals["unknown"] else int(totals["cost"])
    gross_profit = revenue - cogs if cogs is not None else None

    store_filter = " AND e.store IN (0,?)" if store is not None else ""
    params = (company, period, store) if store is not None else (company, period)
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id,a.system_key,a.name,a.nature,
                   COALESCE(SUM(e.amount_cents),0) AS actual_cents,
                   MIN(e.source) AS first_source,
                   COUNT(DISTINCT e.source) AS source_count
            FROM financial_entries e
            JOIN finance_accounts a ON a.id=e.account_id
            WHERE e.company=? AND e.competence=?
              AND e.status NOT IN ('forecast','cancelled','reversed')
            """
            + store_filter
            + " GROUP BY a.id,a.system_key,a.name,a.nature ORDER BY a.code,a.name",
            params,
        ).fetchall()

    actual_by_id = {row["id"]: dict(row) for row in rows}
    account_lines = []
    on_date = period + "-01"
    for account in list_accounts(company):
        actual = actual_by_id.get(account["id"])
        budget = resolve_parameter(
            company,
            "budget:" + (account["system_key"] or account["code"]),
            on_date,
            store=store,
        )
        actual_cents = int(actual["actual_cents"]) if actual else 0
        if not actual_cents and budget is None:
            continue
        source = "budget"
        if actual:
            source = (
                actual["first_source"]
                if actual["source_count"] == 1
                else "multiple"
            )
        account_lines.append(
            {
                "account_id": account["id"],
                "system_key": account["system_key"],
                "name": account["name"],
                "nature": account["nature"],
                "actual_cents": actual_cents,
                "budget_cents": budget,
                "variance_cents": actual_cents - budget if budget is not None else None,
                "source": source,
            }
        )

    def amount(natures, *, key=None, exclude_key=None):
        return sum(
            line["actual_cents"]
            for line in account_lines
            if line["nature"] in natures
            and (key is None or line["system_key"] == key)
            and (exclude_key is None or line["system_key"] != exclude_key)
        )

    owner_compensation = amount(
        {"operating_expense"}, key="owner_compensation"
    ) + amount({"operating_expense"}, key="owner_compensation_taxes")
    operating_expenses = amount(
        {"operating_expense", "tax_expense"},
        exclude_key="owner_compensation",
    ) - amount({"operating_expense"}, key="owner_compensation_taxes")
    financial_expenses = amount({"financial_expense"})
    distributions = amount({"profit_distribution"})
    operating_result = (
        gross_profit - operating_expenses - owner_compensation
        if gross_profit is not None
        else None
    )
    managerial_result = (
        operating_result - financial_expenses
        if operating_result is not None
        else None
    )
    return {
        "company": company,
        "period": period,
        "store": store,
        "revenue_cents": revenue,
        "cogs_cents": cogs,
        "gross_profit_cents": gross_profit,
        "operating_expenses_cents": operating_expenses,
        "owner_compensation_cents": owner_compensation,
        "operating_result_cents": operating_result,
        "financial_expenses_cents": financial_expenses,
        "managerial_result_cents": managerial_result,
        "profit_distribution_cents": distributions,
        "accounts": account_lines,
        "sources": {
            "revenue": "Mobne",
            "cogs": "Mobne",
            "expenses": "financial_entries",
        },
    }
