"""Convert a raw MoneyWell snapshot into Spend Manager rows, validating as we go.

Implements Functional Design §2.2 (field mappings), §3.1 (money),
§3.2 (validation rules V-1..V-13) and §3.5 (refund / sign convention).
All anomalies are collected in one pass and reported together; any
anomaly rejects the whole import (Q16 = B).

Rows reference each other by MoneyWell unique id / tag name here; the
repository resolves those to Spend Manager ids at write time.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from spend_manager.exceptions import StoreValidationError
from spend_manager.moneywell_store.models import MoneyWellSnapshot, MwActivity

_CENTS = Decimal("0.01")
_MAX_CENT_DEVIATION = Decimal("0.001")

ACCOUNT_TYPES = {0: "checking", 2: "credit_card"}
CATEGORY_TYPES = {1: "income", 2: "expense"}
TRANSACTION_TYPES = {0: "deposit", 1: "withdrawal", 2: "check"}
_INFLOW_TYPES = {"deposit"}


@dataclass(frozen=True)
class NewAccount:
    uid: str
    name: str
    type: str
    currency_code: str | None
    is_hidden: bool
    is_debt: bool
    sequence: int | None
    memo: str | None


@dataclass(frozen=True)
class NewCategory:
    uid: str
    name: str
    type: str
    is_hidden: bool
    sequence: int | None
    memo: str | None
    currency_code: str | None


@dataclass(frozen=True)
class NewTransaction:
    uid: str
    account_uid: str
    category_uid: str | None
    date: str
    type: str
    amount_cents: int
    payee: str | None
    memo: str | None
    is_split: bool


@dataclass(frozen=True)
class NewSplit:
    uid: str
    transaction_uid: str
    category_uid: str | None
    amount_cents: int
    memo: str | None
    sequence: int | None


@dataclass(frozen=True)
class ConvertedSnapshot:
    budget_id: str
    accounts: list[NewAccount]
    categories: list[NewCategory]
    tag_names: list[str]  # distinct, in first-seen order
    transactions: list[NewTransaction]
    splits: list[NewSplit]
    transaction_tags: list[tuple[str, str]]  # (transaction uid, tag name)
    split_tags: list[tuple[str, str]]  # (split uid, tag name)
    warnings: list[str]


class CentsError(ValueError):
    pass


def to_cents(value: int | float | str | Decimal | None) -> int:
    """Convert a MoneyWell amount to integer cents (FD §3.1).

    Floats go through ``str()`` (shortest round-trip repr) so binary noise
    such as ``-31.089999999999996`` becomes ``-3109``. Values more than
    0.001 away from a whole cent are rejected rather than silently rounded.
    """
    if value is None or isinstance(value, bool):
        raise CentsError("amount is missing")
    try:
        exact = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CentsError(f"amount {value!r} is not a number") from exc
    if not exact.is_finite():
        raise CentsError(f"amount {value!r} is not a finite number")
    rounded = exact.quantize(_CENTS, rounding=ROUND_HALF_EVEN)
    if abs(exact - rounded) > _MAX_CENT_DEVIATION:
        raise CentsError(f"amount {value!r} is not a whole number of cents")
    return int(rounded * 100)


def ymd_to_iso(value: int | None) -> str:
    """``20260105`` -> ``"2026-01-05"``; raises ``ValueError`` if not a real date."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"date {value!r} is not a YYYYMMDD integer")
    year, rest = divmod(value, 10000)
    month, day = divmod(rest, 100)
    return datetime.date(year, month, day).isoformat()


def account_type(code: int | None) -> str:
    return ACCOUNT_TYPES.get(code, f"moneywell_type_{code}")


def _blank_to_none(value: str | None) -> str | None:
    return value if value not in (None, "") else None


def validate_snapshot(snapshot: MoneyWellSnapshot) -> ConvertedSnapshot:
    """Validate ``snapshot`` against V-1..V-13 and convert it.

    Raises:
        StoreValidationError: one or more anomalies (all are listed).
    """
    return _Converter(snapshot).run()


class _Converter:
    def __init__(self, snapshot: MoneyWellSnapshot) -> None:
        self.s = snapshot
        self.anomalies: list[str] = []

    def fail(self, rule: str, message: str) -> None:
        self.anomalies.append(f"{rule} {message}")

    def run(self) -> ConvertedSnapshot:
        budget_id = self._budget_id()
        accounts = self._accounts()
        categories = self._categories()
        tag_names_by_pk = self._tags()

        account_uids = {a.pk: a.uid for a in self.s.accounts}
        bucket_uids = {b.pk: b.uid for b in self.s.buckets}
        self._check_unique_uids("activity", [a.uid for a in (*self.s.transactions, *self.s.splits)])

        children: dict[int, list[MwActivity]] = defaultdict(list)
        for split in self.s.splits:
            children[split.split_parent_pk].append(split)

        txn_by_pk = {t.pk: t for t in self.s.transactions}
        transactions: list[NewTransaction] = []
        txn_cents: dict[int, int] = {}
        for txn in self.s.transactions:
            converted = self._activity_common("transaction", txn, account_uids, bucket_uids)
            if converted is None:
                continue
            date, type_, cents, category_uid = converted
            is_split = txn.pk in children
            if is_split and txn.bucket_pk is not None:
                self.fail("V-9", f"split transaction {txn.uid} must not have a bucket")
            txn_cents[txn.pk] = cents
            transactions.append(
                NewTransaction(
                    uid=txn.uid,
                    account_uid=account_uids[txn.account_pk],
                    category_uid=category_uid,
                    date=date,
                    type=type_,
                    amount_cents=cents,
                    payee=_blank_to_none(txn.payee),
                    memo=_blank_to_none(txn.memo),
                    is_split=is_split,
                )
            )

        splits = self._splits(children, txn_by_pk, txn_cents, account_uids, bucket_uids)
        transaction_tags, split_tags, ignored_links = self._tag_links(tag_names_by_pk)

        if self.anomalies:
            raise StoreValidationError(self.anomalies)

        warnings: list[str] = []
        if self.s.non_transaction_activity_pks:
            warnings.append(
                f"{len(self.s.non_transaction_activity_pks)} non-transaction activities "
                "(favorites/scheduled) ignored"
            )
        if ignored_links:
            warnings.append(f"{ignored_links} tag link(s) on non-transaction activities ignored")

        return ConvertedSnapshot(
            budget_id=budget_id,
            accounts=accounts,
            categories=categories,
            tag_names=list(dict.fromkeys(tag_names_by_pk.values())),
            transactions=transactions,
            splits=splits,
            transaction_tags=transaction_tags,
            split_tags=split_tags,
            warnings=warnings,
        )

    # --- V-1 -----------------------------------------------------------------

    def _budget_id(self) -> str:
        ids = self.s.budget_ids
        if len(ids) != 1:
            self.fail("V-1", f"expected exactly one MoneyWell budget id, found {len(ids)}")
            return ""
        return ids[0]

    # --- V-2 / V-3 / V-4 -----------------------------------------------------

    def _check_unique_uids(self, entity: str, uids: list[str | None]) -> None:
        seen: set[str] = set()
        for uid in uids:
            if not uid:
                self.fail("V-2", f"{entity} without a unique id")
            elif uid in seen:
                self.fail("V-2", f"duplicate {entity} unique id {uid}")
            else:
                seen.add(uid)

    def _accounts(self) -> list[NewAccount]:
        self._check_unique_uids("account", [a.uid for a in self.s.accounts])
        result = []
        for a in self.s.accounts:
            if not (a.name or "").strip():
                self.fail("V-3", f"account {a.uid} has no name")
                continue
            result.append(
                NewAccount(
                    uid=a.uid or "",
                    name=a.name,
                    type=account_type(a.type_code),
                    currency_code=_blank_to_none(a.currency_code),
                    is_hidden=a.is_hidden,
                    is_debt=a.is_debt,
                    sequence=a.sequence,
                    memo=_blank_to_none(a.memo),
                )
            )
        return result

    def _categories(self) -> list[NewCategory]:
        self._check_unique_uids("bucket", [b.uid for b in self.s.buckets])
        result = []
        for b in self.s.buckets:
            if not (b.name or "").strip():
                self.fail("V-3", f"bucket {b.uid} has no name")
                continue
            if b.type_code not in CATEGORY_TYPES:
                self.fail("V-3", f"bucket {b.uid} has unexpected type {b.type_code!r}")
                continue
            result.append(
                NewCategory(
                    uid=b.uid or "",
                    name=b.name,
                    type=CATEGORY_TYPES[b.type_code],
                    is_hidden=b.is_hidden,
                    sequence=b.sequence,
                    memo=_blank_to_none(b.memo),
                    currency_code=_blank_to_none(b.currency_code),
                )
            )
        return result

    def _tags(self) -> dict[int, str]:
        names: dict[int, str] = {}
        for t in self.s.tags:
            name = (t.name or "").strip()
            if not name:
                self.fail("V-4", f"tag #{t.pk} has no name")
                continue
            names[t.pk] = name
        return names

    # --- V-5 / V-6 / V-7 / V-13 ---------------------------------------------

    def _activity_common(
        self,
        kind: str,
        act: MwActivity,
        account_uids: dict[int, str | None],
        bucket_uids: dict[int, str | None],
    ) -> tuple[str, str, int, str | None] | None:
        ok = True
        if act.account_pk not in account_uids:
            self.fail("V-5", f"{kind} {act.uid} references missing account #{act.account_pk}")
            ok = False
        if act.bucket_pk is not None and act.bucket_pk not in bucket_uids:
            self.fail("V-6", f"{kind} {act.uid} references missing bucket #{act.bucket_pk}")
            ok = False
        try:
            date = ymd_to_iso(act.date_ymd)
        except ValueError:
            self.fail("V-7", f"{kind} {act.uid} has invalid date {act.date_ymd!r}")
            ok = False
        type_ = TRANSACTION_TYPES.get(act.type_code)
        if type_ is None:
            self.fail("V-7", f"{kind} {act.uid} has unexpected type {act.type_code!r}")
            ok = False
        try:
            cents = to_cents(act.amount)
        except CentsError as exc:
            self.fail("V-7", f"{kind} {act.uid}: {exc}")
            ok = False
        if not ok:
            return None
        if (type_ in _INFLOW_TYPES and cents < 0) or (type_ not in _INFLOW_TYPES and cents > 0):
            self.fail("V-13", f"{kind} {act.uid}: amount sign does not match type {type_}")
            return None
        category_uid = bucket_uids[act.bucket_pk] if act.bucket_pk is not None else None
        return date, type_, cents, category_uid

    # --- V-8 / V-10 / V-11 ---------------------------------------------------

    def _splits(
        self,
        children: dict[int, list[MwActivity]],
        txn_by_pk: dict[int, MwActivity],
        txn_cents: dict[int, int],
        account_uids: dict[int, str | None],
        bucket_uids: dict[int, str | None],
    ) -> list[NewSplit]:
        result: list[NewSplit] = []
        for parent_pk, kids in children.items():
            parent = txn_by_pk.get(parent_pk)
            if parent is None:
                for kid in kids:
                    self.fail("V-8", f"split {kid.uid} has no parent transaction #{parent_pk}")
                continue
            kid_cents: list[int] = []
            for kid in kids:
                converted = self._activity_common("split", kid, account_uids, bucket_uids)
                if converted is None:
                    continue
                date, _type, cents, category_uid = converted
                if kid.account_pk != parent.account_pk or kid.date_ymd != parent.date_ymd:
                    self.fail(
                        "V-11", f"split {kid.uid} account/date differ from transaction {parent.uid}"
                    )
                kid_cents.append(cents)
                result.append(
                    NewSplit(
                        uid=kid.uid,
                        transaction_uid=parent.uid,
                        category_uid=category_uid,
                        amount_cents=cents,
                        memo=_blank_to_none(kid.memo),
                        sequence=kid.sequence,
                    )
                )
            if (
                parent_pk in txn_cents
                and len(kid_cents) == len(kids)
                and sum(kid_cents) != txn_cents[parent_pk]
            ):
                self.fail(
                    "V-10",
                    f"splits of transaction {parent.uid} sum to {sum(kid_cents)} cents, "
                    f"transaction is {txn_cents[parent_pk]} cents",
                )
        result.sort(key=lambda s: (s.transaction_uid, s.sequence if s.sequence is not None else 0))
        return result

    # --- V-12 ----------------------------------------------------------------

    def _tag_links(
        self, tag_names_by_pk: dict[int, str]
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]], int]:
        txn_uids = {t.pk: t.uid for t in self.s.transactions}
        split_uids = {s.pk: s.uid for s in self.s.splits}
        known_tag_pks = {t.pk for t in self.s.tags}
        transaction_tags: dict[tuple[str, str], None] = {}
        split_tags: dict[tuple[str, str], None] = {}
        ignored = 0
        for activity_pk, tag_pk in self.s.tag_links:
            if tag_pk not in known_tag_pks:
                self.fail("V-12", f"tag link references missing tag #{tag_pk}")
                continue
            if activity_pk in self.s.non_transaction_activity_pks:
                ignored += 1
                continue
            if tag_pk not in tag_names_by_pk:
                continue  # unnamed tag, already reported under V-4
            name = tag_names_by_pk[tag_pk]
            if activity_pk in txn_uids:
                transaction_tags[(txn_uids[activity_pk], name)] = None
            elif activity_pk in split_uids:
                split_tags[(split_uids[activity_pk], name)] = None
            else:
                self.fail("V-12", f"tag link references missing activity #{activity_pk}")
        return list(transaction_tags), list(split_tags), ignored
