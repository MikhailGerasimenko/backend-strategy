from fastapi import APIRouter

from app.api.v1.schemas.health import HealthResponse
from app.core.utils import get_current_timestamp

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint to verify the service is running.
    """
    return HealthResponse(
        status="healthy",
        timestamp=get_current_timestamp(),
        service="Strategic Navigator",
    )
