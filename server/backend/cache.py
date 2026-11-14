"""Cache L1 en Redis (con respaldo en memoria si Redis no responde) + empaquetado de matrices."""
from __future__ import annotations

import os
import struct
import threading
import zlib
from collections import OrderedDict

import numpy as np

try:
    import blosc  # type: ignore
except Exception:  # pragma: no cover
    blosc = None

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None

REDIS_URL = os.environ.get("SPHEREX_REDIS_URL", "redis://127.0.0.1:6379/0")


def pack(arr: np.ndarray) -> bytes:
    a = np.ascontiguousarray(arr, dtype=np.float32)
    raw = a.tobytes()
    if blosc is not None:
        body, codec = blosc.compress(raw, typesize=4, cname="lz4", clevel=5), b"B"
    else:
        body, codec = zlib.compress(raw, 1), b"Z"
    return codec + struct.pack("<II", a.shape[0], a.shape[1]) + body


def unpack(data: bytes) -> np.ndarray:
    codec, (ny, nx) = data[:1], struct.unpack("<II", data[1:9])
    raw = blosc.decompress(data[9:]) if codec == b"B" else zlib.decompress(data[9:])
    return np.frombuffer(raw, dtype=np.float32).reshape(ny, nx)


class Cache:
    """get/set de bytes. Nunca lanza: si Redis falla se usa un LRU local pequeno."""

    def __init__(self, local_items: int = 256):
        self._local: OrderedDict[str, bytes] = OrderedDict()
        self._max = local_items
        self._lock = threading.Lock()
        self._r = None
        if redis is not None:
            try:
                self._r = redis.Redis.from_url(REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
                self._r.ping()
            except Exception:
                self._r = None

    @property
    def redis_ok(self) -> bool:
        return self._r is not None

    def get(self, key: str) -> bytes | None:
        if self._r is not None:
            try:
                return self._r.get(key)
            except Exception:
                pass
        with self._lock:
            v = self._local.get(key)
            if v is not None:
                self._local.move_to_end(key)
            return v

    def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        if self._r is not None:
            try:
                self._r.set(key, value, ex=ttl)
                return
            except Exception:
                pass
        with self._lock:
            self._local[key] = value
            self._local.move_to_end(key)
            while len(self._local) > self._max:
                self._local.popitem(last=False)

    def info(self) -> dict:
        if self._r is None:
            return {"backend": "memoria-local", "items": len(self._local)}
        try:
            i = self._r.info()
        except Exception as e:
            return {"backend": "redis", "error": str(e)}
        hits, miss = i.get("keyspace_hits", 0), i.get("keyspace_misses", 0)
        return {
            "backend": "redis",
            "used_memory_bytes": i.get("used_memory"),
            "used_memory_human": i.get("used_memory_human"),
            "maxmemory_bytes": i.get("maxmemory"),
            "maxmemory_policy": i.get("maxmemory_policy"),
            "keys": self._r.dbsize(),
            "keyspace_hits": hits,
            "keyspace_misses": miss,
            "hit_rate": round(hits / (hits + miss), 4) if hits + miss else None,
            "ops_per_sec": i.get("instantaneous_ops_per_sec"),
            "evicted_keys": i.get("evicted_keys"),
            "uptime_seconds": i.get("uptime_in_seconds"),
        }
