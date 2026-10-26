"""``GET /health`` and ``GET /health/db`` (FD v2 §1.10).

``/health`` is a cheap liveness check. ``/health/db`` opens the database
read-only, compares its schema version with what the code requires and reports
the most recent backup. Responses never include absolute paths or errors' text.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from spend_manager.config import Settings, get_settings
from spend_manager.db.backup import latest_backup
from spend_manager.db.schema import read_schema_version, required_version

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    env: str


class DatabaseHealthResponse(BaseModel):
    status: Literal["ok", "error"]
    env: str
    database: str
    schema_version: int | None
    required_schema_version: int
    last_backup_at: str | None
    last_backup_file: str | None
    reason: Literal["missing", "unreadable", "schema_outdated", "schema_newer"] | None = None


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(status="ok", env=settings.environment.value)


@router.get(
    "/health/db",
    response_model=DatabaseHealthResponse,
    responses={503: {"model": DatabaseHealthResponse}},
)
def database_health(settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    env = settings.environment.value
    required = required_version()
    backup = latest_backup(settings.backup_dir, env)
    version: int | None = None
    reason: str | None = None

    db_path = settings.db_path
    if not db_path.is_file():
        reason = "missing"
    else:
        try:
            conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                conn.execute("SELECT 1").fetchone()
                version = read_schema_version(conn)
            finally:
                conn.close()
        except sqlite3.Error:
            reason = "unreadable"
        else:
            if version < required:
                reason = "schema_outdated"
            elif version > required:
                reason = "schema_newer"

    body = DatabaseHealthResponse(
        status="error" if reason else "ok",
        env=env,
        database=db_path.name,
        schema_version=version,
        required_schema_version=required,
        last_backup_at=backup.created_at.isoformat() if backup else None,
        last_backup_file=backup.file if backup else None,
        reason=reason,
    )
    return JSONResponse(status_code=503 if reason else 200, content=body.model_dump())
