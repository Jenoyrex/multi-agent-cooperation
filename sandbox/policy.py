"""Every sandbox gate in one place: the public demo, requests from this computer, and URL rules.

Local means: not PUBLIC_DEMO, and (when serving HTTP) the client is this computer talking to its local Host.
The server records that per request; direct Python callers (tests, scripts) are on this computer already.
"""
from __future__ import annotations

import threading
from urllib.parse import urlparse

from dashboard import demo
from src.zero_cost import LOOPBACK
from workbench.agents import WorkbenchError

DEMO_OFF = ("This is the public demo: rule-based agents only, with no keys, no model calls and no code execution. "
            "Download the app and run it on your computer to use this.")
NOT_LOCAL = "This works only from the computer running the app (http://127.0.0.1)."
_request = threading.local()


def set_request_local(is_local: bool) -> None:
    _request.local = is_local


def request_is_local() -> bool:
    return getattr(_request, "local", True)


def require_local(what: str = "") -> None:
    """Raises unless this is the full local app, asked from this computer. Called at every entry and again just before use."""
    if demo.enabled():
        raise WorkbenchError(DEMO_OFF, status=403)
    if not request_is_local():
        raise WorkbenchError(NOT_LOCAL if not what else f"{what}: {NOT_LOCAL}", status=403)


def is_loopback(url: str) -> bool:
    try:
        return urlparse(url).hostname in LOOPBACK
    except ValueError:
        return False


def clean_base_url(url, local_only: bool) -> str:
    """A provider base URL, or WorkbenchError. A key may only travel over https, or to this computer."""
    url = str(url or "").strip().rstrip("/")
    try:
        u = urlparse(url)
        u.port  # raises on a malformed port
    except ValueError:
        raise WorkbenchError("That base URL is not valid.", "base_url") from None
    if len(url) > 300 or u.scheme not in ("http", "https") or not u.hostname:
        raise WorkbenchError("Use a full URL, such as http://localhost:11434/v1.", "base_url")
    if u.username or u.password or u.query or u.fragment:
        raise WorkbenchError("Don't put credentials or a query in the URL. Add the API key in the key field; it is never saved.", "base_url")
    loop = u.hostname in LOOPBACK
    if local_only and not loop:
        raise WorkbenchError("A local model server must be on this computer (localhost or 127.0.0.1).", "base_url")
    if not loop and u.scheme != "https":
        raise WorkbenchError("An API key is only sent over https:// (or to this computer).", "base_url")
    return url
