"""Service-layer errors, mapped to HTTP by the API layer."""

from __future__ import annotations


class ServiceError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.code
        if code:
            self.code = code


class NotFound(ServiceError):
    status = 404
    code = "not_found"


class Forbidden(ServiceError):
    status = 403
    code = "forbidden"


class Unauthorized(ServiceError):
    status = 401
    code = "unauthorized"


class PreconditionFailed(ServiceError):
    status = 412
    code = "disclaimer_required"


class PreconditionRequired(ServiceError):
    status = 428
    code = "authorization_pending"


class Conflict(ServiceError):
    status = 409
    code = "conflict"


class Gone(ServiceError):
    status = 410
    code = "expired"


class BadGateway(ServiceError):
    status = 502
    code = "upstream_error"
