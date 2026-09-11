"""The breaker's state machine, driven by a clock the test controls.

Every transition in CircuitBreaker is a comparison against the clock, so
sleeping through real cooldowns tests the machine's scheduler as much as the
breaker. The trial claim in particular ages out after one cooldown, and any
pause between claiming it and asserting it is still held will flip the result:
a GC pause, a loaded CI box, or the coverage instrumentation `make test` adds.
That is the shape of the one unreproduced failure in issue #17 — a single red
run that nine reruns could not reproduce.

A fake clock removes the class rather than widening the window. It also lets
these tests state the interval they mean ("one cooldown later") instead of
encoding it as a sleep 20% longer than the thing it is waiting for.
"""

import time

import pytest

from app.routing.circuit_breaker import CircuitBreaker, CircuitState

COOLDOWN = 30.0


class FakeClock:
    """A monotonic clock that only moves when a test says so."""

    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def breaker(clock):
    return CircuitBreaker(failure_threshold=1, cooldown_seconds=COOLDOWN, clock=clock)


def test_closed_by_default():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_opens_after_threshold_failures():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow_request() is True

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow_request() is False


def test_success_resets_failure_count_and_closes():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_the_default_clock_is_wall_time():
    """The injected clock is a testing seam, not a behaviour change. Nothing
    else here would notice if the default stopped being the real clock."""
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=COOLDOWN)
    before = time.time()

    breaker.record_failure()

    assert before <= breaker._opened_at <= time.time()


def test_the_circuit_stays_open_right_up_to_the_cooldown(breaker, clock):
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN

    clock.advance(COOLDOWN - 0.001)

    assert breaker.state is CircuitState.OPEN
    assert breaker.allow_request() is False


def test_half_opens_after_cooldown_then_closes_on_success(breaker, clock):
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow_request() is False

    clock.advance(COOLDOWN)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow_request() is True

    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_half_open_failure_reopens_circuit(breaker, clock):
    breaker.record_failure()
    clock.advance(COOLDOWN)
    assert breaker.state is CircuitState.HALF_OPEN

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow_request() is False


def test_half_open_admits_exactly_one_trial(breaker, clock):
    """decision 010 rests on this: half-open lets ONE request probe a
    recovering provider. Without a claim, every concurrent request in the
    cooldown window sees HALF_OPEN and is admitted — the thundering herd the
    state exists to prevent, pointed at a provider that is probably still
    broken and billing for each attempt.

    The clock is frozen across the three calls, which is what "concurrent"
    actually means here. Against the real clock the trial could age out
    between them and the third call would be admitted on merit.
    """
    breaker.record_failure()
    assert breaker.allow_request() is False  # open

    clock.advance(COOLDOWN)  # -> half-open

    assert breaker.allow_request() is True, "the first request should get the trial"
    assert breaker.allow_request() is False, "a second concurrent request took the trial too"
    assert breaker.allow_request() is False


def test_the_trial_claim_is_held_for_a_full_cooldown(breaker, clock):
    """Not merely "for a moment". The claim expiring early would readmit the
    herd it exists to hold back, at a provider still assumed broken."""
    breaker.record_failure()
    clock.advance(COOLDOWN)
    assert breaker.allow_request() is True

    clock.advance(COOLDOWN - 0.001)

    assert breaker.allow_request() is False, "the trial claim lapsed before its cooldown"


def test_a_trial_that_never_reports_back_does_not_wedge_the_breaker(breaker, clock):
    """The claim is timestamped, not a bare flag. If the caller that took the
    trial dies without recording success or failure, the breaker must not stay
    shut forever — after another cooldown the next request may try."""
    breaker.record_failure()
    clock.advance(COOLDOWN)

    assert breaker.allow_request() is True
    assert breaker.allow_request() is False

    clock.advance(COOLDOWN)  # the abandoned trial ages out

    assert breaker.allow_request() is True, "the breaker never recovered"


def test_a_successful_trial_closes_and_frees_the_claim(breaker, clock):
    breaker.record_failure()
    clock.advance(COOLDOWN)

    assert breaker.allow_request() is True
    breaker.record_success()

    # Closed: everything is admitted again, no lingering claim.
    assert breaker.allow_request() is True
    assert breaker.allow_request() is True
