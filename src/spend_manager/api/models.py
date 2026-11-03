"""Request and response models of the read API (Application Design → Component Methods).

Money is always integer cents (``*_cents``), dates are ISO ``YYYY-MM-DD`` and
months ``YYYY-MM``. The filter models are FastAPI query-parameter models, so
their field names are the query parameter names (``account_id`` and
``category_id`` repeat).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from spend_manager.db.queries import LineFilter

MAX_FILTER_IDS = 100
MAX_SEARCH_LENGTH = 100


class LineFilterParams(BaseModel):
    """Which lines match (BR-1); used as-is by the CSV export."""

    date_from: dt.date | None = Field(default=None, description="Inclusive start date.")
    date_to: dt.date | None = Field(default=None, description="Inclusive end date.")
    account_id: list[int] = Field(
        default_factory=list, max_length=MAX_FILTER_IDS, description="Any of these accounts."
    )
    category_id: list[int] = Field(
        default_factory=list,
        max_length=MAX_FILTER_IDS,
        description="Any of these categories (split lines use the split's category).",
    )
    uncategorized: bool = Field(default=False, description="Also match lines with no category.")
    include_transfers: bool = Field(
        default=False, description="Include lines in the category named 'Transfer'."
    )
    q: str | None = Field(
        default=None,
        description=(
            f"Case-insensitive text in payee, transaction memo or split memo "
            f"(trimmed, at most {MAX_SEARCH_LENGTH} characters)."
        ),
    )

    @field_validator("q")
    @classmethod
    def _trim_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > MAX_SEARCH_LENGTH:
            raise ValueError(f"must be at most {MAX_SEARCH_LENGTH} characters")
        return value or None

    @model_validator(mode="after")
    def _check_date_range(self) -> LineFilterParams:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self

    def to_line_filter(self) -> LineFilter:
        return LineFilter(
            date_from=self.date_from,
            date_to=self.date_to,
            account_ids=tuple(self.account_id),
            category_ids=tuple(self.category_id),
            uncategorized=self.uncategorized,
            include_transfers=self.include_transfers,
            q=self.q,
        )


class TransactionFilter(LineFilterParams):
    """Line filter plus paging for the transaction list (BR-2)."""

    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=50, ge=1, le=200)


class SplitRow(BaseModel):
    id: int
    category_id: int | None
    category_name: str | None
    memo: str | None
    amount_cents: int
    is_transfer: bool
    matches: bool = Field(description="This split line matches the filter.")


class TransactionRow(BaseModel):
    id: int
    date: dt.date
    account_id: int
    account_name: str
    type: Literal["deposit", "withdrawal", "check"]
    payee: str | None
    memo: str | None
    amount_cents: int
    category_id: int | None
    category_name: str | None
    is_split: bool
    is_transfer: bool
    matches: bool
    splits: list[SplitRow]


class TransactionPage(BaseModel):
    items: list[TransactionRow]
    page: int
    page_size: int
    total_transactions: int = Field(description="Transactions with at least one matching line.")
    matching_line_count: int
    matching_total_cents: int = Field(description="Signed sum of the matching lines only.")


class Period(BaseModel):
    kind: Literal["month", "year", "last12"]
    date_from: dt.date
    date_to: dt.date
    label: str
    months: list[str] = Field(description="The months of the period, YYYY-MM, oldest first.")


class SpendingRow(BaseModel):
    category_id: int | None = Field(description="null for the Uncategorized row.")
    category_name: str
    spending_cents: int = Field(description="−SUM(amount_cents); refunds reduce it.")
    line_count: int


class SpendingByCategoryReport(BaseModel):
    period: Period
    rows: list[SpendingRow]
    total_cents: int


class MatrixRow(BaseModel):
    category_id: int | None = Field(description="null for the Uncategorized row.")
    category_name: str
    cells_cents: list[int] = Field(description="Spending per month, aligned to ``months``.")
    total_cents: int


class CategoryByMonthReport(BaseModel):
    period: Period
    months: list[str]
    rows: list[MatrixRow]
    column_totals_cents: list[int]
    grand_total_cents: int


class Account(BaseModel):
    id: int
    name: str
    type: str
    is_hidden: bool


class Category(BaseModel):
    id: int
    name: str
    type: Literal["income", "expense"]
    is_hidden: bool


class Snapshot(BaseModel):
    name: str = Field(description="Folder or .zip name directly under MONEYWELL_STORE_ROOT.")
    kind: Literal["folder", "zip"]
    snapshot_date: dt.date | None
