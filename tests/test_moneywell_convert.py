"""Tests for spend_manager.moneywell_store.convert (FD §2.2, §3.1, §3.2, §3.5)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from moneywell_fixture import BUDGET_ID, activity, add_transaction, default_store, write_store

from spend_manager.exceptions import StoreValidationError
from spend_manager.moneywell_store.convert import (
    CentsError,
    account_type,
    to_cents,
    validate_snapshot,
    ymd_to_iso,
)
from spend_manager.moneywell_store.reader import read_store


def _convert(tmp_path, store=None):
    return validate_snapshot(read_store(write_store(store or default_store(), tmp_path)))


def _anomalies(tmp_path, store) -> list[str]:
    with pytest.raises(StoreValidationError) as exc_info:
        _convert(tmp_path, store)
    return exc_info.value.anomalies


# --- money / dates / enums ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-31.089999999999996, -3109),
        (0.1 + 0.2, 30),
        (4085.8, 408580),
        (2500, 250000),
        (-745, -74500),
        ("12.34", 1234),
        (Decimal("12.3405"), 1234),  # within 0.001 of a whole cent
        (Decimal("-12.3395"), -1234),
        (0, 0),
    ],
)
def test_to_cents(value, expected):
    assert to_cents(value) == expected


@pytest.mark.parametrize(
    "value", [None, True, "abc", 1.2345, Decimal("0.005"), Decimal("NaN"), float("inf")]
)
def test_to_cents_rejects(value):
    with pytest.raises(CentsError):
        to_cents(value)


def test_ymd_to_iso():
    assert ymd_to_iso(20260105) == "2026-01-05"


@pytest.mark.parametrize("value", [None, 0, 20261340, 20260230, "20260105", True])
def test_ymd_to_iso_rejects(value):
    with pytest.raises(ValueError):
        ymd_to_iso(value)


def test_account_type_mapping_preserves_unknown_codes():
    assert account_type(0) == "checking"
    assert account_type(2) == "credit_card"
    assert account_type(5) == "moneywell_type_5"


# --- happy path -----------------------------------------------------------------


def test_default_store_converts(tmp_path):
    result = _convert(tmp_path)

    assert result.budget_id == BUDGET_ID
    assert [(a.uid, a.type, a.is_hidden, a.is_debt) for a in result.accounts] == [
        ("ACC-1", "checking", False, False),
        ("ACC-2", "credit_card", True, False),
    ]
    assert [(c.name, c.type) for c in result.categories] == [
        ("Salary", "income"),
        ("Groceries", "expense"),
        ("Auto: Fuel", "expense"),
    ]
    assert result.tag_names == ["Christmas", "Haircut"]

    txns = {t.uid: t for t in result.transactions}
    assert len(txns) == 7
    assert txns["TXN-2"].amount_cents == -3109
    assert txns["TXN-2"].date == "2026-01-05"
    assert txns["TXN-3"].category_uid is None  # uncategorized is fine (Q8)
    assert txns["TXN-4"].type == "check"
    assert txns["TXN-5"].is_split and txns["TXN-5"].category_uid is None
    assert not txns["TXN-1"].is_split

    splits = {s.uid: s for s in result.splits}
    assert [s.uid for s in result.splits] == ["SPL-1", "SPL-2", "SPL-3", "SPL-4", "SPL-5"]
    assert splits["SPL-2"].memo == "fuel"
    assert splits["SPL-3"].category_uid is None
    assert splits["SPL-5"].amount_cents == 500

    assert result.transaction_tags == [("TXN-2", "Christmas"), ("TXN-6", "Christmas")]
    assert result.split_tags == [("SPL-1", "Haircut")]
    assert result.warnings == [
        "1 non-transaction activities (favorites/scheduled) ignored",
        "1 tag link(s) on non-transaction activities ignored",
    ]


def test_refund_is_a_positive_deposit_in_an_expense_category(tmp_path):
    refund = next(t for t in _convert(tmp_path).transactions if t.uid == "TXN-6")
    assert (refund.type, refund.amount_cents, refund.category_uid) == ("deposit", 4273, "BKT-2")


def test_empty_strings_become_null(tmp_path):
    store = default_store()
    activity(store, "TXN-1")["ZMEMO"] = ""
    activity(store, "TXN-1")["ZPAYEE"] = ""
    txn = next(t for t in _convert(tmp_path, store).transactions if t.uid == "TXN-1")
    assert txn.memo is None and txn.payee is None


# --- one test per validation rule -------------------------------------------------


def test_v1_budget_id_required(tmp_path):
    store = default_store()
    store["budget_ids"] = []
    assert _anomalies(tmp_path, store) == ["V-1 expected exactly one MoneyWell budget id, found 0"]


def test_v1_multiple_budget_ids(tmp_path):
    store = default_store()
    store["budget_ids"] = ["A", "B"]
    assert any(a.startswith("V-1") for a in _anomalies(tmp_path, store))


def test_v2_duplicate_and_missing_uids(tmp_path):
    store = default_store()
    store["accounts"][1]["ZUNIQUEID"] = "ACC-1"
    activity(store, "TXN-3")["ZUNIQUEID"] = None
    anomalies = _anomalies(tmp_path, store)
    assert "V-2 duplicate account unique id ACC-1" in anomalies
    assert "V-2 activity without a unique id" in anomalies


def test_v3_bucket_type_and_names(tmp_path):
    store = default_store()
    store["buckets"][0]["ZTYPE"] = 7
    store["accounts"][0]["ZNAME"] = "  "
    store["buckets"][1]["ZNAME"] = None
    anomalies = _anomalies(tmp_path, store)
    assert "V-3 bucket BKT-1 has unexpected type 7" in anomalies
    assert "V-3 account ACC-1 has no name" in anomalies
    assert "V-3 bucket BKT-2 has no name" in anomalies


def test_v4_tag_name_required(tmp_path):
    store = default_store()
    store["tags"][1]["ZNAME"] = " "
    assert _anomalies(tmp_path, store) == ["V-4 tag #2 has no name"]


def test_v5_missing_account(tmp_path):
    store = default_store()
    activity(store, "TXN-1")["ZACCOUNT2"] = 99
    assert _anomalies(tmp_path, store) == ["V-5 transaction TXN-1 references missing account #99"]


def test_v6_missing_bucket(tmp_path):
    store = default_store()
    activity(store, "SPL-2")["ZBUCKET2"] = 99
    assert any(
        a == "V-6 split SPL-2 references missing bucket #99" for a in _anomalies(tmp_path, store)
    )


def test_v7_bad_date_type_amount(tmp_path):
    store = default_store()
    activity(store, "TXN-1")["ZDATEYMD"] = 20261340
    activity(store, "TXN-2")["ZTYPE"] = 9
    activity(store, "TXN-3")["ZAMOUNT"] = -1.2345
    activity(store, "TXN-4")["ZAMOUNT"] = None
    anomalies = _anomalies(tmp_path, store)
    assert "V-7 transaction TXN-1 has invalid date 20261340" in anomalies
    assert "V-7 transaction TXN-2 has unexpected type 9" in anomalies
    assert "V-7 transaction TXN-3: amount -1.2345 is not a whole number of cents" in anomalies
    assert "V-7 transaction TXN-4: amount is missing" in anomalies


def test_v8_split_parent_must_be_a_transaction(tmp_path):
    store = default_store()
    activity(store, "SPL-1")["ZSPLITPARENT"] = 6  # itself: a split, not a transaction
    activity(store, "SPL-3")["ZAMOUNT"] = -70  # keep TXN-5 sum consistent
    assert "V-8 split SPL-1 has no parent transaction #6" in _anomalies(tmp_path, store)


def test_v9_split_parent_without_bucket(tmp_path):
    store = default_store()
    activity(store, "TXN-5")["ZBUCKET2"] = 2
    assert _anomalies(tmp_path, store) == ["V-9 split transaction TXN-5 must not have a bucket"]


def test_v10_split_sum_must_match(tmp_path):
    store = default_store()
    activity(store, "SPL-3")["ZAMOUNT"] = -11
    assert _anomalies(tmp_path, store) == [
        "V-10 splits of transaction TXN-5 sum to -10100 cents, transaction is -10000 cents"
    ]


def test_v11_split_account_and_date_match_parent(tmp_path):
    store = default_store()
    activity(store, "SPL-1")["ZDATEYMD"] = 20260111
    assert _anomalies(tmp_path, store) == [
        "V-11 split SPL-1 account/date differ from transaction TXN-5"
    ]


def test_v12_tag_link_to_missing_tag_or_activity(tmp_path):
    store = default_store()
    store["tag_links"] += [(1, 99), (999, 1)]
    anomalies = _anomalies(tmp_path, store)
    assert "V-12 tag link references missing tag #99" in anomalies
    assert "V-12 tag link references missing activity #999" in anomalies


def test_v13_sign_must_match_type(tmp_path):
    store = default_store()
    activity(store, "TXN-1")["ZAMOUNT"] = -2500  # deposit with negative amount
    add_transaction(store, 50, "TXN-50", ZTYPE=1, ZAMOUNT=10)  # withdrawal, positive
    anomalies = _anomalies(tmp_path, store)
    assert "V-13 transaction TXN-1: amount sign does not match type deposit" in anomalies
    assert "V-13 transaction TXN-50: amount sign does not match type withdrawal" in anomalies


def test_all_anomalies_reported_together(tmp_path):
    store = default_store()
    for i in range(12):
        add_transaction(store, 100 + i, f"BAD-{i}", ZDATEYMD=0)
    with pytest.raises(StoreValidationError) as exc_info:
        _convert(tmp_path, store)
    assert len(exc_info.value.anomalies) == 12
    assert "12 validation error(s)" in str(exc_info.value)
    assert "and 2 more" in str(exc_info.value)
