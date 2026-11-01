"""``GET /api/accounts`` and ``GET /api/categories`` — filter choices for the UI."""

from __future__ import annotations

from fastapi import APIRouter

from spend_manager.api.deps import ReadConnection
from spend_manager.api.models import Account, Category
from spend_manager.db import queries

router = APIRouter(prefix="/api", tags=["read"])


@router.get("/accounts", response_model=list[Account])
def get_accounts(conn: ReadConnection) -> list[Account]:
    """All accounts (hidden ones included), in MoneyWell order."""
    return [Account.model_validate(dict(row)) for row in queries.list_accounts(conn)]


@router.get("/categories", response_model=list[Category])
def get_categories(conn: ReadConnection) -> list[Category]:
    """All categories (hidden ones included): expense, then income, each in MoneyWell order."""
    return [Category.model_validate(dict(row)) for row in queries.list_categories(conn)]
