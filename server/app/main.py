"""Biz360X reference API server (FastAPI).

Implements the core vertical slice of openapi.yaml end to end against the
real schema: sign-in, /me, obligations, approvals with the two-person rule,
invoice preview → draft → issue (numbering + IRN job), receipts with
allocation, jobs. The remaining operations follow the same patterns.
"""
from __future__ import annotations

import pathlib

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from .core import ApiError, problem_response
from .routes_compliance import router as compliance
from .routes_platform import router as platform
from .routes_sales import router as sales

app = FastAPI(title="Biz360X API (reference)", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(ApiError)
async def api_error(request: Request, e: ApiError):
    return problem_response(request, e)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, e: RequestValidationError):
    errs = [{"field": ".".join(str(x) for x in err["loc"][1:]), "message": err["msg"]} for err in e.errors()]
    return problem_response(request, ApiError(400, "validation_failed", "Validation failed", errors=errs))


@app.get("/v1/openapi.yaml", include_in_schema=False)
def spec():
    return FileResponse(pathlib.Path(__file__).resolve().parents[2] / "api" / "openapi.yaml", media_type="application/yaml")


@app.get("/healthz", include_in_schema=False)
def health():
    return JSONResponse({"ok": True})


for r in (platform, compliance, sales):
    app.include_router(r, prefix="/v1")
