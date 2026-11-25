"""FastAPI main application for the Control Plane."""

from fastapi import FastAPI

app = FastAPI(
    title="OverWatch Control Plane",
    description="MCP Pocket Computer control plane API",
    version="0.1.0",
)


@app.get("/health")
@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
