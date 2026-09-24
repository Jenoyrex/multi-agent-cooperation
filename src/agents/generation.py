"""Explicit generation settings and the shared API-call/retry wrapper used
by the real-model agents.

Nothing here has a provider default: model id, temperature, max output
tokens, API timeout and max retries must all be supplied (constructor or
environment variables), so a run can never silently inherit a provider's
defaults. No model ids or values are chosen in code.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Mapping, Optional, Tuple, TypeVar

from src.agents.schema import TransportError

T = TypeVar("T")


class ConfigError(ValueError):
    """Missing or invalid generation configuration."""


EFFORT_LEVELS = ("low", "medium", "high", "max")


@dataclass(frozen=True)
class GenerationConfig:
    model: str
    temperature: float
    max_output_tokens: int
    timeout_s: float
    max_retries: int  # retries AFTER the first attempt; 0 = single attempt
    retry_backoff_s: float = 1.0  # sleep before retry k is backoff * 2**(k-1)
    # Claude only: output_config.effort. None = not sent. OpenAIAgent rejects
    # any value (the experiment uses a non-reasoning GPT model).
    effort: Optional[str] = None

    def __post_init__(self):
        if self.effort is not None and self.effort not in EFFORT_LEVELS:
            raise ConfigError(f"effort must be one of {EFFORT_LEVELS} or None")
        if not self.model:
            raise ConfigError("model id must be a non-empty string")
        if self.temperature < 0:
            raise ConfigError("temperature must be >= 0")
        if self.max_output_tokens <= 0:
            raise ConfigError("max_output_tokens must be > 0")
        if self.timeout_s <= 0:
            raise ConfigError("timeout_s must be > 0")
        if self.max_retries < 0:
            raise ConfigError("max_retries must be >= 0")
        if self.retry_backoff_s < 0:
            raise ConfigError("retry_backoff_s must be >= 0")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_env(cls, prefix: str, env: Optional[Mapping[str, str]] = None) -> "GenerationConfig":
        """Read <PREFIX>_MODEL, _TEMPERATURE, _MAX_OUTPUT_TOKENS, _TIMEOUT_S,
        _MAX_RETRIES (and optional _RETRY_BACKOFF_S, _EFFORT). All five
        required."""
        env = os.environ if env is None else env
        names = ["MODEL", "TEMPERATURE", "MAX_OUTPUT_TOKENS", "TIMEOUT_S", "MAX_RETRIES"]
        missing = [f"{prefix}_{n}" for n in names if not str(env.get(f"{prefix}_{n}", "")).strip()]
        if missing:
            raise ConfigError(f"Missing required generation settings: {', '.join(missing)}")
        try:
            return cls(
                model=env[f"{prefix}_MODEL"].strip(),
                temperature=float(env[f"{prefix}_TEMPERATURE"]),
                max_output_tokens=int(env[f"{prefix}_MAX_OUTPUT_TOKENS"]),
                timeout_s=float(env[f"{prefix}_TIMEOUT_S"]),
                max_retries=int(env[f"{prefix}_MAX_RETRIES"]),
                retry_backoff_s=float(env.get(f"{prefix}_RETRY_BACKOFF_S", 1.0)),
                effort=str(env.get(f"{prefix}_EFFORT", "")).strip() or None,
            )
        except ValueError as exc:
            raise ConfigError(f"Invalid generation setting for {prefix}: {exc}") from exc


def _status_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _is_retryable(exc: BaseException) -> bool:
    code = _status_code(exc)
    if code is not None:
        return code in (408, 409, 429) or code >= 500
    return _is_connection_like(exc)


def _is_connection_like(exc: BaseException) -> bool:
    name = type(exc).__name__
    return (
        isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError))
        or "Connection" in name
        or "Timeout" in name
    )


async def call_with_retries(
    fn: Callable[[], Awaitable[T]], cfg: GenerationConfig
) -> Tuple[T, int, float]:
    """Await fn() with up to cfg.max_retries retries. Returns
    (result, attempts, total_latency_s).

    Only API-level failures become TransportError: status 408/409/429/5xx
    and connection/timeout errors are retried; other API errors (e.g. 400,
    401) fail immediately. Anything else (a bug, e.g. TypeError) propagates
    unchanged rather than being disguised as an infrastructure failure.
    Every retry is a fresh model call; discarded attempts' tokens are not
    observable."""
    start = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            result = await fn()
            return result, attempts, time.monotonic() - start
        except Exception as exc:  # noqa: BLE001 — classified below
            if _status_code(exc) is None and not _is_connection_like(exc):
                raise
            if attempts > cfg.max_retries or not _is_retryable(exc):
                raise TransportError(
                    f"{type(exc).__name__}: {exc}",
                    attempts=attempts,
                    latency_s=time.monotonic() - start,
                ) from exc
            await asyncio.sleep(cfg.retry_backoff_s * 2 ** (attempts - 1))
