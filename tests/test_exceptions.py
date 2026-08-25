from fastapi import status
import pytest

from app.core.exceptions import (
    BaseAPIException,
    InternalServerError,
    NotFoundError,
    ValidationError,
)


@pytest.mark.unit
def test_not_found_error():
    """Test NotFoundError exception."""
    error = NotFoundError("Resource not found")
    assert error.status_code == status.HTTP_404_NOT_FOUND
    assert error.detail == "Resource not found"


@pytest.mark.unit
def test_not_found_error_default_message():
    """Test NotFoundError with default message."""
    error = NotFoundError()
    assert error.status_code == status.HTTP_404_NOT_FOUND
    assert error.detail == "Resource not found"


@pytest.mark.unit
def test_validation_error():
    """Test ValidationError exception."""
    error = ValidationError("Invalid input")
    assert error.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert error.detail == "Invalid input"


@pytest.mark.unit
def test_validation_error_default_message():
    """Test ValidationError with default message."""
    error = ValidationError()
    assert error.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert error.detail == "Validation error"


@pytest.mark.unit
def test_internal_server_error():
    """Test InternalServerError exception."""
    error = InternalServerError("Server error")
    assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert error.detail == "Server error"


@pytest.mark.unit
def test_internal_server_error_default_message():
    """Test InternalServerError with default message."""
    error = InternalServerError()
    assert error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert error.detail == "Internal server error"


@pytest.mark.unit
def test_base_api_exception():
    """Test BaseAPIException base class."""
    error = BaseAPIException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Bad request"
    )
    assert error.status_code == status.HTTP_400_BAD_REQUEST
    assert error.detail == "Bad request"
