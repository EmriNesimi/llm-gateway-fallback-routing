import pytest

from app.providers.base import ProviderError, is_retryable_status_code


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (400, False),  # bad request
        (401, False),  # auth failure
        (403, False),  # forbidden
        (404, False),  # not found
        (422, False),  # unprocessable
        (428, False),  # arbitrary other 4xx
        (429, True),  # rate limited — worth retrying
        (500, True),
        (502, True),
        (503, True),
        (599, True),
    ],
)
def test_is_retryable_status_code(status_code, expected):
    assert is_retryable_status_code(status_code) is expected


def test_provider_error_defaults_to_retryable():
    exc = ProviderError("something went wrong")
    assert exc.retryable is True


def test_provider_error_retryable_can_be_set_false():
    exc = ProviderError("bad request", retryable=False)
    assert exc.retryable is False
