from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class BaseResponse(BaseModel, Generic[T]):
    """Base API response format with unified structure."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2026-01-22T12:00:00.000000",
                "data": {},
            }
        }
    )

    request_id: str = Field(..., description="Unique request identifier")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    data: T = Field(..., description="Response data")


class ErrorDetail(BaseModel):
    """Error details."""

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    field: str | None = Field(
        None, description="Field name if error is field-specific"
    )


class ErrorResponse(BaseModel):
    """Error response format."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2026-01-22T12:00:00.000000",
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Resource not found",
                },
            }
        }
    )

    request_id: str = Field(..., description="Unique request identifier")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    error: ErrorDetail = Field(..., description="Error details")
    errors: list[ErrorDetail] | None = Field(
        None, description="List of errors for validation"
    )
