import uuid

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from app.core.middleware import RequestIDMiddleware


@pytest.fixture
def test_app():
    """Create a test FastAPI app with RequestIDMiddleware."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(request: Request):
        return {"request_id": request.state.request_id}

    app.add_middleware(RequestIDMiddleware)
    return app


@pytest.mark.unit
def test_middleware_adds_request_id(test_app):
    """Test that middleware adds request_id to request state."""
    client = TestClient(test_app)
    response = client.get("/test")
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert isinstance(data["request_id"], str)
    assert len(data["request_id"]) > 0


@pytest.mark.unit
def test_middleware_adds_request_id_header(test_app):
    """Test that middleware adds X-Request-ID header to response."""
    client = TestClient(test_app)
    response = client.get("/test")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert isinstance(response.headers["X-Request-ID"], str)


@pytest.mark.unit
def test_middleware_uses_custom_request_id(test_app):
    """Test that middleware uses custom X-Request-ID from headers."""
    client = TestClient(test_app)
    custom_id = "custom-request-id-123"
    response = client.get("/test", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == custom_id
    assert response.headers["X-Request-ID"] == custom_id


@pytest.mark.unit
def test_middleware_generates_uuid(test_app):
    """Test that middleware generates valid UUID when no header provided."""
    client = TestClient(test_app)
    response = client.get("/test")
    assert response.status_code == 200
    data = response.json()
    request_id = data["request_id"]

    # Should be a valid UUID
    try:
        uuid.UUID(request_id)
    except ValueError:
        pytest.fail(f"Request ID {request_id} is not a valid UUID")
