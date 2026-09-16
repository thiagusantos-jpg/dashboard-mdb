from __future__ import annotations

import base64
import csv
import gzip
import io
import re
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
    # CSV only: the part of fee_cents that is the anticipation discount, and
    # the portal category ("Venda", "Cobrança", "Cancelamento"...).
    advance_fee_cents: int = 0
    category: str = "Venda"


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


# --- Relatório de recebíveis do portal Stone (CSV) ----------------------------
# The portal exports the same settlement data as the XML, one row per sale:
# DOCUMENTO;STONECODE;CATEGORIA;DATA DA VENDA;DATA DE VENCIMENTO;...;STONE ID;
# QTD DE PARCELAS;Nº DA PARCELA;VALOR BRUTO;VALOR LÍQUIDO;DESCONTO DE MDR;
# DESCONTO DE ANTECIPAÇÃO;DESCONTO UNIFICADO;ÚLTIMO STATUS;...
# Checked against a real Conta Stone statement (jun–ago/2026): the day's net
# sum equals the day's card credits on all 92 days. Pix is not in this report.

_CSV_REQUIRED = ("CATEGORIA", "DATA DE VENCIMENTO", "VALOR BRUTO", "VALOR LÍQUIDO")
_SALE = "Venda"


def _csv_cents(raw: Optional[str]) -> int:
    text = (raw or "").strip()
    if not text:
        return 0
    try:
        value = Decimal(text.replace(".", "").replace(",", "."))
    except InvalidOperation:
        raise ReceivablesFileError(f"Valor inválido no relatório da Stone: {raw!r}") from None
    return int((value * 100).quantize(Decimal(1), rounding="ROUND_HALF_UP"))


def _csv_date(raw: Optional[str]) -> Optional[str]:
    match = re.match(r"^(\d{2})/(\d{2})/(\d{4})", (raw or "").strip())
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "outro"


def looks_like_receivables_csv(content: bytes) -> bool:
    head = content[:600].decode("utf-8-sig", errors="ignore").upper()
    return "STONE ID" in head and "VALOR LÍQUIDO" in head


def parse_receivables_csv(content: bytes) -> list:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    fields = [f.strip() for f in (reader.fieldnames or [])]
    missing = [name for name in _CSV_REQUIRED if name not in fields]
    if missing:
        raise ReceivablesFileError(
            "Este CSV não é o relatório de recebíveis da Stone (faltam as colunas: " + ", ".join(missing) + ")."
        )
    receivables = []
    seen = {}
    for raw_row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items()}
        settlement = _csv_date(row.get("DATA DE VENCIMENTO"))
        if not settlement:
            continue
        category = row.get("CATEGORIA") or _SALE
        gross = _csv_cents(row.get("VALOR BRUTO"))
        net = _csv_cents(row.get("VALOR LÍQUIDO"))
        stone_id = row.get("STONE ID", "")
        number = int(row.get("Nº DA PARCELA") or 1)
        if stone_id and category == _SALE:
            key = stone_id
        elif stone_id:
            key = f"{stone_id}:{_slug(category)}"
        else:
            # Monthly fee and balance adjustments carry no Stone ID: identify
            # them by what they are, numbering exact repeats within the file.
            base = f"{_slug(category)}:{settlement}:{net}"
            seen[base] = seen.get(base, 0) + 1
            key = f"{base}:{seen[base]}"
        receivables.append(Receivable(
            transaction_key=key,
            installment_number=number,
            brand_id=row.get("BANDEIRA") or None,
            gross_cents=gross,
            fee_cents=gross - net if category in (_SALE, "Cancelamento") else 0,
            net_cents=net,
            settlement_date=settlement,
            advance_fee_cents=-_csv_cents(row.get("DESCONTO DE ANTECIPAÇÃO")) if category == _SALE else 0,
            category=category,
        ))
    if not receivables:
        raise ReceivablesFileError("O relatório da Stone não tem nenhuma linha com data de vencimento.")
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
