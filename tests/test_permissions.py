from __future__ import annotations

import pytest

from backend.permissions import role_allows


@pytest.mark.parametrize(
    "role,permission,allowed",
    [
        ("administrator", "users.manage", True),
        ("partner", "finance.sensitive.read", True),
        ("manager", "finance.sensitive.read", False),
        ("manager", "inventory.write", True),
        ("viewer", "inventory.write", False),
        ("viewer", "dashboard.read", True),
    ],
)
def test_default_roles(role, permission, allowed):
    assert role_allows(role, permission) is allowed

