# spend-manager-service

FastAPI service that imports MoneyWell budget data **directly from MoneyWell's
`persistentStore` SQLite database** into the Spend Manager SQLite database —
accounts, categories (MoneyWell *buckets*), tags, transactions (MoneyWell
*activities*) and splits.

An older endpoint that turns a monthly MoneyWell text export into a CSV is
still available but **deprecated**.

Built with [AI-DLC](https://github.com) — see the AI-DLC artifacts (requirements,
design, plans) in Google Drive under `AI-DLC → spend-manager-service →`
`2026-09-27-import-moneywell-sqlite` (store import) and
`2026-09-19-import-moneywell-transactions` (text-export CSV import).

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned via `.python-version`
and installed automatically by `uv` if not already present.

```bash
uv sync --all-groups
```

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `MONEYWELL_STORE_ROOT` | `~/Project.SpendManager/MoneyWell_Data` | Directory holding MoneyWell budget folders and `.zip` backups |
| `SPEND_MANAGER_DB_PATH` | `<project root>/data/spend_manager.db` | Spend Manager SQLite database (git-ignored — contains financial data) |
| `MONEYWELL_SOURCE_DIR` | `~/Google Drive/My Drive/AppData/MoneyWell` | *(deprecated endpoint)* directory containing MoneyWell text exports |
| `OUTPUT_DIR` | `<project root>/data/output` | *(deprecated endpoint)* directory the generated CSV files are written to (git-ignored) |

## Running

```bash
uv run uvicorn spend_manager.main:app --reload
```

```bash
curl -X POST http://127.0.0.1:8000/imports/moneywell-store \
     -H 'Content-Type: application/json' -d '{"budget_folder": "SampleData_20261003"}'

curl http://127.0.0.1:8000/health
# {"status": "ok"}
```

Interactive API docs: <http://127.0.0.1:8000/docs>.

### MoneyWell snapshots

Each MoneyWell import is taken from a **dated snapshot** folder under
`MONEYWELL_STORE_ROOT`, so every import can be traced back to its source:

```
MoneyWell_Data/
  SampleData_YYYYMMDD/           persistentStore (+ -wal, -shm) copied from MoneyWell
  CompareData_YYYYMMDD/          optional: FullSummary_YYYYMMDD.txt (MoneyWell Summary
                                 Report export) used to reconcile category totals
```

Copy the store while MoneyWell is **closed** and always include
`persistentStore-wal` / `-shm` when present.

## Endpoints

### `POST /imports/moneywell-store`

Imports one MoneyWell budget into the Spend Manager database.

**Request body**: `{"budget_folder": "<name>"}` — the name of a folder or
`.zip` backup **directly under** `MONEYWELL_STORE_ROOT` (a single name, not a
path). Both MoneyWell layouts are accepted: `<folder>/data/persistentStore`
(a budget bundle, e.g. `RobBudget@_@<id>@_@<timestamp>`) and
`<folder>/persistentStore`. A zip must contain exactly one `persistentStore`.

**The MoneyWell source is never modified.** The store and its
`persistentStore-wal` / `-shm` files are copied (or extracted) into a temporary
directory, which is what SQLite opens; the WAL is required because recent
MoneyWell changes live only there.

**What is imported**

| MoneyWell | Spend Manager table | Notes |
|---|---|---|
| Account | `account` | Account/routing numbers and balances are **not** imported |
| Bucket | `category` | `income` / `expense`; bucket groups are a future enhancement |
| Tag | `tag` | Keyed by name (MoneyWell tags with the same name merge) |
| Activity (Transaction entity) | `spend_transaction` | Favorites and scheduled transactions are ignored |
| Split children | `transaction_split` | The parent stays in `spend_transaction` with `is_split = 1` and no category |
| Tags on transactions / splits | `transaction_tag` / `transaction_split_tag` | |

**Re-imports are insert-only.** Records are matched by MoneyWell unique id
(`moneywell_uid`; tags by name). Records already in the database are skipped —
never updated or deleted — because Spend Manager is the system of record after
import (edits happen in spend-manager-ui). New MoneyWell records are added.
Consequences:

- Renames, re-categorizations, amount edits, and tags added in MoneyWell after a
  record was imported are **not** carried over.
- If a transaction that was already imported is later **split** in MoneyWell,
  the import is rejected (400) — its split state cannot change on re-import.
- A record deleted in Spend Manager but still in MoneyWell is re-inserted by the
  next import (known limitation).

**One budget per database.** The first import records the MoneyWell budget id;
a store from any other budget is rejected (400).

**All or nothing.** The store is fully validated first (budget id, unique ids,
references, dates, amounts, split totals, sign vs. type, …); the import then
runs in a single database transaction. Any problem → nothing is written.

**Responses**

| Status | When |
|---|---|
| 201 | Import succeeded — body below |
| 400 | Invalid `budget_folder` (not a single name, escapes the root), unusable zip, not a SQLite file, unsupported MoneyWell layout, validation errors (listed in `detail`), budget mismatch, or split conflict |
| 404 | Store root, budget folder/zip, or `persistentStore` not found |
| 422 | Malformed request body |
| 500 | Unexpected I/O or database error |

```json
{
  "moneywell_budget_id": "E31AED8B-793E-4313-A094-C6E2F77CB82D",
  "counts": {
    "accounts": {"inserted": 4, "skipped_existing": 0},
    "categories": {"inserted": 69, "skipped_existing": 0},
    "tags": {"inserted": 4, "skipped_existing": 0},
    "transactions": {"inserted": 3777, "skipped_existing": 0},
    "transaction_splits": {"inserted": 345, "skipped_existing": 0},
    "transaction_tags": {"inserted": 8, "skipped_existing": 0},
    "transaction_split_tags": {"inserted": 0, "skipped_existing": 0}
  },
  "warnings": ["486 non-transaction activities (favorites/scheduled) ignored"]
}
```

### `POST /imports/{year}/{month}` *(deprecated)*

Imports one month's MoneyWell **text export** (`RobBudget_{year}{month}_Details.txt`
in `MONEYWELL_SOURCE_DIR`) and writes `transactions_{year}{month}.csv` to
`OUTPUT_DIR`, overwriting any prior file for that month. Use
`POST /imports/moneywell-store` instead.

- `year` — 4 digits (e.g. `2025`); `month` — 2 digits, `01`–`12`
- **Output CSV columns**: `Month, Category, Payee, Memo, Date, Type, Amount, Currency`
  (`Date` ISO `YYYY-MM-DD`; `Month` `YYYY-MM` from the request; `Amount` 2 dp)
- **Responses**: 201 `{"output_file": "<path>"}` · 404 no source file · 422 bad
  `year`/`month` · 400 not a valid MoneyWell export · 500 unexpected I/O error

The export is tab-delimited (`Payee, Memo, Date, Type, Reference, Amount,
Currency`); each category's transactions are followed by a summary row (empty
`Date` and `Type`) holding the category label and subtotal, and the file ends
with a `Report Total` row. Subtotal mismatches are logged as warnings.

### `GET /health`

Returns `{"status": "ok"}`.

## Spend Manager database

Schema version 2 (`src/spend_manager/db/schema.py`), created or upgraded automatically on
first import and tracked in `schema_version`; later changes are added as new
migrations. Tables: `account`, `category`, `tag`, `spend_transaction`,
`transaction_split`, `transaction_tag`, `transaction_split_tag`,
`import_metadata` (bound MoneyWell budget id, first/last import time).

Every table has its own `id INTEGER PRIMARY KEY`; imported rows also carry
`moneywell_uid` (nullable, unique) so rows created later in the UI can coexist.

**Money** is stored as signed **integer cents** (`amount_cents`), converted from
MoneyWell's floating-point amounts exactly (e.g. `-31.089999999999996` → `-3109`).

**Sign convention and refunds**

- `amount_cents` is authoritative for direction: money **in** is positive,
  money **out** is negative. `type` (`deposit` / `withdrawal` / `check`) must
  agree with the sign — imports with a mismatch are rejected.
- A **refund** is not a separate type: it is a positive amount (a `deposit`, or
  a positive split) in an `expense` category. Partial refunds inside a split
  work the same way.
- Net spending for an expense category = −Σ `amount_cents` over
  `spend_transaction` rows with `is_split = 0` plus `transaction_split` rows in
  that category (never the split parent). Refunds therefore reduce spending.
  Note that not every positive amount in an expense category is a refund
  (e.g. card payments categorized as "Transfer").

**Reporting view `category_line`** (read-only, schema v2) — one row per
categorizable line: every non-split transaction plus every split row, never a
split parent. Columns: `spend_transaction_id`, `transaction_split_id` (NULL for
non-split rows), `date`, `account_id`, `category_id` (NULL = no bucket), `payee`,
`memo`, `amount_cents`, `amount` (dollars, for display).

Category totals that reconcile to the cent with a MoneyWell Summary Report:

```sql
SELECT COALESCE(c.name, '(no bucket)')             AS category,
       printf('%.2f', SUM(l.amount_cents) / 100.0) AS amount
FROM category_line l LEFT JOIN category c ON c.id = l.category_id
WHERE l.date >= '2026-01-01' AND l.date < '2027-01-01'   -- optional date range
GROUP BY 1 ORDER BY 1;
```

Aggregate `amount_cents` and divide by `100.0` once at the end: `/ 100` is
integer division in SQLite (drops the cents), and summing the per-row
`amount` adds floating-point values.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest              # runs with coverage (see pyproject.toml)
```

Tests never use real financial data from the repo:

- `tests/moneywell_fixture.py` builds a small synthetic, Core-Data-shaped
  MoneyWell store (optionally with all data only in the WAL) for the store
  import tests; `tests/fixtures/synthetic_202501.txt` is a synthetic text export.
- `tests/test_real_moneywell_store.py` imports the **newest**
  `SampleData_YYYYMMDD` snapshot under `MONEYWELL_STORE_ROOT` into a temporary
  database, checks counts derived from the snapshot itself, and — when a
  same-dated `CompareData_YYYYMMDD/FullSummary_*.txt` exists — reconciles every
  category total and the report total to the cent. `tests/test_real_sample.py`
  parses the real text export. Both are skipped when that data is absent.

## Out of scope

Budget reports and read/query endpoints, bucket groups, multiple budgets per
database, updating or deleting imported records via re-import, a UI
(spend-manager-ui), authentication, and Docker/deployment.
