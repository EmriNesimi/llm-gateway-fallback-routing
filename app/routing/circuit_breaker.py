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
        # When the current half-open trial was admitted. Half-open is supposed
        # to let exactly ONE request probe a recovering provider — decision 010
        # leans on that. Without this, every concurrent request in the cooldown
        # window sees HALF_OPEN and is admitted, which is the thundering herd
        # the state exists to prevent, aimed at a provider that is probably
        # still broken and billing for every attempt.
        #
        # Timestamped rather than a plain flag so a trial whose caller never
        # reports back cannot wedge the breaker shut forever: after another
        # cooldown, the next request may claim the trial.
        self._trial_started_at: float | None = None

    def _update_state(self) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and time.time() - self._opened_at >= self._cooldown_seconds
        ):
            self._state = CircuitState.HALF_OPEN

    def allow_request(self) -> bool:
        """Claims the half-open trial as a side effect when it returns True.

        Safe without a lock: this is synchronous and there is no await between
        the check and the claim, so within one event loop it cannot interleave.
        """
        self._update_state()
        if self._state is CircuitState.OPEN:
            return False
        if self._state is CircuitState.HALF_OPEN:
            now = time.time()
            claimed = self._trial_started_at
            if claimed is not None and now - claimed < self._cooldown_seconds:
                return False
            self._trial_started_at = now
        return True

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._trial_started_at = None

    def record_failure(self) -> None:
        self._failure_count += 1
        if (
            self._state is CircuitState.HALF_OPEN
            or self._failure_count >= self._failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._opened_at = time.time()
        self._trial_started_at = None

    @property
    def state(self) -> CircuitState:
        self._update_state()
        return self._state
