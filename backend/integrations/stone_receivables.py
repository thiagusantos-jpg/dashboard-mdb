from __future__ import annotations

import base64
import gzip
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional
from xml.etree import ElementTree


_PRODUCTION_BASE = "https://conciliation.stone.com.br/v2/merchant"


class ReceivablesFileError(ValueError):
    pass


@dataclass(frozen=True)
class Receivable:
    transaction_key: str
    installment_number: int
    brand_id: Optional[str]
    gross_cents: int
    fee_cents: int
    net_cents: int
    settlement_date: Optional[str]


def _to_cents(raw: Optional[str]) -> int:
    if raw is None:
        return 0
    try:
        return int((Decimal(raw) * 100).to_integral_value())
    except InvalidOperation:
        raise ReceivablesFileError(f"Valor inválido no arquivo de conciliação: {raw!r}") from None


def _text(el, tag: str) -> Optional[str]:
    found = el.find(tag)
    return found.text.strip() if found is not None and found.text else None


def _ddmmyyyy_to_iso(raw: Optional[str]) -> Optional[str]:
    # PrevisionPaymentDate/OriginalPaymentDate use aaaammdd per Stone's layout docs.
    if not raw or len(raw) != 8:
        return None
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def parse_conciliation_xml(content: bytes) -> list:
    """Parses a Stone conciliation-file (layout 2.4) FinancialTransactions
    section into one Receivable per installment — GrossAmount/MdrAmount+SaleFee/
    NetAmount and PrevisionPaymentDate are the fields confirmed in
    docs/integrations (Transaction and Installment schemas)."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ReceivablesFileError(f"XML de conciliação inválido: {exc}") from None

    receivables = []
    for transaction in root.iter("Transaction"):
        transaction_key = _text(transaction, "AcquirerTransactionKey")
        brand_id = _text(transaction, "BrandId")
        if not transaction_key:
            continue
        installments_container = transaction.find("Installments")
        if installments_container is None:
            continue
        for installment in installments_container.findall("Installment"):
            number_raw = _text(installment, "InstallmentNumber")
            gross = _to_cents(_text(installment, "GrossAmount"))
            net = _to_cents(_text(installment, "NetAmount"))
            mdr = _to_cents(_text(installment, "MdrAmount"))
            sale_fee = _to_cents(_text(installment, "SaleFee"))
            fee = mdr + sale_fee
            settlement = _ddmmyyyy_to_iso(_text(installment, "PrevisionPaymentDate"))
            receivables.append(Receivable(
                transaction_key=transaction_key,
                installment_number=int(number_raw) if number_raw else 1,
                brand_id=brand_id,
                gross_cents=gross,
                fee_cents=fee,
                net_cents=net,
                settlement_date=settlement,
            ))
    return receivables


def fetch_conciliation_file(
    affiliation_code: str,
    reference_date: str,
    api_key: str,
    *,
    layout: str = "XML2_4",
    http_client=None,
) -> bytes:
    """Real call against Stone's conciliation API — Basic auth with the Portal
    Stone API key as username and an empty password, per the confirmed spec
    (docs/integrations/open-finance-provider-evaluation.md links this API).
    There is no sandbox for this endpoint; a real Stone merchant account and
    API key are required to exercise this against live data."""
    if http_client is None:
        import httpx
        http_client = httpx.Client(timeout=30)
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    response = http_client.get(
        f"{_PRODUCTION_BASE}/{affiliation_code}/conciliation-file/{reference_date}",
        params={"layout": layout},
        headers={
            "Authorization": f"Basic {token}",
            "x-user-type": "client",
            "Accept-Encoding": "gzip",
        },
    )
    response.raise_for_status()
    content = response.content
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return content
