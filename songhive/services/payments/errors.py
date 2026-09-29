"""Payment service errors."""

from typing import Optional


class PaymentError(ValueError):
    """A payment operation could not be completed."""

    def __init__(self, message: str, *, status_code: int = 400, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code or "payment_error"
