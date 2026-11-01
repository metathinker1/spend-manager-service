"""Transaction list and line filter (2026-10-31-spend-manager-ui FD §2.1 BR-1, §2.2 BR-2)."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError
from read_fixture import CARD, CHECKING, GROCERIES, HOUSEHOLD, TRANSFER, build_read_db

from spend_manager.api.models import LineFilterParams, TransactionFilter
from spend_manager.db.schema import open_db, open_read_db
from spend_manager.services.transactions_query import get_transaction_page, iter_csv_lines

ALL_IDS_NEWEST_FIRST = [108, 107, 106, 104, 103, 111, 102, 101, 109, 110]


@pytest.fixture
def db_path(tmp_path):
    return build_read_db(tmp_path / "sm.db")


@pytest.fixture
def conn(db_path):
    connection = open_read_db(db_path)
    try:
        yield connection
    finally:
        connection.close()


def page(conn, **params):
    return get_transaction_page(conn, TransactionFilter(**params))


def ids(result):
    return [item.id for item in result.items]


def split_matches(result, transaction_id):
    item = next(i for i in result.items if i.id == transaction_id)
    return {s.id: s.matches for s in item.splits}


def test_default_excludes_transfers_and_orders_newest_first(conn):
    result = page(conn)

    assert ids(result) == ALL_IDS_NEWEST_FIRST  # T105 (Transfer) is hidden
    assert result.total_transactions == 10
    assert result.matching_line_count == 11
    assert result.matching_total_cents == 266767
    assert all(item.matches for item in result.items)


def test_same_date_orders_by_id_descending(conn):
    assert ids(page(conn, date_from="2026-01-20", date_to="2026-01-20")) == [111, 102]


def test_transaction_fields(conn):
    item = next(i for i in page(conn).items if i.id == 102)

    assert item.model_dump() == {
        "id": 102,
        "date": dt.date(2026, 1, 20),
        "account_id": CARD,
        "account_name": "Card",
        "type": "withdrawal",
        "payee": "Cafe Rio",
        "memo": "lunch with team",
        "amount_cents": -2500,
        "category_id": 12,
        "category_name": "Dining",
        "is_split": False,
        "is_transfer": False,
        "matches": True,
        "splits": [],
    }


def test_split_transaction_returns_all_splits_in_sequence(conn):
    item = next(i for i in page(conn).items if i.id == 106)

    assert item.is_split and item.category_id is None
    assert [(s.id, s.category_name, s.memo, s.amount_cents) for s in item.splits] == [
        (201, "Groceries", None, -8000),
        (202, "Household", "paper towels", -4000),
    ]


def test_transfer_split_is_returned_but_does_not_match_by_default(conn):
    result = page(conn)

    assert split_matches(result, 107) == {203: False, 204: True}
    split = next(i for i in result.items if i.id == 107).splits[0]
    assert split.is_transfer is True


def test_include_transfers(conn):
    result = page(conn, include_transfers=True)

    assert 105 in ids(result)
    assert result.total_transactions == 11
    assert result.matching_line_count == 13
    assert next(i for i in result.items if i.id == 105).is_transfer is True
    assert split_matches(result, 107) == {203: True, 204: True}


def test_transfer_name_is_case_insensitive(db_path):
    writer = open_db(db_path)
    writer.execute("UPDATE category SET name = 'TRANSFER' WHERE id = ?", (TRANSFER,))
    writer.close()
    conn = open_read_db(db_path)
    try:
        assert 105 not in ids(page(conn))
    finally:
        conn.close()


def test_category_filter_matches_split_lines(conn):
    result = page(conn, category_id=[GROCERIES])

    assert ids(result) == [108, 106, 101, 110]
    assert result.matching_line_count == 4
    assert result.matching_total_cents == -5000 - 8000 + 1500 - 999
    assert split_matches(result, 106) == {201: True, 202: False}


def test_uncategorized_filter(conn):
    result = page(conn, uncategorized=True)

    assert ids(result) == [107, 104]
    assert (result.matching_line_count, result.matching_total_cents) == (2, -6234)


def test_category_or_uncategorized(conn):
    result = page(conn, category_id=[HOUSEHOLD], uncategorized=True)

    assert ids(result) == [107, 106, 104, 109]
    assert result.matching_line_count == 4


def test_transfer_category_matches_nothing_unless_transfers_included(conn):
    assert page(conn, category_id=[TRANSFER]).total_transactions == 0
    assert ids(page(conn, category_id=[TRANSFER], include_transfers=True)) == [107, 105]


def test_account_filter(conn):
    result = page(conn, account_id=[CARD])

    assert ids(result) == [108, 106, 102]
    assert result.matching_line_count == 4


def test_date_range(conn):
    result = page(conn, date_from="2026-02-01", date_to="2026-02-28")

    assert ids(result) == [106, 104]
    assert result.matching_line_count == 3


def test_open_ended_dates(conn):
    assert ids(page(conn, date_from="2026-03-01")) == [108, 107]
    assert ids(page(conn, date_to="2025-12-31")) == [109, 110]


def test_combined_filters(conn):
    result = page(conn, account_id=[CHECKING], category_id=[GROCERIES], date_from="2026-01-01")

    assert ids(result) == [101]


def test_unknown_ids_match_nothing(conn):
    assert page(conn, account_id=[999]).total_transactions == 0
    assert page(conn, category_id=[999]).total_transactions == 0


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("SAFE", [108, 101]),  # payee, case-insensitive
        ("lunch", [102]),  # transaction memo
        ("towels", [106]),  # split memo
        ("100%", [107]),  # '%' is literal
        ("%", [107]),
        ("_", []),  # '_' is literal
    ],
)
def test_search(conn, q, expected):
    assert ids(page(conn, q=q)) == expected


def test_search_in_split_memo_matches_only_that_split(conn):
    result = page(conn, q="towels")

    assert split_matches(result, 106) == {201: False, 202: True}
    assert (result.matching_line_count, result.matching_total_cents) == (1, -4000)


def test_search_in_parent_memo_matches_every_split(conn):
    assert split_matches(page(conn, q="big shop"), 106) == {201: True, 202: True}


def test_blank_search_is_ignored(conn):
    assert ids(page(conn, q="   ")) == ALL_IDS_NEWEST_FIRST


def test_paging(conn):
    first = page(conn, page=1, page_size=3)
    last = page(conn, page=4, page_size=3)
    past_end = page(conn, page=5, page_size=3)

    assert ids(first) == [108, 107, 106]
    assert ids(last) == [110]
    assert past_end.items == []
    for result in (first, last, past_end):
        assert result.total_transactions == 10
        assert result.matching_line_count == 11
    assert (past_end.page, past_end.page_size) == (5, 3)


def test_csv_lines_one_per_matching_line_in_list_order(conn):
    lines = list(iter_csv_lines(conn, LineFilterParams()))

    assert [(line["transaction_id"], line["split_id"]) for line in lines] == [
        (108, None),
        (107, 204),
        (106, 201),
        (106, 202),
        (104, None),
        (103, None),
        (111, None),
        (102, None),
        (101, None),
        (109, None),
        (110, None),
    ]
    split = lines[2]
    assert (split["account"], split["payee"], split["category"], split["memo"]) == (
        "Card",
        "COSTCO",
        "Groceries",
        None,
    )


def test_csv_lines_follow_the_filter(conn):
    lines = list(
        iter_csv_lines(conn, LineFilterParams(category_id=[TRANSFER], include_transfers=True))
    )

    assert [(line["transaction_id"], line["is_transfer"]) for line in lines] == [(107, 1), (105, 1)]


class TestFilterValidation:
    def test_date_from_after_date_to(self):
        with pytest.raises(ValidationError, match="date_from must not be after date_to"):
            TransactionFilter(date_from="2026-02-01", date_to="2026-01-31")

    def test_search_is_trimmed_and_limited(self):
        assert TransactionFilter(q="  coffee ").q == "coffee"
        assert TransactionFilter(q=" " * 5 + "x" * 100 + " ").q == "x" * 100
        with pytest.raises(ValidationError, match="at most 100 characters"):
            TransactionFilter(q="x" * 101)

    @pytest.mark.parametrize(
        "params",
        [
            {"page": 0},
            {"page_size": 0},
            {"page_size": 201},
            {"account_id": list(range(101))},
            {"category_id": list(range(101))},
        ],
    )
    def test_invalid_values(self, params):
        with pytest.raises(ValidationError):
            TransactionFilter(**params)

    def test_line_filter(self):
        lines = TransactionFilter(account_id=[1, 2], uncategorized=True).to_line_filter()

        assert lines.account_ids == (1, 2)
        assert lines.category_ids == ()
        assert lines.uncategorized and not lines.include_transfers
