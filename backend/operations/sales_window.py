"""Receipts dated in a day range, read across every synced sales month the range spans.
Shared by the product map (backend/api.py) and the price action result."""
from __future__ import annotations

from .. import database as db, sync


def receipts_between(company, start, end, conn):
    """Receipts dated start..end (ISO days) and their analysis rows, across the sales months they span."""
    receipts, analysis = [], []
    for key in sync.month_range(start[:7], end[:7]):
        ds = db.dataset(company, 'sales', key, conn)
        if not ds or not ds['payload'].get('raw_count'):
            continue
        receipts += [r for r in ds['payload']['receipts'] if start <= r['date'] <= end]
        analysis += ds['payload'].get('analysis') or []
    return receipts, analysis
