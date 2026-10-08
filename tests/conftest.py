from collections import deque

import pytest

from dashboard import data, demo


@pytest.fixture(autouse=True)
def _public_demo_off(monkeypatch):
    """Tests never inherit PUBLIC_DEMO from the shell (a test that needs it turns it on itself),
    and no test sees another's demo start count or cached provenance."""
    monkeypatch.delenv(demo.ENV, raising=False)
    monkeypatch.setattr(demo, "_starts", deque())
    monkeypatch.setattr(data, "_provenance_cache", (0.0, None))
