HTTP_STATUS_BY_CODE = {
    "INVALID_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "REAUTHENTICATION_REQUIRED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "REQUEST_IN_PROGRESS": 409,
    "RATE_LIMITED": 429,
    "FEATURE_DISABLED": 503,
    "SERVER_UNAVAILABLE": 503,
    "INTERNAL_ERROR": 500,
}


class AppError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, details: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or []

    @property
    def status_code(self) -> int:
        return HTTP_STATUS_BY_CODE.get(self.code, 500)
