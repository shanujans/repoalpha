"""
utils/cache.py — Zero-cost In-process Cache
RepoAlpha

Supabase free tier has no built-in caching. Rather than paying for Redis,
we use a simple TTL dict cache that lives in the Python process.

For the harvester/enricher running as a single process (GitHub Actions or
a Render worker), this eliminates repeated Supabase reads within one run —
e.g. the enricher reads the repo list once and caches it for the session.

For the Streamlit dashboard, Streamlit's own @st.cache_data handles caching,
so this module is used only by the background agents.
"""

import time
import threading
from typing import Any, Optional
from loguru import logger


class TTLCache:
    """
    Thread-safe TTL cache backed by a plain dict.
    Entries expire after `default_ttl` seconds.

    Usage:
        cache = TTLCache(default_ttl=300)
        cache.set("repos:all", data)
        data = cache.get("repos:all")   # None if expired
    """

    def __init__(self, default_ttl: float = 300.0, max_size: int = 512):
        self._store: dict[str, tuple[Any, float]] = {}   # key → (value, expiry)
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        with self._lock:
            # Evict oldest entries when at capacity
            if len(self._store) >= self._max_size:
                oldest = min(self._store, key=lambda k: self._store[k][1])
                del self._store[oldest]
                logger.debug(f"TTLCache evicted: {oldest}")
            expiry = time.monotonic() + (ttl or self._default_ttl)
            self._store[key] = (value, expiry)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ── Singleton instances used across agents ────────────────────────────────────

# Short TTL — used for hot paths like "is this repo already processed?"
short_cache = TTLCache(default_ttl=60, max_size=1000)

# Medium TTL — used for repo lists and signal aggregates
repo_cache = TTLCache(default_ttl=300, max_size=256)


def cached(cache: TTLCache, key: str, ttl: Optional[float] = None):
    """
    Decorator that caches a function's return value.

    Usage:
        @cached(repo_cache, "repos:buy")
        def get_buy_repos():
            return supabase.table("repositories").select("*").eq("rating","BUY").execute().data
    """
    def decorator(fn):
        def wrapper(*args, **kwargs):
            hit = cache.get(key)
            if hit is not None:
                logger.debug(f"Cache HIT: {key}")
                return hit
            result = fn(*args, **kwargs)
            cache.set(key, result, ttl)
            logger.debug(f"Cache SET: {key}")
            return result
        return wrapper
    return decorator
