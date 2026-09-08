import time

from app.routing.circuit_breaker import CircuitBreaker, CircuitState


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


def test_half_opens_after_cooldown_then_closes_on_success():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow_request() is False

    time.sleep(0.06)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow_request() is True

    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_half_open_failure_reopens_circuit():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)

    breaker.record_failure()
    time.sleep(0.06)
    assert breaker.state is CircuitState.HALF_OPEN

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow_request() is False


def test_half_open_admits_exactly_one_trial():
    """decision 010 rests on this: half-open lets ONE request probe a
    recovering provider. Without a claim, every concurrent request in the
    cooldown window sees HALF_OPEN and is admitted — the thundering herd the
    state exists to prevent, pointed at a provider that is probably still
    broken and billing for each attempt.
    """
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    breaker.record_failure()
    assert breaker.allow_request() is False  # open

    time.sleep(0.06)  # cooldown elapses -> half-open

    assert breaker.allow_request() is True, "the first request should get the trial"
    assert breaker.allow_request() is False, "a second concurrent request took the trial too"
    assert breaker.allow_request() is False


def test_a_trial_that_never_reports_back_does_not_wedge_the_breaker():
    """The claim is timestamped, not a bare flag. If the caller that took the
    trial dies without recording success or failure, the breaker must not stay
    shut forever — after another cooldown the next request may try."""
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    breaker.record_failure()
    time.sleep(0.06)

    assert breaker.allow_request() is True
    assert breaker.allow_request() is False

    time.sleep(0.06)  # the abandoned trial ages out

    assert breaker.allow_request() is True, "the breaker never recovered"


def test_a_successful_trial_closes_and_frees_the_claim():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    breaker.record_failure()
    time.sleep(0.06)

    assert breaker.allow_request() is True
    breaker.record_success()

    # Closed: everything is admitted again, no lingering claim.
    assert breaker.allow_request() is True
    assert breaker.allow_request() is True
