from __future__ import annotations

import json
import secrets
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .. import database as db


class AccountNature(str, Enum):
    REVENUE = "revenue"
    COGS = "cogs"
    OPERATING_EXPENSE = "operating_expense"
    FINANCIAL_EXPENSE = "financial_expense"
    TAX_EXPENSE = "tax_expense"
    FINANCING_INFLOW = "financing_inflow"
    LOAN_PRINCIPAL = "loan_principal"
    PROFIT_DISTRIBUTION = "profit_distribution"
    TRANSFER = "transfer"


@dataclass(frozen=True)
class AccountDefinition:
    key: str
    code: str
    name: str
    nature: str
    sensitive: bool = False


def _definitions():
    operating = AccountNature.OPERATING_EXPENSE.value
    financial = AccountNature.FINANCIAL_EXPENSE.value
    rows = [
        ("sales", "1.01", "Receita de vendas", AccountNature.REVENUE.value, False),
        ("cogs", "2.01", "Custo das mercadorias vendidas", AccountNature.COGS.value, True),
        ("rent", "3.01", "Aluguel", operating, False),
        ("condominium", "3.02", "Condomínio", operating, False),
        ("iptu", "3.03", "IPTU", operating, False),
        ("insurance", "3.04", "Seguros", operating, False),
        ("security", "3.05", "Segurança", operating, False),
        ("electricity", "3.10", "Energia elétrica", operating, False),
        ("water", "3.11", "Água", operating, False),
        ("gas", "3.12", "Gás", operating, False),
        ("internet", "3.13", "Internet", operating, False),
        ("telephone", "3.14", "Telefone", operating, False),
        ("waste", "3.15", "Resíduos", operating, False),
        ("salaries", "3.20", "Salários", operating, True),
        ("overtime", "3.21", "Horas extras", operating, True),
        ("benefits", "3.22", "Benefícios", operating, True),
        ("payroll_taxes", "3.23", "Encargos trabalhistas", operating, True),
        ("vacation", "3.24", "Férias", operating, True),
        ("thirteenth_salary", "3.25", "13º salário", operating, True),
        ("severance", "3.26", "Rescisões", operating, True),
        ("owner_compensation", "3.30", "Pró-labore", operating, True),
        ("owner_compensation_taxes", "3.31", "Encargos sobre pró-labore", operating, True),
        ("cleaning", "3.40", "Limpeza", operating, False),
        ("packaging", "3.41", "Embalagens", operating, False),
        ("supplies", "3.42", "Materiais de consumo", operating, False),
        ("uniforms", "3.43", "Uniformes", operating, False),
        ("maintenance", "3.50", "Manutenção", operating, False),
        ("refrigeration", "3.51", "Refrigeração", operating, False),
        ("pest_control", "3.52", "Dedetização", operating, False),
        ("equipment", "3.53", "Equipamentos", operating, False),
        ("accounting", "3.60", "Contabilidade", operating, False),
        ("legal", "3.61", "Jurídico", operating, False),
        ("licenses", "3.62", "Licenças", operating, False),
        ("systems", "3.63", "Sistemas", operating, False),
        ("mobne", "3.64", "Mobne", operating, False),
        ("advertising", "3.70", "Publicidade", operating, False),
        ("campaigns", "3.71", "Campanhas", operating, False),
        ("delivery", "3.72", "Entregas", operating, False),
        ("commissions", "3.73", "Comissões", operating, False),
        ("taxes", "4.01", "Impostos", AccountNature.TAX_EXPENSE.value, True),
        ("bank_fees", "5.01", "Tarifas bancárias", financial, False),
        ("loan_interest", "5.02", "Juros de empréstimos", financial, True),
        ("fines", "5.03", "Juros e multas", financial, False),
        ("receivables_advance", "5.04", "Antecipação de recebíveis", financial, True),
        ("acquiring_fees", "5.05", "Taxas de adquirentes e cartões", operating, False),
        ("payment_terminal_rent", "5.06", "Aluguel de máquinas", operating, False),
        ("expired_loss", "6.01", "Perdas por vencimento", operating, False),
        ("damage_loss", "6.02", "Perdas por avaria e quebra", operating, False),
        ("inventory_loss", "6.03", "Perdas de inventário", operating, False),
        ("loan_proceeds", "7.01", "Recebimento de empréstimo", AccountNature.FINANCING_INFLOW.value, True),
        ("loan_principal", "7.02", "Amortização de principal", AccountNature.LOAN_PRINCIPAL.value, True),
        ("profit_distribution", "8.01", "Distribuição de lucros", AccountNature.PROFIT_DISTRIBUTION.value, True),
        ("internal_transfer", "9.01", "Transferência entre contas", AccountNature.TRANSFER.value, False),
    ]
    return tuple(AccountDefinition(*row) for row in rows)


DEFAULT_ACCOUNTS = {definition.key: definition for definition in _definitions()}


def default_account(key: str) -> AccountDefinition:
    try:
        return DEFAULT_ACCOUNTS[key]
    except KeyError as exc:
        raise ValueError("Conta padrão desconhecida.") from exc


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _seed_defaults(company: int) -> None:
    timestamp = db.now()
    with db.connection() as conn:
        for account in DEFAULT_ACCOUNTS.values():
            conn.execute(
                """
                INSERT INTO finance_accounts(
                    id,company,code,name,nature,system_key,sensitive,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(company,system_key) DO NOTHING
                """,
                (
                    _new_id(),
                    company,
                    account.code,
                    account.name,
                    account.nature,
                    account.key,
                    int(account.sensitive),
                    timestamp,
                    timestamp,
                ),
            )


def _row(row) -> dict:
    result = dict(row)
    result["sensitive"] = bool(result["sensitive"])
    result["archived"] = bool(result["archived"])
    return result


def list_accounts(company: int, include_archived: bool = False) -> list:
    _seed_defaults(company)
    where = "" if include_archived else " AND archived=0"
    with db.connection() as conn:
        return [
            _row(row)
            for row in conn.execute(
                "SELECT * FROM finance_accounts WHERE company=?" + where + " ORDER BY code,name",
                (company,),
            )
        ]


def _slug(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "-".join("".join(c if c.isalnum() else " " for c in plain.lower()).split())


def create_account(
    company: int,
    name: str,
    nature: AccountNature,
    *,
    code: Optional[str] = None,
    sensitive: bool = False,
) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Informe o nome da conta.")
    nature_value = nature.value if isinstance(nature, AccountNature) else AccountNature(nature).value
    account_id = _new_id()
    timestamp = db.now()
    final_code = (code or "custom-" + _slug(clean_name)).strip()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO finance_accounts(
                id,company,code,name,nature,sensitive,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                account_id,
                company,
                final_code,
                clean_name,
                nature_value,
                int(sensitive),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM finance_accounts WHERE id=?", (account_id,)
        ).fetchone()
    return _row(row)


def archive_account(company: int, account_id: int, *, expected_version: int) -> None:
    with db.connection() as conn:
        changed = conn.execute(
            """
            UPDATE finance_accounts SET archived=1,version=version+1,updated_at=?
            WHERE id=? AND company=? AND version=? AND archived=0
            """,
            (db.now(), account_id, company, expected_version),
        )
        if changed.rowcount != 1:
            raise ValueError("Conta não encontrada ou alterada por outro usuário.")


def set_parameter(
    company: int,
    key: str,
    value,
    effective_from: str,
    *,
    effective_to: Optional[str] = None,
    store: Optional[int] = None,
    category: Optional[int] = None,
    product: Optional[int] = None,
    responsible_user: Optional[int] = None,
) -> dict:
    if sum(item is not None for item in (store, category, product)) > 1:
        raise ValueError("Informe somente um escopo específico.")
    parameter_id = _new_id()
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO management_parameters(
                id,company,key,value_json,store,category,product,effective_from,
                effective_to,responsible_user,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(company,key,store,category,product,effective_from)
            DO UPDATE SET value_json=excluded.value_json,effective_to=excluded.effective_to,
                responsible_user=excluded.responsible_user,
                version=management_parameters.version+1,updated_at=excluded.updated_at
            """,
            (
                parameter_id,
                company,
                key,
                json.dumps(value, ensure_ascii=False),
                store or 0,
                category or 0,
                product or 0,
                effective_from,
                effective_to,
                responsible_user,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM management_parameters
            WHERE company=? AND key=? AND store=? AND category=? AND product=?
                AND effective_from=?
            """,
            (
                company,
                key,
                store or 0,
                category or 0,
                product or 0,
                effective_from,
            ),
        ).fetchone()
    return dict(row)


def resolve_parameter(
    company: int,
    key: str,
    on_date: str,
    *,
    store: Optional[int] = None,
    category: Optional[int] = None,
    product: Optional[int] = None,
):
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT value_json FROM management_parameters
            WHERE company=? AND key=? AND effective_from<=?
              AND (effective_to IS NULL OR effective_to>=?)
              AND (store=0 OR store=?)
              AND (category=0 OR category=?)
              AND (product=0 OR product=?)
            ORDER BY
              CASE WHEN product<>0 THEN 4 WHEN category<>0 THEN 3
                   WHEN store<>0 THEN 2 ELSE 1 END DESC,
              effective_from DESC
            LIMIT 1
            """,
            (company, key, on_date, on_date, store or 0, category or 0, product or 0),
        ).fetchone()
    return json.loads(row["value_json"]) if row else None
