# spend-manager-service

MVP FastAPI service that imports a monthly MoneyWell transaction export,
transforms it into a table (one row per transaction, with `Category` and
`Month` added), and writes it to a CSV file.

Built with [AI-DLC](https://github.com) — see the AI-DLC artifacts (requirements,
design, plans) in Google Drive under
`AI-DLC → spend-manager-service → 2026-09-19-import-moneywell-transactions`.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned via `.python-version`
and installed automatically by `uv` if not already present.

```bash
uv sync --all-groups
```

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `MONEYWELL_SOURCE_DIR` | `~/Google Drive/My Drive/AppData/MoneyWell` | Directory containing MoneyWell export files |
| `OUTPUT_DIR` | `<project root>/data/output` | Directory the generated CSV files are written to (git-ignored — contains financial data) |

## Running

```bash
uv run uvicorn spend_manager.main:app --reload
```

Then, with a source file such as `RobBudget_202510_Details.txt` present in
`MONEYWELL_SOURCE_DIR`:

```bash
curl -X POST http://127.0.0.1:8000/imports/2025/10
# {"output_file": ".../data/output/transactions_202510.csv"}

curl http://127.0.0.1:8000/health
# {"status": "ok"}
```

Interactive API docs: <http://127.0.0.1:8000/docs>.

## Endpoint

### `POST /imports/{year}/{month}`

Imports one month's MoneyWell export and writes `transactions_{year}{month}.csv`
to `OUTPUT_DIR`, overwriting any prior file for that month.

- `year` — 4 digits (e.g. `2025`)
- `month` — 2 digits, `01`–`12`

The source file name is derived as `RobBudget_{year}{month}_Details.txt` and
read from `MONEYWELL_SOURCE_DIR`.

**Output CSV columns**: `Month, Category, Payee, Memo, Date, Type, Amount, Currency`.
`Date` is ISO `YYYY-MM-DD`; `Month` is `YYYY-MM` (from the request, not the
transaction dates); `Amount` is a 2-decimal-place string. The source file's
`Reference` column is not included (it is always empty in MoneyWell exports).

**Responses**

| Status | When |
|---|---|
| 201 | Import succeeded — body `{"output_file": "<path>"}` |
| 404 | No source file for the requested month |
| 422 | `year`/`month` malformed |
| 400 | Source file present but not a valid MoneyWell export (wrong header, unparseable row, etc.) |
| 500 | Unexpected I/O error |

### `GET /health`

Returns `{"status": "ok"}`.

## Source file format

Each MoneyWell export is tab-delimited: `Payee, Memo, Date, Type, Reference,
Amount, Currency`. A run of transaction rows for one category is followed by
a **category summary row** (identified by empty `Date` and `Type`) whose
`Payee` holds the category label and whose `Amount` holds the subtotal. The
file ends with a `Report Total` row, which is excluded from the output.

If a category's transactions don't sum to its reported subtotal, or the
category subtotals don't sum to `Report Total`, the import still succeeds —
a warning is logged rather than failing the request.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest              # runs with coverage (see pyproject.toml)
```

`tests/fixtures/synthetic_202501.txt` is a small synthetic export covering
every parsing rule; it is committed to the repo. `tests/test_real_sample.py`
additionally runs against the real MoneyWell export at
`~/Google Drive/My Drive/AppData/MoneyWell/RobBudget_202510_Details.txt` when
that file exists locally, and is skipped otherwise — the real file (personal
financial data) is never copied into the repo.

## Out of scope (this MVP)

Budget report generation, database persistence, multi-month import,
authentication, Docker/deployment, and repairing MoneyWell's payee/memo
truncation.
