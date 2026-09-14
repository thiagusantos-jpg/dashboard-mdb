"""What a price action changed: the product's sales and margin in the 30 days before
the action was created against the 30 days after it was concluded. Partial while those
30 days have not passed yet, so the partner sees it moving instead of waiting a month."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import models
from .sales_window import receipts_between

RESULT_DAYS = 30
_PRICE_KEY = re.compile(r"^preco:(\d+)$")


def _local_day(iso):
    return datetime.fromisoformat(iso).astimezone(ZoneInfo('America/Sao_Paulo')).date()


def _window(company, product_id, start, end, conn):
    receipts, analysis = receipts_between(company, start.isoformat(), end.isoformat(), conn)
    products = models.summarize(receipts, None, analysis)['products']
    p = next((x for x in products if x['id'] == product_id), None)
    return {'start': start.isoformat(), 'end': end.isoformat(),
            'revenue': p['revenue'] if p else 0, 'quantity': p['quantity'] if p else 0.0,
            'margin': p['margin'] if p else None}


def price_action_result(company, action, today, conn):
    match = _PRICE_KEY.match(action['alert_key'] or '')
    if not match:
        return {'kind': 'none'}
    if action['status'] != 'resolved' or not action['resolved_at']:
        return {'kind': 'pending'}
    product_id = int(match.group(1))
    created = _local_day(action['created_at'])
    resolved = _local_day(action['resolved_at'])
    before = _window(company, product_id, created - timedelta(days=RESULT_DAYS), created - timedelta(days=1), conn)
    after_start = resolved + timedelta(days=1)
    after_end = min(resolved + timedelta(days=RESULT_DAYS), today - timedelta(days=1))
    ready_on = (resolved + timedelta(days=RESULT_DAYS + 1)).isoformat()
    if after_end < after_start:
        return {'kind': 'measuring', 'days': 0, 'total': RESULT_DAYS, 'ready_on': ready_on, 'before': before, 'after': None}
    days = (after_end - after_start).days + 1
    return {'kind': 'measured' if days >= RESULT_DAYS else 'measuring', 'days': days, 'total': RESULT_DAYS,
            'ready_on': ready_on, 'before': before,
            'after': _window(company, product_id, after_start, after_end, conn)}
