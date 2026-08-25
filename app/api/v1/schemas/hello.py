from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.responses import BaseResponse


class HelloData(BaseModel):
    """Response data for hello endpoint."""

    message: str = Field(
        ...,
        description="Greeting message",
        json_schema_extra={"example": "Hello, World!"},
    )


class HelloResponse(BaseModel):
    """Simple response format for hello endpoint."""

    message: str = Field(
        ...,
        description="Greeting message",
        json_schema_extra={"example": "Hello, World!"},
    )


class HelloFormattedResponse(BaseResponse[HelloData]):
    """Formatted response for hello endpoint with unified format."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2026-01-22T15:00:00+03:00",
                "data": {
                    "message": "Hello, World!",
                },
            }
        }
    )

    data: HelloData = Field(
        ..., description="Response data with greeting message"
    )
