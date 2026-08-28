from sqlalchemy.exc import IntegrityError


class FileSizeLimitExceededError(ValueError):
    """Exception raised when an uploaded file exceeds the maximum allowed size."""

    def __init__(self, max_size: int, actual_size: int):
        self.max_size = max_size
        self.actual_size = actual_size
        super().__init__(f"File size {actual_size} exceeds the maximum allowed size of {max_size} bytes")


def is_unique_constraint_error(exc: IntegrityError) -> bool:
    """Return True when an IntegrityError is a uniqueness constraint violation."""
    cause = getattr(exc, "orig", None)
    message = str(cause) if cause is not None else str(exc)
    return "unique" in message.lower()
