"""Caching decorator for automatic API response caching.

Wraps connector methods to check cache before making API calls.
Cache keys are built from the method name and arguments.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
from typing import Any, Callable, TypeVar

import structlog

from src.cache.service import cache_get, cache_set

logger = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def cached(ttl_seconds: int = 300, key_prefix: str = "") -> Callable[[F], F]:
    """Decorator that caches the return value of a function.

    Works with both sync and async functions. Builds a cache key from the
    decorated function's qualified name and its positional/keyword arguments.

    Callers can pass ``bypass_cache=True`` as a keyword argument to skip
    the cache lookup and force a fresh call (the result is still cached).

    Args:
        ttl_seconds: Time-to-live for cached values in seconds.
        key_prefix: Optional prefix prepended to the auto-generated key.
            If empty, the function's qualified name is used.

    Returns:
        A decorator that wraps the target function with cache logic.

    Example::

        @cached(ttl_seconds=3600, key_prefix="google_ads")
        async def get_campaigns(self, customer_id: str) -> list[dict]:
            ...
    """

    def decorator(func: F) -> F:
        is_coroutine = asyncio.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            bypass = kwargs.pop("bypass_cache", False)

            cache_key = _build_key(func, key_prefix, args, kwargs)

            if not bypass:
                try:
                    hit = await cache_get(cache_key)
                    if hit is not None:
                        logger.debug(
                            "cache_decorator_hit",
                            func=func.__qualname__,
                            key=cache_key,
                        )
                        return hit
                except Exception:
                    logger.exception(
                        "cache_decorator_get_error",
                        func=func.__qualname__,
                    )

            # Cache miss or bypass -- call through to the real function
            result = await func(*args, **kwargs)

            # Cache the result (best-effort)
            if result is not None:
                try:
                    await cache_set(cache_key, result, ttl_seconds=ttl_seconds)
                except Exception:
                    logger.exception(
                        "cache_decorator_set_error",
                        func=func.__qualname__,
                    )

            return result

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            bypass = kwargs.pop("bypass_cache", False)

            cache_key = _build_key(func, key_prefix, args, kwargs)

            # For sync functions we run the async cache ops in an event loop.
            # If there is already a running loop we skip caching rather than
            # crashing the caller.
            loop: asyncio.AbstractEventLoop | None = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

            # If an event loop is already running we cannot use
            # ``asyncio.run`` -- just call through without caching.
            if loop is not None and loop.is_running():
                return func(*args, **kwargs)

            if not bypass:
                try:
                    hit = asyncio.run(_safe_cache_get(cache_key))
                    if hit is not None:
                        logger.debug(
                            "cache_decorator_hit",
                            func=func.__qualname__,
                            key=cache_key,
                        )
                        return hit
                except Exception:
                    logger.exception(
                        "cache_decorator_get_error",
                        func=func.__qualname__,
                    )

            result = func(*args, **kwargs)

            if result is not None:
                try:
                    asyncio.run(_safe_cache_set(cache_key, result, ttl_seconds))
                except Exception:
                    logger.exception(
                        "cache_decorator_set_error",
                        func=func.__qualname__,
                    )

            return result

        if is_coroutine:
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_key(
    func: Callable[..., Any],
    prefix: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """Create a deterministic cache key from function identity and arguments.

    The first positional argument is skipped if it looks like ``self``
    (i.e. when the function is a bound method on a class instance) to avoid
    including the object's id in the key.

    Args:
        func: The decorated callable.
        prefix: User-supplied key prefix (may be empty).
        args: Positional arguments passed to the function.
        kwargs: Keyword arguments passed to the function.

    Returns:
        A colon-separated cache key string.
    """
    func_name = prefix or func.__qualname__

    # Skip ``self`` for methods
    cache_args = args
    if cache_args and hasattr(cache_args[0], "__class__"):
        method_name = func.__name__
        if hasattr(cache_args[0].__class__, method_name):
            cache_args = cache_args[1:]

    # Build a stable hash of the arguments
    arg_parts: list[str] = []
    for a in cache_args:
        arg_parts.append(_safe_repr(a))
    for k in sorted(kwargs.keys()):
        arg_parts.append(f"{k}={_safe_repr(kwargs[k])}")

    arg_str = "|".join(arg_parts)
    arg_hash = hashlib.md5(arg_str.encode(), usedforsecurity=False).hexdigest()[:12]

    return f"sidera:cache:{func_name}:{arg_hash}"


def _safe_repr(value: Any) -> str:
    """Produce a stable string representation of a value for hashing.

    Falls back to ``str()`` for types that are not JSON-serializable.
    """
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


async def _safe_cache_get(key: str) -> dict | None:
    """Thin wrapper so ``asyncio.run`` has a coroutine to call."""
    return await cache_get(key)


async def _safe_cache_set(key: str, value: Any, ttl: int) -> bool:
    """Thin wrapper so ``asyncio.run`` has a coroutine to call."""
    return await cache_set(key, value, ttl_seconds=ttl)
