"""HSN/SAC lookup endpoint — auto-fetch HSN code + GST rate from a product name.

``GET /hsn-codes?search=refrigerator`` → ranked matches the Item form turns into
``hsn_sac_code`` + ``gst_treatment`` (and the matching *GST {rate}%* Item Tax
Template). Read-only reference data; gated on ``Item`` read since that is who
edits items. The dataset is global (same for every tenant), so no company scope.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.compliance import HsnCodeMatch
from app.services import hsn_lookup as service

router = APIRouter(prefix="/hsn-codes", tags=["compliance: hsn lookup"])


@router.get(
    "",
    response_model=list[HsnCodeMatch],
    summary="Search HSN/SAC codes by product name or code",
    description="Ranked lookup over the official HSN rate schedule. Pass a product "
    "name (e.g. `refrigerator`) or a code fragment (e.g. `8418`). Each match carries "
    "the standard GST rate and treatment for auto-filling an item.",
)
async def search_hsn_codes(
    current_user: Annotated[CurrentUser, Depends(require_permission("Item", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    search: Annotated[str, Query(min_length=2, description="Product name or HSN code fragment")],
    limit: Annotated[int, Query(ge=1, le=50)] = 15,
) -> list[HsnCodeMatch]:
    return [HsnCodeMatch.model_validate(r) for r in await service.search_hsn(db, search, limit)]
