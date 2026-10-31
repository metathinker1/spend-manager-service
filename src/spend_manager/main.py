"""FastAPI application entry point.

Registers the health and import routers, the exception handlers that
translate domain exceptions into HTTP responses (Functional Design §4, Error
Model), and the lifespan (2026-10-17-sqlite-cloud-production-db FD v2 §1.11):
validate settings, hold the shared run lock, log a startup banner, and take a
backup on shutdown.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spend_manager.api.health import router as health_router
from spend_manager.api.imports import router as imports_router
from spend_manager.api.moneywell_store import router as moneywell_store_router
from spend_manager.config import Settings, get_settings
from spend_manager.db.backup import RunLock, create_backup
from spend_manager.exceptions import (
    BackupError,
    DatabaseSetupError,
    ImportRejectedError,
    ParseError,
    SourceFileNotFoundError,
    SourceNotFoundError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # ConfigurationError aborts startup
    lock = RunLock(settings.db_path).acquire_shared()  # fails while a restore runs
    logger.info(
        "Spend Manager starting: env=%s db=%s backups=%s",
        settings.environment.value,
        settings.db_path,
        settings.backup_dir or "disabled",
    )
    try:
        yield
    finally:
        try:
            _shutdown_backup(settings)
        finally:
            lock.release()


def _shutdown_backup(settings: Settings) -> None:
    if settings.backup_dir is None or not settings.db_path.is_file():
        return
    try:
        result = create_backup(
            settings.db_path,
            settings.backup_dir,
            settings.environment.value,
            keep=settings.spend_manager_backup_keep,
        )
    except (BackupError, OSError) as exc:
        logger.error("Shutdown backup failed: %s", exc, exc_info=exc)
        return
    if result.status == "created":
        logger.info("Shutdown backup: created %s", result.file)
    else:
        logger.info("Shutdown backup: skipped (unchanged since %s)", result.file)


app = FastAPI(
    title="Spend Manager Service",
    description=(
        "Imports MoneyWell data into the Spend Manager database. "
        "(The monthly text-export → CSV import is deprecated.)"
    ),
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(moneywell_store_router)
app.include_router(imports_router)


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
