from sqlalchemy.exc import IntegrityError


class FileSizeLimitExceededError(ValueError):
    """Exception raised when an uploaded file exceeds the maximum allowed size."""

    def __init__(self, max_size: int, actual_size: int):
        self.max_size = max_size
        self.actual_size = actual_size
        super().__init__(f"File size {actual_size} exceeds the maximum allowed size of {max_size} bytes")


class QuotaExceededError(ValueError):
    """Exception raised when an upload would exceed the owner's storage quota."""

    def __init__(self, quota: int, used: int, incoming: int):
        self.quota = quota
        self.used = used
        self.incoming = incoming
        super().__init__(f"Upload of {incoming} bytes exceeds the upload quota ({used}/{quota} bytes used)")


def is_unique_constraint_error(exc: IntegrityError) -> bool:
    """Return True when an IntegrityError is a uniqueness constraint violation."""
    cause = getattr(exc, "orig", None)
    message = str(cause) if cause is not None else str(exc)
    return "unique" in message.lower()
