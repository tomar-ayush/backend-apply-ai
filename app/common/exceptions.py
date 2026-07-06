class AppException(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class NotFoundError(AppException):
    def __init__(self, resource: str, resource_id: str = ""):
        detail = f"{resource} not found"
        if resource_id:
            detail = f"{resource} '{resource_id}' not found"
        super().__init__(status_code=404, detail=detail)


class ConflictError(AppException):
    def __init__(self, detail: str):
        super().__init__(status_code=409, detail=detail)


class ValidationError(AppException):
    def __init__(self, detail: str):
        super().__init__(status_code=422, detail=detail)


class InvalidTransitionError(AppException):
    def __init__(self, from_status: str, to_status: str):
        super().__init__(
            status_code=400,
            detail=f"Invalid status transition: {from_status} -> {to_status}",
        )


class UnauthorizedError(AppException):
    def __init__(self, detail: str = "Authentication required"):
        super().__init__(status_code=401, detail=detail)


class ForbiddenError(AppException):
    def __init__(self, detail: str = "Access denied"):
        super().__init__(status_code=403, detail=detail)


class BadRequestError(AppException):
    def __init__(self, detail: str):
        super().__init__(status_code=400, detail=detail)


class ExternalServiceError(AppException):
    def __init__(self, service: str, detail: str):
        super().__init__(status_code=502, detail=f"{service}: {detail}")
