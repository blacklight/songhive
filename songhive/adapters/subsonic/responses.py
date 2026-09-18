"""
Subsonic response envelopes and serialization.

Every ``/rest`` endpoint answers with the same envelope::

    {"subsonic-response": {"status": "ok"|"failed", "version": ..., ...}}

rendered as XML (default), JSON (``f=json``) or JSONP (``f=jsonp`` with a
``callback`` parameter). The payload body is a plain dict tree that is
shared verbatim by the JSON encoder and translated to XML following the
Subsonic convention: scalar values become attributes, nested dicts and
lists become child elements.

Because the Tornado streaming handlers bypass FastAPI, the renderers here
are deliberately free of FastAPI types so both stacks can use them.
"""

import json
import re
from datetime import datetime
from typing import Any, Mapping, Optional
from xml.etree import ElementTree as ET

from ...version import __version__

#: Highest Subsonic API version this adapter understands.
API_VERSION = "1.16.1"

_ENVELOPE_TAG = "subsonic-response"
_XMLNS = "http://subsonic.org/restapi"

IGNORED_ARTICLES = "The El La Los Las Le Les A An As Es Os"


def iso(dt: Optional[datetime]) -> Optional[str]:
    """Format a datetime for Subsonic timestamp fields (``created``, ``starred``...)."""
    return dt.isoformat() if dt is not None else None


def envelope(payload: Mapping[str, Any], *, failed: bool = False) -> dict:
    """Wrap a payload dict in the subsonic-response envelope."""
    return {
        _ENVELOPE_TAG: {
            "status": "failed" if failed else "ok",
            "version": API_VERSION,
            "type": "songhive",
            "serverVersion": __version__,
            "openSubsonic": True,
            **payload,
        }
    }


def error_envelope(code: int, message: str) -> dict:
    """Return a failed envelope carrying a Subsonic error object."""
    return envelope({"error": {"code": code, "message": message}}, failed=True)


def request_format(params: Mapping[str, Any]) -> str:
    """Return the negotiated response format: ``xml`` (default), ``json`` or ``jsonp``."""
    fmt = str(params.get("f", "xml")).lower()
    return fmt if fmt in {"json", "jsonp", "xml"} else "xml"


#: A JSONP callback must be a plain JS identifier path (``foo``/``foo.bar``).
_CALLBACK_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)*$")


def _valid_callback(callback: Any) -> Optional[str]:
    """Return the callback name when it is a safe JS identifier path."""
    if isinstance(callback, str) and len(callback) <= 200 and _CALLBACK_RE.match(callback):
        return callback
    return None


def _scalar(value: Any) -> str:
    """Serialize a scalar attribute value for XML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_element(parent: ET.Element, tag: str, value: Any) -> None:
    """Append ``value`` under ``parent`` as one or more ``<tag>`` elements."""
    if isinstance(value, dict):
        element = ET.SubElement(parent, tag)
        for key, item in value.items():
            _apply_field(element, key, item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _build_element(parent, tag, item)
    elif value is not None:
        element = ET.SubElement(parent, tag)
        element.text = _scalar(value)


def _apply_field(element: ET.Element, key: str, value: Any) -> None:
    """Serialize a single dict field as an attribute or child elements."""
    if value is None:
        return
    if isinstance(value, (dict, list, tuple)):
        _build_element(element, key, value)
    else:
        element.set(key, _scalar(value))


def to_xml(data: Mapping[str, Any]) -> bytes:
    """Serialize a full envelope dict to Subsonic XML bytes."""
    root = ET.Element(_ENVELOPE_TAG, xmlns=_XMLNS)
    for key, value in data[_ENVELOPE_TAG].items():
        _apply_field(root, key, value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render(data: Mapping[str, Any], params: Mapping[str, Any]) -> tuple[bytes, str]:
    """
    Render an envelope dict to ``(body, content_type)`` per the ``f`` param.

    JSONP wraps the JSON payload in the ``callback`` parameter; when absent
    the request degrades to plain JSON.
    """
    fmt = request_format(params)
    if fmt == "xml":
        return to_xml(data), "text/xml; charset=utf-8"

    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if fmt == "jsonp":
        callback = _valid_callback(params.get("callback"))
        if callback is not None:
            return callback.encode("utf-8") + b"(" + body + b")", "text/javascript; charset=utf-8"
    return body, "application/json; charset=utf-8"
