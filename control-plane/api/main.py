"""FastAPI main application for the Control Plane."""

from __future__ import annotations

from control_plane.api.approvals import router as approvals_router
from control_plane.api.audit import router as audit_router
from control_plane.api.deps import ApiError
from control_plane.api.profiles import router as profiles_router
from control_plane.api.sessions import router as sessions_router
from control_plane.api.stream import router as stream_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(
    title="OverWatch Control Plane",
    description="MCP Pocket Computer control plane API",
    version="0.1.0",
)


@app.exception_handler(ApiError)
def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
    """Render structured API errors as ``{"error": ErrorBody}``."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.body.model_dump()},
    )


@app.get("/health")
@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


app.include_router(approvals_router)
app.include_router(audit_router)
app.include_router(profiles_router)
app.include_router(sessions_router)
app.include_router(stream_router)
