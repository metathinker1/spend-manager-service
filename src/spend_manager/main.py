"""FastAPI application entry point.

Registers the health check, the import routers, and the exception handlers
that translate domain exceptions into the HTTP responses defined in
Functional Design §4 (Error Model).
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spend_manager.api.imports import router as imports_router
from spend_manager.api.moneywell_store import router as moneywell_store_router
from spend_manager.exceptions import (
    DatabaseSetupError,
    ImportRejectedError,
    ParseError,
    SourceFileNotFoundError,
    SourceNotFoundError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Spend Manager Service",
    description=(
        "Imports MoneyWell data into the Spend Manager database. "
        "(The monthly text-export → CSV import is deprecated.)"
    ),
    version="0.2.0",
)
app.include_router(moneywell_store_router)
app.include_router(imports_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(SourceFileNotFoundError)
def handle_source_file_not_found(request: Request, exc: SourceFileNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ParseError)
def handle_parse_error(request: Request, exc: ParseError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(SourceNotFoundError)
def handle_source_not_found(request: Request, exc: SourceNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ImportRejectedError)
def handle_import_rejected(request: Request, exc: ImportRejectedError) -> JSONResponse:
    logger.warning("MoneyWell store import rejected: %s", exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(DatabaseSetupError)
def handle_database_setup(request: Request, exc: DatabaseSetupError) -> JSONResponse:
    # Server misconfiguration: the cause is in the message; no traceback needed.
    logger.error("Database setup error while processing %s: %s", request.url, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(sqlite3.Error)
def handle_sqlite_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
    # Handlers run outside the original ``except`` block, so pass the exception
    # explicitly; ``logger.exception`` would log "NoneType: None" instead.
    logger.error("Unhandled database error while processing %s: %s", request.url, exc, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(OSError)
def handle_os_error(request: Request, exc: OSError) -> JSONResponse:
    logger.error("Unhandled OS error while processing %s: %s", request.url, exc, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
