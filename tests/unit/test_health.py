"""Unit tests for the health check endpoints."""

from control_plane.api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_api_v1_health_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
