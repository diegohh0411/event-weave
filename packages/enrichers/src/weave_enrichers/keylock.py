"""One lock per key, alive while any thread holds or waits on it, so cache eviction can never split a key's lock."""
import threading
from contextlib import contextmanager


class KeyedLocks:
    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict[object, list] = {}  # key -> [lock, users]

    @contextmanager
    def hold(self, key):
        with self._guard:
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1
                if entry[1] == 0:
                    del self._locks[key]

    def __len__(self) -> int:
        with self._guard:
            return len(self._locks)
