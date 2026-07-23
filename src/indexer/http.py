"""httpx wrapper: timeouts, bounded exponential backoff, typed error classes.

Never logs request headers (auth material lives in headers for PMData).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

log = logging.getLogger(__name__)


class FetchError(Exception):
    """Base class for fetch failures."""


class UnavailableError(FetchError):
    """HTTP 404: the object does not exist at the source (terminal)."""


class TransientError(FetchError):
    """Retryable failure that persisted past the retry budget."""


class PermanentError(FetchError):
    """Non-retryable HTTP failure (other 4xx)."""


class MalformedError(Exception):
    """Source bytes/JSON did not match the expected shape (parse-level)."""


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers or {},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.get(path)
            except httpx.HTTPError as exc:
                if attempt > self._max_retries:
                    raise TransientError(f"network error on {path}: {exc}") from exc
                self._backoff(attempt, path, f"network error: {type(exc).__name__}")
                continue
            status = response.status_code
            if status == 404:
                raise UnavailableError(f"404 not found: {path}")
            if status == 429 or status >= 500:
                if attempt > self._max_retries:
                    raise TransientError(f"HTTP {status} persisted for {path}")
                self._backoff(attempt, path, f"HTTP {status}")
                continue
            if status >= 400:
                raise PermanentError(f"HTTP {status} for {path}")
            return response

    def _backoff(self, attempt: int, path: str, reason: str) -> None:
        delay = min(self._backoff_base * (2 ** (attempt - 1)), self._backoff_max)
        log.warning(
            "retrying request",
            extra={"path": path, "attempt": attempt, "delay_s": delay, "reason": reason},
        )
        self._sleep(delay)

    def get_bytes(self, path: str) -> bytes:
        return self._get(path).content

    def get_json(self, path: str):
        return self._get(path).json()
