"""
tests/test_cache.py
Tests the TTLCache utility.
"""
import time
from utils.cache import TTLCache


def test_set_and_get():
    c = TTLCache(default_ttl=10)
    c.set("k", "value")
    assert c.get("k") == "value"

def test_miss_returns_none():
    c = TTLCache()
    assert c.get("nonexistent") is None

def test_expired_returns_none():
    c = TTLCache(default_ttl=0.05)   # 50 ms TTL
    c.set("k", "v")
    time.sleep(0.1)
    assert c.get("k") is None

def test_delete():
    c = TTLCache()
    c.set("k", 42)
    c.delete("k")
    assert c.get("k") is None

def test_clear():
    c = TTLCache()
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert len(c) == 0

def test_max_size_evicts():
    c = TTLCache(max_size=3)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    c.set("d", 4)       # triggers eviction
    assert len(c) == 3
