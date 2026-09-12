from __future__ import annotations

from backend.operations.baskets import basket_pairs


def make_receipt(id_, aliases, product_ids):
    return {
        "id": id_, "reference_id": 0, "aliases": aliases, "date": "2026-09-01", "status": "V", "species": "CF",
        "revenue": 100_00,
        "items": [
            {"id": n, "product_id": pid, "quantity": "1", "revenue": 50_00, "cost": 20_00,
             "unit_cost": "20", "status": "V"}
            for n, pid in enumerate(product_ids)
        ],
    }


def test_receipt_aliases_count_as_one_basket():
    canonical_receipt_with_aliases = [make_receipt(1, [1, 2, 3], [10, 20])]

    result = basket_pairs(canonical_receipt_with_aliases)

    assert result["receipt_count"] == 1


def test_pairs_are_counted_across_multiple_baskets():
    receipts = [
        make_receipt(1, [1], [10, 20]),
        make_receipt(2, [2], [10, 20]),
        make_receipt(3, [3], [10, 30]),
    ]

    result = basket_pairs(receipts)

    pair = next(p for p in result["pairs"] if {p["product_a"], p["product_b"]} == {10, 20})
    assert pair["count"] == 2


def test_cancelled_receipts_are_excluded():
    cancelled = make_receipt(1, [1], [10, 20])
    cancelled["status"] = "C"

    result = basket_pairs([cancelled])

    assert result["pairs"] == []
