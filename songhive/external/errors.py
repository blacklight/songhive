"""
Typed exceptions for external library adapters.
"""

from typing import Optional


class ExternalLibraryError(Exception):
    """Base exception for external library adapters."""


class ExternalConfigError(ExternalLibraryError):
    """The adapter configuration is invalid or incomplete."""

    def __init__(self, message: str = "", field: Optional[str] = None) -> None:
        self.field = field
        super().__init__(message)


class UnsupportedExternalOperation(ExternalLibraryError):
    """The requested operation is not implemented by this adapter."""

    def __init__(self, message: str = "", operation: Optional[str] = None) -> None:
        self.operation = operation
        super().__init__(message)


class ExternalItemNotFound(ExternalLibraryError):
    """The requested provider item could not be found."""

    def __init__(self, message: str = "", provider_key: Optional[str] = None) -> None:
        self.provider_key = provider_key
        super().__init__(message)


class ExternalPermissionDenied(ExternalLibraryError):
    """The adapter refused to perform the operation."""

    def __init__(self, message: str = "", operation: Optional[str] = None) -> None:
        self.operation = operation
        super().__init__(message)


class ExternalWriteBackError(ExternalLibraryError):
    """Writing metadata back to the provider failed."""

    def __init__(self, message: str = "", provider_key: Optional[str] = None) -> None:
        self.provider_key = provider_key
        super().__init__(message)
