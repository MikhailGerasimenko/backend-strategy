from fastapi import APIRouter, Request

from app.api.v1.schemas.hello import (
    HelloData,
    HelloFormattedResponse,
    HelloResponse,
)
from app.core.utils import get_current_timestamp

router = APIRouter()


@router.get("/", response_model=HelloResponse)
async def hello_world():
    """
    Hello world endpoint (simple response format).
    """
    return HelloResponse(message="Hello, World!")


@router.get("/hello/{name}", response_model=HelloResponse)
async def hello_name(name: str):
    """
    Personalized hello endpoint (simple response format).
    """
    return HelloResponse(message=f"Hello, {name}!")


@router.get("/hello-formatted/{name}", response_model=HelloFormattedResponse)
async def hello_name_formatted(name: str, request: Request):
    """
    Personalized hello endpoint with unified response format.

    Example of using unified response format with request_id and timestamp.
    """
    data = HelloData(message=f"Hello, {name}!")
    return HelloFormattedResponse(
        request_id=request.state.request_id,
        timestamp=get_current_timestamp(),
        data=data,
    )
