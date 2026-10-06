from __future__ import annotations

from threading import Event, Lock


class CancellationToken:
    """Thread-safe, one-way local stop signal independent of the planner."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reason = "GLOBAL_STOP"

    def cancel(self, reason: str = "GLOBAL_STOP") -> None:
        if not reason:
            raise ValueError("cancellation reason must not be empty")
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

