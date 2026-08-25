from unittest.mock import patch

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.handlers import (
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.middleware import RequestIDMiddleware

@pytest.fixture
def test_app():
    """Create a test FastAPI app with exception handlers."""
    app = FastAPI()

    @app.get("/http-error")
    async def http_error():
        raise StarletteHTTPException(status_code=404, detail="Not found")

    @app.get("/validation-error")
    async def validation_error():
        raise RequestValidationError(errors=[])

    @app.get("/general-error")
    async def general_error():
        raise ValueError("Something went wrong")

    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
    app.add_exception_handler(Exception, general_exception_handler)

    return app


@pytest.mark.unit
def test_http_exception_handler(test_app):
    """Test HTTP exception handler."""
    client = TestClient(test_app)
    response = client.get("/http-error")
    assert response.status_code == 404
    data = response.json()
    assert "request_id" in data
    assert "timestamp" in data
    assert "error" in data
    assert data["error"]["code"] == "HTTP_404"
    assert "not found" in data["error"]["message"].lower()


@pytest.mark.unit
def test_validation_exception_handler(test_app):
    """Test validation exception handler."""
    client = TestClient(test_app)
    response = client.get("/validation-error")
    assert response.status_code == 422
    data = response.json()
    assert "request_id" in data
    assert "timestamp" in data
    assert "error" in data
    assert data["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.unit
def test_validation_exception_handler_with_errors():
    """Test validation exception handler with actual validation errors."""
    app = FastAPI()

    @app.get("/test-validation")
    async def test_validation():
        errors = [
            {
                "type": "missing",
                "loc": ("body", "field1"),
                "msg": "Field required",
            },
            {
                "type": "type_error",
                "loc": ("body", "field2"),
                "msg": "value is not a valid integer",
            },
        ]
        raise RequestValidationError(errors=errors)

    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )

    client = TestClient(app)
    response = client.get("/test-validation")
    assert response.status_code == 422
    data = response.json()
    assert "request_id" in data
    assert "timestamp" in data
    assert "error" in data
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "errors" in data
    assert len(data["errors"]) == 2
    assert data["errors"][0]["code"] == "VALIDATION_ERROR"
    assert data["errors"][0]["message"] == "Field required"
    assert data["errors"][0]["field"] == "body.field1"


@pytest.mark.unit
@patch("app.core.handlers.logger")
def test_general_exception_handler(mock_logger, test_app):
    """Test general exception handler."""
    client = TestClient(test_app, raise_server_exceptions=False)
    response = client.get("/general-error")
    assert response.status_code == 500
    data = response.json()
    assert "request_id" in data
    assert "timestamp" in data
    assert "error" in data
    assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"
    # Verify logger was called
    mock_logger.exception.assert_called_once()


@pytest.mark.unit
@patch("app.core.handlers.logger")
def test_exception_handlers_have_request_id(mock_logger, test_app):
    """Test that all exception handlers include request_id."""
    client = TestClient(test_app, raise_server_exceptions=False)

    # Test HTTP exception
    response = client.get("/http-error")
    assert "request_id" in response.json()

    # Test validation exception
    response = client.get("/validation-error")
    assert "request_id" in response.json()

    # Test general exception
    response = client.get("/general-error")
    assert "request_id" in response.json()
