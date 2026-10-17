"""An accuracy fixture, not a program worth running.

Python settles even less at parse time than Go does: there are no declared
types on most call sites, a method can be replaced at runtime, and a decorator
can return something other than what it wrapped. Each construct here is one a
real Python codebase uses.
"""

from __future__ import annotations

import json
from typing import Protocol


class Store(Protocol):
    """A structural interface — nothing declares that it implements this."""

    def get(self, key: str) -> str: ...

    def put(self, key: str, value: str) -> None: ...


class MemStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str:
        return self.data[key]

    def put(self, key: str, value: str) -> None:
        self.data[key] = value


class AuditStore:
    """A decorator holding the thing it decorates, with no declared type."""

    def __init__(self, inner):
        self.inner = inner
        self.log: list[str] = []

    def get(self, key: str) -> str:
        self._note("get")
        return self.inner.get(key)

    def put(self, key: str, value: str) -> None:
        self._note("put")
        self.inner.put(key, value)

    def _note(self, entry: str) -> None:
        self.log.append(entry)


def encode(value) -> str:
    """A module-level function, called by name — the case that must not be missed."""
    return json.dumps(value)
