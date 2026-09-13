from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from .. import permissions, security
from ..finance import period_reviews


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class ReviewAction(BaseModel):
    action: str = Field(pattern="^(review|reopen)$")
    expected_revision: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=500)
    acknowledged: bool = False


@router.get("/period-reviews/{period}")
def get_period_review(
    company: int,
    period: str = Path(pattern=PERIOD_PATTERN),
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    return period_reviews.get_review(company, period)


@router.post("/period-reviews/{period}")
def post_period_review(
    company: int,
    body: ReviewAction,
    period: str = Path(pattern=PERIOD_PATTERN),
    auth: security.AuthContext = Depends(permissions.require_permission("settings.manage")),
):
    # Reviewing vouches for totals that include sensitive accounts.
    permissions.ensure_permission(auth, "finance.sensitive.read", company)
    try:
        return period_reviews.record_review(
            company, period, action=body.action, expected_revision=body.expected_revision,
            reason=body.reason, acknowledged=body.acknowledged, actor_id=auth.user_id,
        )
    except period_reviews.ReviewConflict as exc:
        raise HTTPException(409, {"code": "version_conflict", "message": str(exc), "fields": ["expected_revision"]}) from exc
    except period_reviews.ReviewValidation as exc:
        raise HTTPException(422, {"code": "invalid_fields", "message": str(exc), "fields": exc.fields}) from exc
