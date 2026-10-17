from __future__ import annotations

import functools

from store import encode


def retry(fn):
    """A decorator that returns a wrapper — the wrapped call is inside it."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@retry
def fetch(key: str) -> str:
    """Decorated: callers name `fetch`, but reach it through `wrapper`."""
    return encode({"key": key})


def via_getattr(obj, name: str):
    """The method name is a value. Nothing static can say what this calls."""
    return getattr(obj, name)()


def via_local_alias():
    """A function bound to a local name first."""
    f = encode
    return f({"a": 1})


def direct() -> str:
    """The plain case, for contrast."""
    return encode({"b": 2})
