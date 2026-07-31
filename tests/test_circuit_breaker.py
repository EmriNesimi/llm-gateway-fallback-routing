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
