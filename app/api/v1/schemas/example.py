from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.responses import BaseResponse


class ExampleItemData(BaseModel):
    """Response data for example endpoint."""

    id: int = Field(..., description="Item identifier")
    name: str = Field(..., description="Item name")
    status: str = Field(..., description="Item status")


class ExampleItemResponse(BaseResponse[ExampleItemData]):
    """Formatted response for example endpoint with unified format."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2026-01-22T15:00:00+03:00",
                "data": {
                    "id": 1,
                    "name": "Example Item 1",
                    "status": "active",
                },
            }
        }
    )

    data: ExampleItemData = Field(..., description="Item data")
