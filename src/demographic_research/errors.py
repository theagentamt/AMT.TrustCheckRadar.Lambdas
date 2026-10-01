HTTP_STATUS = {
    "INVALID_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "AGE_VERIFICATION_REQUIRED": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "ACCOUNT_DELETION_IN_PROGRESS": 409,
    "FEATURE_DISABLED": 503,
    "ENROLLMENT_UNAVAILABLE": 503,
    "SERVER_UNAVAILABLE": 503,
}


class AppError(Exception):
    def __init__(self, code, message, *, retryable=False, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or []

    @property
    def status_code(self):
        return HTTP_STATUS.get(self.code, 500)
