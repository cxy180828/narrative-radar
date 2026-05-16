"""
In-memory cache with TTL support.
"""

import time
import threading
from typing import Any, Optional


class TTLCache:
    """Thread-safe in-memory cache with TTL eviction."""

    def __init__(self, default_ttl: int = 1800, max_size: int = 5000):
        self._store = {}
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache. Returns None if expired or missing."""
        with self._lock:
            item = self._store.get(key)
            if item is None:
                self._misses += 1
                return None
            ts, value = item
            if time.time() - ts > self._default_ttl:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: int = None) -> Any:
        """Set value in cache. Returns the value for chaining."""
        with self._lock:
            if len(self._store) >= self._max_size:
                self._evict_oldest(self._max_size // 4)
            self._store[key] = (time.time(), value)
        return value

    def delete(self, key: str):
        """Remove a key from cache."""
        with self._lock:
            self._store.pop(key, None)

    def has(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return self.get(key) is not None

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._store.clear()

    def _evict_oldest(self, count: int):
        """Evict the N oldest entries."""
        if not self._store:
            return
        sorted_keys = sorted(self._store.keys(), key=lambda k: self._store[k][0])
        for key in sorted_keys[:count]:
            del self._store[key]

    def cleanup_expired(self):
        """Remove all expired entries."""
        now = time.time()
        with self._lock:
            expired = [k for k, (ts, _) in self._store.items() if now - ts > self._default_ttl]
            for k in expired:
                del self._store[k]
        return len(expired)

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (self._hits / max(total, 1)) * 100,
        }

    @property
    def size(self) -> int:
        return len(self._store)
