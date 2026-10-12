"""API surface smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from vouch import __version__
from vouch.api.app import app

client = TestClient(app)


def test_health_is_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_schema_is_served():
    assert client.get("/openapi.json").status_code == 200
