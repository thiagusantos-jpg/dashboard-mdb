"""Keeps Stone costs from being booked twice.

Once the Stone receivables report is imported, its fees (MDR, anticipation)
and the monthly terminal fee are booked automatically
(backend/finance/receivables.py). A manual expense in those categories for a
month the report covers would count the same money again, so creating one
needs an explicit "this is another charge" confirmation.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .. import database as db
from . import accounts

AUTOMATIC_KEYS = ("acquiring_fees", "receivables_advance", "payment_terminal_rent")
_WHAT = {
    "acquiring_fees": ("as taxas da maquininha", "entram"),
    "receivables_advance": ("a antecipação de recebíveis", "entra"),
    "payment_terminal_rent": ("a mensalidade da Stone", "entra"),
}
_MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def coverage(company: int) -> dict:
    """Which categories are automatic and which months the report covers."""
    ids = {accounts.account_by_key(company, key)["id"]: key for key in AUTOMATIC_KEYS}
    with db.connection() as conn:
        months = sorted({
            row["m"] for row in conn.execute(
                "SELECT DISTINCT SUBSTR(settlement_date,1,7) AS m FROM stone_receivables "
                "WHERE company=? AND settlement_date IS NOT NULL",
                (company,),
            )
        })
    return {"accounts": {str(i): k for i, k in ids.items()}, "months": months}


def _label(month: str) -> str:
    return f"{_MONTHS[int(month[5:7]) - 1]}/{month[:4]}"


def duplicate_message(company: int, account_id: int, months: Optional[Iterable[str]] = None) -> Optional[str]:
    """None when the expense cannot duplicate the Stone report. `months`
    None means an open-ended rule (recurring): any imported report counts."""
    info = coverage(company)
    key = info["accounts"].get(str(account_id))
    if not key or not info["months"]:
        return None
    what, verb = _WHAT[key]
    if months is None:
        return (
            f"{what[0].upper()}{what[1:]} já {verb} automaticamente pelo relatório de recebíveis da Stone. "
            "Uma despesa recorrente nesta categoria vai contar em dobro."
        )
    hit = sorted(set(months) & set(info["months"]))
    if not hit:
        return None
    return (
        f"Em {', '.join(_label(m) for m in hit)}, {what} já {verb} automaticamente "
        "pelo relatório de recebíveis da Stone. Lançar aqui vai contar em dobro."
    )
