"""
Subsonic API error codes and the ``SubsonicError`` exception.

Error codes follow the canonical Subsonic scheme documented at
http://www.subsonic.org/pages/api.jsp — clients branch on ``code`` and may
surface ``message`` verbatim, so messages must not leak internals.
"""

GENERIC_ERROR = 0
MISSING_PARAMETER = 10
CLIENT_PROTOCOL_TOO_OLD = 20
SERVER_PROTOCOL_TOO_OLD = 30
WRONG_CREDENTIALS = 40
TOKEN_AUTH_UNSUPPORTED = 41
NOT_AUTHORIZED = 50
TRIAL_EXPIRED = 60
NOT_FOUND = 70


class SubsonicError(Exception):
    """
    Raised inside Subsonic endpoints to abort with a protocol-level error.

    The adapter's exception handler renders this as a ``failed`` subsonic
    response envelope with HTTP 200, per the Subsonic API contract.
    """

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def missing_parameter(name: str) -> SubsonicError:
    """Return the standard error for a required-but-absent parameter."""
    return SubsonicError(MISSING_PARAMETER, f"Required parameter is missing: {name}")
