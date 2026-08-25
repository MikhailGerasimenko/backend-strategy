from fastapi import APIRouter, Request

from app.api.v1.schemas.example import ExampleItemData, ExampleItemResponse
from app.core.exceptions import NotFoundError
from app.core.utils import get_current_timestamp

router = APIRouter()


@router.get("/example/{item_id}", response_model=ExampleItemResponse)
async def get_example_item(item_id: int, request: Request):
    """
    Example endpoint with unified response format and error handling.

    Demonstrates:
    - Using unified response format
    - Raising custom exceptions
    - Automatic error handling through exception handlers
    """
    if item_id < 1:
        raise NotFoundError(f"Item with id {item_id} not found")

    data = ExampleItemData(
        id=item_id,
        name=f"Example Item {item_id}",
        status="active",
    )

    return ExampleItemResponse(
        request_id=request.state.request_id,
        timestamp=get_current_timestamp(),
        data=data,
    )
