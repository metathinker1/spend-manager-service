"""Read model for a MoneyWell ``persistentStore`` (Core Data SQLite).

Values are kept exactly as stored in MoneyWell (``Z_PK`` keys, integer
enum codes, float/integer amounts, ``YYYYMMDD`` dates); conversion and
validation happen in ``spend_manager.moneywell_store.convert``.
See Functional Design §2.1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MwAccount:
    pk: int
    uid: str | None
    name: str | None
    type_code: int | None
    currency_code: str | None
    is_hidden: bool
    is_debt: bool
    sequence: int | None
    memo: str | None


@dataclass(frozen=True)
class MwBucket:
    pk: int
    uid: str | None
    name: str | None
    type_code: int | None
    is_hidden: bool
    sequence: int | None
    memo: str | None
    currency_code: str | None


@dataclass(frozen=True)
class MwTag:
    pk: int
    name: str | None


@dataclass(frozen=True)
class MwActivity:
    """A Transaction-entity row of ``ZACTIVITY`` (top-level or split child)."""

    pk: int
    uid: str | None
    account_pk: int | None
    bucket_pk: int | None
    split_parent_pk: int | None
    date_ymd: int | None
    type_code: int | None
    amount: int | float | str | None
    payee: str | None
    memo: str | None
    sequence: int | None


@dataclass(frozen=True)
class MoneyWellSnapshot:
    budget_ids: list[str]
    accounts: list[MwAccount]
    buckets: list[MwBucket]
    tags: list[MwTag]
    transactions: list[MwActivity]
    splits: list[MwActivity]
    tag_links: list[tuple[int, int]]  # (activity Z_PK, tag Z_PK)
    non_transaction_activity_pks: frozenset[int]
