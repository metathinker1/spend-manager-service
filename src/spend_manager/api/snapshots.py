"""``GET /api/moneywell-snapshots`` — snapshots available for import (BR-4)."""

from __future__ import annotations

from fastapi import APIRouter

from spend_manager.api.deps import SettingsDep
from spend_manager.api.models import Snapshot
from spend_manager.services.snapshots import list_snapshots

router = APIRouter(prefix="/api", tags=["read"])


@router.get(
    "/moneywell-snapshots",
    response_model=list[Snapshot],
    responses={404: {"description": "MONEYWELL_STORE_ROOT not found."}},
)
def get_moneywell_snapshots(settings: SettingsDep) -> list[Snapshot]:
    """Folders and .zip files directly under MONEYWELL_STORE_ROOT, newest first."""
    return list_snapshots(settings.store_root)
