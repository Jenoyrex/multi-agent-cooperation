"""Zero-cost guard. The project must cost $0, so by default nothing may build
a real provider SDK client (Anthropic, OpenAI) or send an agent request to a
non-loopback host. Both raise ZeroCostViolation before anything is sent.

The only way past it is ALLOW_PAID_PROVIDER_CALLS=1 in the environment, an
explicit decision to spend money that the $0 policy does not permit.

network_tripwire() also blocks, for one process, every non-loopback socket
connection and DNS lookup, whoever makes it (offline runs and tests).
"""
from __future__ import annotations

import os
import socket
from contextlib import contextmanager

ENV = "ALLOW_PAID_PROVIDER_CALLS"
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class ZeroCostViolation(RuntimeError):
    """Something tried to spend money or reach the network in zero-cost mode."""


def paid_calls_allowed() -> bool:
    return os.environ.get(ENV, "").strip() == "1"


def require_paid_calls(what: str) -> None:
    if not paid_calls_allowed():
        raise ZeroCostViolation(f"Zero-cost mode refused {what}: real provider calls cost money, and this "
                                f"project must cost $0 ({ENV} is not set to 1). Nothing was sent.")


def require_loopback(host, what: str) -> None:
    if host not in LOOPBACK and not paid_calls_allowed():
        raise ZeroCostViolation(f"Zero-cost mode refused {what} to non-loopback host {host!r}. Nothing was sent.")


@contextmanager
def network_tripwire():
    """Within the block, any non-loopback connect() or DNS lookup raises
    ZeroCostViolation (loopback stays allowed: asyncio uses a loopback socket
    pair on Windows). Yields the list of refused attempts."""
    blocked = []
    saved = {name: getattr(socket.socket, name) for name in ("connect", "connect_ex")}
    saved_getaddrinfo = socket.getaddrinfo

    def guarded(original):
        def connect(self, address):
            host = address[0] if isinstance(address, tuple) else address
            if isinstance(host, str) and host not in LOOPBACK and self.family in (socket.AF_INET, socket.AF_INET6):
                blocked.append(("connect", address))
                raise ZeroCostViolation(f"network tripwire: connection to {address!r} refused")
            return original(self, address)
        return connect

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode("idna") if isinstance(host, bytes) else host  # httpx passes bytes
        if isinstance(name, str):
            name.encode("idna")  # an invalid name fails here as in the real call, before any query
        if name is not None and name not in LOOPBACK:
            blocked.append(("getaddrinfo", name))
            raise ZeroCostViolation(f"network tripwire: DNS lookup of {name!r} refused")
        return saved_getaddrinfo(host, *args, **kwargs)

    for name, original in saved.items():
        setattr(socket.socket, name, guarded(original))
    socket.getaddrinfo = getaddrinfo
    try:
        yield blocked
    finally:
        for name, original in saved.items():
            setattr(socket.socket, name, original)
        socket.getaddrinfo = saved_getaddrinfo
