from __future__ import annotations

from collections import Counter
from itertools import combinations


def basket_pairs(receipts: list, *, min_count: int = 1) -> dict:
    """Counts by canonical receipt (backend/models.py's canonical_receipts()
    already merged fiscal-reference chains into one receipt with an `aliases`
    list) — a receipt is one basket regardless of how many raw documents were
    merged into it, so this never expands aliases back into separate baskets."""
    pair_counts: Counter = Counter()
    product_counts: Counter = Counter()
    for receipt in receipts:
        if receipt.get("status") == "C":
            continue
        product_ids = sorted({
            item["product_id"] for item in receipt["items"] if item.get("status") == "V"
        })
        for pid in product_ids:
            product_counts[pid] += 1
        for a, b in combinations(product_ids, 2):
            pair_counts[(a, b)] += 1
    pairs = [
        {"product_a": a, "product_b": b, "count": count}
        for (a, b), count in pair_counts.items() if count >= min_count
    ]
    pairs.sort(key=lambda p: -p["count"])
    return {
        "receipt_count": len(receipts),
        "product_counts": dict(product_counts),
        "pairs": pairs,
    }
