from __future__ import annotations

from store import encode


class Cache:
    """An unrelated class whose `get` shares a name with Store.get."""

    def __init__(self) -> None:
        self.hits: dict[int, str] = {}

    def get(self, key: int) -> str:
        return self.hits[key]


class Service:
    def __init__(self, store) -> None:
        self.store = store

    def load(self, key: str) -> str:
        # The receiver's type is never written down: `store` is whatever was
        # passed in. A parser that guesses here will guess wrong eventually.
        return self.store.get(key)

    def save(self, key: str, value) -> None:
        self.store.put(key, encode(value))

    def warm(self, cache: Cache, key: int) -> str:
        # Annotated, so the target is knowable from syntax alone.
        return cache.get(key)

    def helper(self) -> str:
        # A call on `self` whose target is in this class.
        return self._inner()

    def _inner(self) -> str:
        return "inner"
