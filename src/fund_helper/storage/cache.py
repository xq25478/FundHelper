from __future__ import annotations

import functools
import hashlib
import pickle
import time
from pathlib import Path
from typing import Any, Callable


def _key(args: tuple, kwargs: dict[str, Any]) -> str:
    raw = pickle.dumps((args, sorted(kwargs.items())))
    return hashlib.sha1(raw).hexdigest()


def disk_cache(root: str | Path, ttl_seconds: int = 3600) -> Callable:
    """Lightweight pickle disk cache. Replace with parquet/sqlite for hot paths."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def deco(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            path = root / f"{func.__qualname__}.{_key(args, kwargs)}.pkl"
            if path.exists() and time.time() - path.stat().st_mtime < ttl_seconds:
                with path.open("rb") as f:
                    return pickle.load(f)
            res = func(*args, **kwargs)
            with path.open("wb") as f:
                pickle.dump(res, f)
            return res
        return wrapper
    return deco
