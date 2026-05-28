"""
utils/retry.py — Enterprise Resilience Layer
RepoAlpha

Wraps all external API calls with:
  - Exponential backoff retry (tenacity)
  - Circuit breaker pattern (manual token-bucket style)
  - Per-service rate limit budgeting

Zero cost: tenacity is a pure Python library.

Why this matters for enterprise grade:
  Without retry logic, a single Bright Data timeout at 2AM kills
  the entire nightly pipeline. With this, temporary failures are
  healed automatically and the circuit opens only on sustained outages.
"""

import time
import functools
import threading
from datetime import datetime, timedelta
from typing import Callable, Any, TypeVar
from loguru import logger

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    RetryError,
)
import requests
import logging

F = TypeVar("F", bound=Callable[..., Any])


# ─── Tenacity Retry Decorators ───────────────────────────────────────────────

def retry_on_network(max_attempts: int = 4, min_wait: float = 2.0, max_wait: float = 30.0):
    """
    Decorator for any function that makes HTTP calls.
    Retries on transient errors with exponential backoff.

    Usage:
        @retry_on_network(max_attempts=4)
        def fetch_profile(url):
            return requests.get(url, timeout=15)
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type((
            requests.ConnectionError,
            requests.Timeout,
            requests.HTTPError,
            ConnectionResetError,
        )),
        before_sleep=before_sleep_log(
            logging.getLogger("tenacity"), logging.WARNING
        ),
        reraise=True,
    )


def retry_on_rate_limit(max_attempts: int = 6, base_wait: float = 60.0):
    """
    Decorator for GitHub API calls where 429 / 403 means rate limited.
    Waits longer: 60s, 120s, 240s...
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=base_wait, min=base_wait, max=300),
        retry=retry_if_exception_type((
            requests.HTTPError,
            requests.Timeout,
        )),
        reraise=True,
    )


# ─── Circuit Breaker ─────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Classic three-state circuit breaker:
      CLOSED   → normal operation
      OPEN     → fast-fail for `reset_timeout` seconds
      HALF_OPEN → try one request; if it succeeds, close; if it fails, reopen

    Prevents hammering a degraded API when it's clearly down,
    e.g. Bright Data maintenance window.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 120.0,  # seconds
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout

        self._failures = 0
        self._state = "CLOSED"   # CLOSED | OPEN | HALF_OPEN
        self._opened_at: datetime | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._state == "OPEN":
                if datetime.utcnow() - self._opened_at > timedelta(seconds=self.reset_timeout):
                    self._state = "HALF_OPEN"
                    logger.warning(f"CircuitBreaker [{self.name}] → HALF_OPEN")
                    return False
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                logger.info(f"CircuitBreaker [{self.name}] → CLOSED (recovered)")

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            logger.warning(f"CircuitBreaker [{self.name}] failure {self._failures}/{self.failure_threshold}")
            if self._failures >= self.failure_threshold:
                self._state = "OPEN"
                self._opened_at = datetime.utcnow()
                logger.error(
                    f"CircuitBreaker [{self.name}] → OPEN "
                    f"(will retry after {self.reset_timeout}s)"
                )

    def __call__(self, func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if self.is_open:
                raise RuntimeError(
                    f"CircuitBreaker [{self.name}] is OPEN — skipping call to {func.__name__}"
                )
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as exc:
                self.record_failure()
                raise
        return wrapper  # type: ignore


# ─── Rate Limiter (Token Bucket) ─────────────────────────────────────────────

class RateLimiter:
    """
    Token-bucket rate limiter for respecting free-tier API budgets.

    Example — Groq llama3-70b free tier: 500 req/day max.
    Set calls_per_minute=0.35 (500/24/60 ≈ 0.35/min) and
    the limiter will sleep if you're going too fast.
    """

    def __init__(self, calls_per_minute: float, name: str = "api"):
        self.name = name
        self.min_interval = 60.0 / calls_per_minute  # seconds between calls
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            sleep_for = self.min_interval - elapsed
            if sleep_for > 0:
                logger.debug(f"RateLimiter [{self.name}] sleeping {sleep_for:.2f}s")
                time.sleep(sleep_for)
            self._last_call = time.monotonic()

    def __call__(self, func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            self.wait()
            return func(*args, **kwargs)
        return wrapper  # type: ignore


# ─── Pre-configured instances ────────────────────────────────────────────────

# Groq free tier — llama3-70b: ~8 req/min safe budget (500/day / 24h / 60min)
groq_70b_limiter = RateLimiter(calls_per_minute=8, name="groq-70b")

# Groq free tier — llama3-8b: much more headroom
groq_8b_limiter = RateLimiter(calls_per_minute=40, name="groq-8b")

# Bright Data — conservative pacing to stay within $250 credit
brightdata_limiter = RateLimiter(calls_per_minute=20, name="brightdata")

# GitHub public API without PAT: 1 req/sec
github_limiter = RateLimiter(calls_per_minute=50, name="github")

# Circuit breakers per external service
brightdata_breaker = CircuitBreaker("brightdata", failure_threshold=5, reset_timeout=120)
groq_breaker = CircuitBreaker("groq", failure_threshold=3, reset_timeout=60)
github_breaker = CircuitBreaker("github", failure_threshold=8, reset_timeout=300)
