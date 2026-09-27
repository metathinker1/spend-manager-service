"""FastAPI application entry point.

Registers the health check, the imports router, and the exception handlers
that translate domain exceptions into the HTTP responses defined in
Functional Design §4 (Error Model).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spend_manager.api.imports import router as imports_router
from spend_manager.exceptions import ParseError, SourceFileNotFoundError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Spend Manager Service",
    description="Imports MoneyWell transaction exports and produces per-month transaction CSVs.",
    version="0.1.0",
)
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


@app.exception_handler(OSError)
def handle_os_error(request: Request, exc: OSError) -> JSONResponse:
    logger.exception("Unhandled OS error while processing %s", request.url)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
