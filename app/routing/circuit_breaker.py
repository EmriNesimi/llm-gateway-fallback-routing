import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"  # normal operation
    OPEN = "open"  # failing, calls blocked
    HALF_OPEN = "half_open"  # cooldown elapsed, one trial call allowed


class CircuitBreaker:
    """Per-provider circuit breaker.

    Opens after `failure_threshold` consecutive failures, blocking further calls
    until `cooldown_seconds` has elapsed. Then allows a single trial call
    (half-open); success closes the circuit, failure re-opens it.
    """

    def __init__(self, failure_threshold: int, cooldown_seconds: float):
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None

    def _update_state(self) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and time.time() - self._opened_at >= self._cooldown_seconds
        ):
            self._state = CircuitState.HALF_OPEN

    def allow_request(self) -> bool:
        self._update_state()
        return self._state is not CircuitState.OPEN

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._failure_count += 1
        if (
            self._state is CircuitState.HALF_OPEN
            or self._failure_count >= self._failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._opened_at = time.time()

    @property
    def state(self) -> CircuitState:
        self._update_state()
        return self._state
