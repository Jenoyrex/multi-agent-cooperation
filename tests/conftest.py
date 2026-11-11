import http.server
import socketserver
from collections import deque

import pytest

from dashboard import data, demo
from src import zero_cost


def _bind_without_reverse_dns(self):
    """The tests' own stdlib servers skip socket.getfqdn too (slow on macOS runners); dashboard.server.LabServer does it itself."""
    socketserver.TCPServer.server_bind(self)
    self.server_name, self.server_port = str(self.server_address[0]), self.server_address[1]


http.server.HTTPServer.server_bind = _bind_without_reverse_dns


@pytest.fixture(autouse=True)
def _public_demo_off(monkeypatch):
    """Tests never inherit PUBLIC_DEMO or ALLOW_PAID_PROVIDER_CALLS from the shell (a test that needs it turns it on itself),
    and no test sees another's demo start count or cached provenance."""
    monkeypatch.delenv(demo.ENV, raising=False)
    monkeypatch.delenv(zero_cost.ENV, raising=False)  # zero-cost mode is always on in tests
    monkeypatch.setattr(demo, "_starts", deque())
    monkeypatch.setattr(data, "_provenance_cache", (0.0, None))
