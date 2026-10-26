"""``POST /imports/moneywell-store`` — import a MoneyWell persistentStore.

See Functional Design §2.4 (API models), §4 (Error Model) and
Requirements FR-1, FR-12, FR-13.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from spend_manager.config import Settings, get_settings
from spend_manager.services.moneywell_store_import import import_store

router = APIRouter()


class MoneyWellStoreImportRequest(BaseModel):
    budget_folder: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Name of a MoneyWell budget folder or .zip backup directly under "
            "MONEYWELL_STORE_ROOT, e.g. 'SampleData'."
        ),
    )


class EntityCounts(BaseModel):
    inserted: int
    skipped_existing: int


class BackupStatus(BaseModel):
    status: str = Field(description="created | skipped_unchanged | failed | disabled")
    file: str | None = None


class MoneyWellStoreImportResponse(BaseModel):
    moneywell_budget_id: str
    counts: dict[str, EntityCounts] = Field(
        description=(
            "Per entity: accounts, categories, tags, transactions, transaction_splits, "
            "transaction_tags, transaction_split_tags."
        )
    )
    warnings: list[str]
    backup: BackupStatus


@router.post(
    "/imports/moneywell-store",
    status_code=201,
    response_model=MoneyWellStoreImportResponse,
    responses={
        400: {
            "description": "Invalid source, unsupported store, validation failure, "
            "budget mismatch, or split conflict. Nothing was written."
        },
        404: {"description": "Store root, budget folder/zip, or persistentStore not found."},
    },
)
def create_moneywell_store_import(
    request: MoneyWellStoreImportRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MoneyWellStoreImportResponse:
    """Import accounts, categories (buckets), tags, transactions (activities) and
    splits from a MoneyWell store. Re-imports insert new records only."""
    summary = import_store(request.budget_folder, settings)
    return MoneyWellStoreImportResponse(
        moneywell_budget_id=summary.moneywell_budget_id,
        counts={
            name: EntityCounts(inserted=c.inserted, skipped_existing=c.skipped_existing)
            for name, c in summary.counts.items()
        },
        warnings=summary.warnings,
        backup=BackupStatus(status=summary.backup.status, file=summary.backup.file),
    )
