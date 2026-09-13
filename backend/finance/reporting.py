from __future__ import annotations

from typing import Optional

from .. import database as db, models
from .accounts import list_accounts, resolve_parameter


def _break_even(lines, revenue, cogs, sales_available, restricted, period) -> dict:
    """Break-even point of the competence from the cost center (replaces the
    legacy manually typed fixed cost): fixed costs ÷ contribution margin, where
    contribution = revenue − COGS − variable costs. Every missing input makes
    it unavailable with a reason instead of an estimate."""
    empty = {
        "fixed_costs_cents": None, "variable_costs_cents": None, "contribution_margin_cents": None,
        "contribution_margin_pct": None, "break_even_cents": None, "gap_pct": None,
    }
    if restricted:
        return {**empty, "reason": "Parte das despesas é restrita ao seu perfil: o ponto de equilíbrio não é exibido."}
    fixed = sum(line["actual_cents"] for line in lines if line["cost_behavior"] == "fixed")
    variable = sum(line["actual_cents"] for line in lines if line["cost_behavior"] == "variable")
    result = {**empty, "fixed_costs_cents": fixed, "variable_costs_cents": variable}
    if not sales_available:
        return {**result, "reason": f"Vendas de {period} ainda não sincronizadas: o ponto de equilíbrio depende da margem do mês."}
    if cogs is None:
        return {**result, "reason": "Há itens vendidos sem custo conhecido: a margem do mês não pode ser calculada."}
    if not revenue:
        return {**result, "reason": "Sem faturamento no mês: não há margem para calcular o ponto de equilíbrio."}
    contribution = revenue - cogs - variable
    result["contribution_margin_cents"] = contribution
    result["contribution_margin_pct"] = round(contribution * 100 / revenue, 1)
    if fixed <= 0:
        return {**result, "reason": "Nenhuma despesa fixa lançada nesta competência: lance os custos fixos em Custos e despesas para calcular."}
    if contribution <= 0:
        return {**result, "reason": "Margem de contribuição negativa: o faturamento não cobre o CMV e os custos variáveis."}
    break_even = round(fixed * revenue / contribution)
    return {
        **result,
        "break_even_cents": break_even,
        "gap_pct": round((revenue - break_even) * 100 / break_even, 1),
        "reason": "",
    }


def management_result(
    company: int,
    period: str,
    store: Optional[int] = None,
    *,
    include_sensitive: bool = True,
) -> dict:
    """Managerial P&L for one competence. Unavailable is never a confirmed zero:
    without a synced sales dataset revenue/COGS and every result built on them
    are None; a period synced with zero sales keeps a legitimate 0. Without
    include_sensitive, sensitive accounts are left out and every expense total
    they would feed becomes None, so a filtered total never poses as the
    company total. data_status says which of these applies."""
    sales = db.dataset(company, "sales", period)
    sales_available = sales is not None
    if sales_available:
        totals = models.summarize(sales["payload"]["receipts"])["totals"]
        revenue = int(totals["revenue"])
        cogs = None if totals["unknown"] else int(totals["cost"])
    else:
        revenue = None
        cogs = None
    gross_profit = revenue - cogs if revenue is not None and cogs is not None else None

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
    restricted = False
    on_date = period + "-01"
    from .accounts import resolve_parameters

    all_accounts = list_accounts(company, include_archived=True)
    budget_key = lambda account: "budget:" + (account["system_key"] or account["code"])  # noqa: E731
    budgets = resolve_parameters(company, [budget_key(a) for a in all_accounts], on_date, store=store)
    for account in all_accounts:
        actual = actual_by_id.get(account["id"])
        budget = budgets[budget_key(account)]
        actual_cents = int(actual["actual_cents"]) if actual else 0
        if not actual_cents and budget is None:
            continue
        if account["sensitive"] and not include_sensitive:
            restricted = restricted or bool(actual_cents)
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
                "cost_behavior": account["cost_behavior"],
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
    break_even = _break_even(account_lines, revenue, cogs, sales_available, restricted, period)
    if restricted:
        operating_expenses = owner_compensation = financial_expenses = None
        distributions = operating_result = managerial_result = None

    reasons = []
    if not sales_available:
        reasons.append(
            f"Vendas de {period} ainda não sincronizadas do Mobne: receita, CMV e resultados ficam indisponíveis."
        )
    elif cogs is None:
        reasons.append("Há itens vendidos sem custo conhecido: CMV e resultados ficam indisponíveis.")
    if restricted:
        reasons.append(
            "Parte das despesas é restrita ao seu perfil: totais de despesas e resultados não são exibidos."
        )
    from .period_reviews import get_review

    reviewed = get_review(company, period)["status"] == "reviewed"
    if not reviewed:
        reasons.append("Despesas do mês ainda não revisadas.")
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
        "break_even": break_even,
        "data_status": {
            "sales_available": sales_available,
            "expenses_reviewed": reviewed,
            "restricted": restricted,
            "reason": " ".join(reasons),
        },
        "sources": {
            "revenue": "Mobne",
            "cogs": "Mobne",
            "expenses": "financial_entries",
        },
    }
