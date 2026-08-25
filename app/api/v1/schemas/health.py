from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "timestamp": "2026-01-22T12:00:00.000000",
                "service": "FastAPI Template",
            }
        }
    )

    status: str = Field(..., description="Service health status")
    timestamp: str = Field(..., description="Current timestamp in ISO format")
    service: str = Field(..., description="Service name")
