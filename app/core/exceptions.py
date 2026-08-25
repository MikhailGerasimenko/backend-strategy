from fastapi import HTTPException, status


class BaseAPIException(HTTPException):
    """Base exception for API."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        headers: dict | None = None,
    ):
        super().__init__(
            status_code=status_code, detail=detail, headers=headers
        )


class NotFoundError(BaseAPIException):
    """Exception for resources that are not found."""

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ValidationError(BaseAPIException):
    """Exception for validation errors."""

    def __init__(self, detail: str = "Validation error"):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
        )


class InternalServerError(BaseAPIException):
    """Exception for internal server errors."""

    def __init__(self, detail: str = "Internal server error"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
        )
