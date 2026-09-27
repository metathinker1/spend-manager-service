"""``POST /imports/{year}/{month}`` — import one month's MoneyWell export.

See Functional Design §4 (Error Model) and Requirements FR-1, FR-11, FR-12.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from spend_manager.config import Settings, get_settings
from spend_manager.services.import_service import import_month

router = APIRouter()

# Path parameter patterns double as request validation (-> 422 on mismatch)
# and as the only source used to build the on-disk file path, so no
# unvalidated input ever reaches the filesystem (NFR-3).
_YEAR_PATTERN = r"^\d{4}$"
_MONTH_PATTERN = r"^(0[1-9]|1[0-2])$"


class ImportResponse(BaseModel):
    output_file: str


@router.post("/imports/{year}/{month}", status_code=201, response_model=ImportResponse)
def create_import(
    settings: Annotated[Settings, Depends(get_settings)],
    year: str = Path(..., pattern=_YEAR_PATTERN, description="4-digit year, e.g. 2025"),
    month: str = Path(..., pattern=_MONTH_PATTERN, description="2-digit month, 01-12"),
) -> ImportResponse:
    output_path = import_month(year, month, settings)
    return ImportResponse(output_file=str(output_path))
