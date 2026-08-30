"""In-memory key-value store with compare-and-swap."""

from __future__ import annotations


class KVStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._rev: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def put(self, key: str, value: str) -> int:
        self._data[key] = value
        self._rev[key] = self._rev.get(key, 0) + 1
        return self._rev[key]

    def cas(self, key: str, expected_rev: int, value: str) -> bool:
        if self._rev.get(key, 0) != expected_rev:
            return False
        self.put(key, value)
        return True

    def delete(self, key: str) -> bool:
        if key not in self._data:
            return False
        del self._data[key]
        self._rev.pop(key, None)
        return True

    def keys(self) -> list[str]:
        return sorted(self._data)
