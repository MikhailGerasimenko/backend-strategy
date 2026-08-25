from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_v1_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") in ("ok", "healthy", "UP") or "status" in data


def test_navigator_health_requires_auth_or_public(client):
    # /api/health требует сессию; без неё 401 JSON
    response = client.get("/api/health")
    assert response.status_code in (200, 401)
