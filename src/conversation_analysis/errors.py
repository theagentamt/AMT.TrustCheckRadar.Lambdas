HTTP_STATUS_BY_CODE = {
    "INVALID_REQUEST": 400,
    "UNSUPPORTED_SCHEMA_VERSION": 400,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "DEVICE_BINDING_REQUIRED": 403,
    "DEVICE_BINDING_MISMATCH": 403,
    "ENTITLEMENT_EXHAUSTED": 403,
    "RATE_LIMITED": 429,
    "SERVER_UNAVAILABLE": 500,
    "INTERNAL_ERROR": 500,
    "ANALYSIS_TIMEOUT": 504,
}


class AppError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool, details: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or []

    @property
    def status_code(self) -> int:
        return HTTP_STATUS_BY_CODE.get(self.code, 500)
