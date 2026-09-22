"""Per-provider circuit breaker: closed, open after N failures, half-open after a cooldown.

Half-open admits exactly one trial request — the claim is timestamped so a
trial that never reports back cannot wedge the breaker shut. The clock is
injectable so the tests drive a fake one instead of racing wall time; that
race was one of the candidates in issue #17.
"""

import time
from collections.abc import Callable
from enum import Enum


class CircuitState(Enum):
    """Closed is normal. Open blocks calls. Half-open admits one trial after the cooldown."""

    CLOSED = "closed"  # normal operation
    OPEN = "open"  # failing, calls blocked
    HALF_OPEN = "half_open"  # cooldown elapsed, one trial call allowed


class CircuitBreaker:
    """Per-provider circuit breaker.

    Opens after `failure_threshold` consecutive failures, blocking further calls
    until `cooldown_seconds` has elapsed. Then allows a single trial call
    (half-open); success closes the circuit, failure re-opens it.
    """

    def __init__(
        self,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.time,
    ):
        # Injectable so the tests can advance time instead of sleeping through
        # it. Every state transition here is a clock comparison, so a test that
        # races real time is testing the machine's scheduler as much as the
        # breaker: the trial claim ages out after one cooldown, and any pause
        # between claiming it and asserting it is held — a GC pause, a loaded
        # CI box, coverage instrumentation — turns a correct breaker into a red
        # build. Widening the window only makes that rarer. Issue #17.
        self._clock = clock
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
            and self._clock() - self._opened_at >= self._cooldown_seconds
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
            now = self._clock()
            claimed = self._trial_started_at
            if claimed is not None and now - claimed < self._cooldown_seconds:
                return False
            self._trial_started_at = now
        return True

    def record_success(self) -> None:
        """Close the circuit and clear the failure count, whatever state it was in."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._trial_started_at = None

    def record_failure(self) -> None:
        """Count a failure. Opens at the threshold, or at once if this was the half-open trial."""
        self._failure_count += 1
        if (
            self._state is CircuitState.HALF_OPEN
            or self._failure_count >= self._failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()
        self._trial_started_at = None

    @property
    def state(self) -> CircuitState:
        """Current state, after applying the open-to-half-open transition if the cooldown is up."""
        self._update_state()
        return self._state
