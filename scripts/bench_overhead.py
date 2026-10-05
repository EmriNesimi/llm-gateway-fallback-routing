"""Measure what the gateway itself costs per request, with the providers stubbed.

docs/load-test-results.md asks for this and says why: its numbers are an
end-to-end measurement dominated by the upstream round trip, so they cannot
answer "what did the gateway add". The reservation path is the largest thing
added since, and it is invisible behind a 700ms provider call.

So the provider is replaced with one that returns immediately, and everything
else is real — a real router, real reservations, real settles, and a real
Redis with whatever durability it is configured for. What is left in the
timing is the gateway: routing, both ledgers reserving and settling, the
audit write, metrics.

Costs nothing and touches no provider. It does write to the ledgers it is
pointed at, so it defaults to a scratch Redis database and refuses to run
against the one the gateway uses — the suite already destroyed the real
ledger once by doing exactly that.

    python -m scripts.bench_overhead --requests 500
    python -m scripts.bench_overhead --compare     # and what reserving costs

Exit codes:

    0   measured
    1   a request failed, or the chain asked for does not exist
    2   Redis could not be reached, so nothing was measured
    3   measured, and the result disproves itself — a negative reservation
        cost means the machine's noise was larger than the thing being
        measured. Re-run somewhere idle.
"""

import argparse
import asyncio
import logging
import os
import pathlib
import re
import statistics
import sys
import tempfile
import time

import httpx

# A scratch database, never the gateway's. See the module docstring.
_SCRATCH_DB = 14


def _scratch_redis_url(url: str) -> str:
    """The same server, a different database index."""
    swapped, n = re.subn(r"/\d+$", f"/{_SCRATCH_DB}", url)
    return swapped if n else f"{url.rstrip('/')}/{_SCRATCH_DB}"


# Only REDIS_URL is taken from .env, and pointed at a scratch database.
# GATEWAY_ENV_FILE is then blanked so nothing else in .env loads: no OTLP
# endpoint to export traces nowhere, no real client keys, no budget caps that
# would refuse the run partway through. The benchmark measures the request
# path, not one deployment's configuration.
#
# All of it before any app import, because app/core/config.py builds its
# Settings singleton at import time and app/budget/dependency.py builds the
# ledger clients from it. tests/conftest.py does the same thing for the same
# reason and carries the same E402 waiver.
def _configured_redis_url() -> str:
    """REDIS_URL from the environment, else from .env, else the default.

    That order matters and was wrong at first: this read .env unconditionally,
    so `REDIS_URL=... make bench` was silently ignored and the run went to
    whichever Redis .env happened to name. An explicit environment variable
    has to win — it is the only way to point this at a different server, and
    a flag that does nothing is worse than no flag.
    """
    from_env = os.environ.get("REDIS_URL")
    if from_env:
        return from_env

    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "REDIS_URL":
                return value.strip().strip("\"'")
    return "redis://localhost:6379/0"


os.environ["REDIS_URL"] = _scratch_redis_url(_configured_redis_url())
# And a throwaway database. Redirecting Redis alone is not isolation: the
# audit write is part of the request path being measured, so every sample
# lands a row — 339 of them went into the real gateway.db the first time this
# was run, which is the same pollution tests/conftest.py's autouse isolated_db
# fixture exists to prevent. A benchmark is not a test and gets no fixture.
_BENCH_DB = pathlib.Path(tempfile.gettempdir()) / "llm-gateway-bench.db"
_BENCH_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_BENCH_DB}"
os.environ["GATEWAY_ENV_FILE"] = ""

import app.main as main_module  # noqa: E402 - see above
from app.budget import dependency as budget_dependency  # noqa: E402 - see above
from app.core.config import settings  # noqa: E402 - see above
from app.db.session import init_db  # noqa: E402 - see above
from app.main import app  # noqa: E402 - see above
from app.providers.base import ChatResponse, StreamChunk  # noqa: E402 - see above
from app.ratelimit import dependency as ratelimit_dependency  # noqa: E402 - see above
from app.routing.model_map import FALLBACK_CHAINS  # noqa: E402 - see above
from app.security.auth import require_api_key  # noqa: E402 - see above
from scripts.reconcile import _redacted  # noqa: E402 - see above


class _Instant:
    """Answers at once, with token counts the ledgers will price."""

    async def chat(self, messages, request_id="", params=None, skip_providers=None):
        """Return immediately. No network, no provider, no cost."""
        return ChatResponse(
            content="ok", provider="anthropic", model="claude-opus-5",
            input_tokens=100, output_tokens=20,
        )

    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        """A few chunks and the usage totals, with no waiting between them.

        Short on purpose: the streaming path's cost is in its bookkeeping —
        reserve, settle, the `settled` flag, the audit write — not in how many
        chunks go past, and a long stream would just measure the transport.
        """
        for _ in range(4):
            yield StreamChunk(content="x" * 64, provider="anthropic", model="claude-opus-5")
        yield StreamChunk(
            content="", done=True, provider="anthropic", model="claude-opus-5",
            input_tokens=100, output_tokens=20,
        )


_UNREACHABLE = 2
# A measurement the run itself proves wrong. Its own exit code so a script
# can tell "could not measure" from "measured, and here it is".
_TOO_NOISY = 3


# A chain of only free providers. _reserve_chain skips free providers
# entirely, and with nothing reserved against a provider the per-key
# reservation is skipped too — so this chain does zero Redis round-trips for
# spend, where a billable one does six. The difference between them is the
# reservation cost, which is the number docs/load-test-results.md keeps
# saying it cannot produce.
#
# The provider is stubbed either way, so this measures the bookkeeping the
# chain implies, not Ollama. It does not need Ollama running.
_FREE_CHAIN = "local"


async def _measure(client, requests: int, chain: str) -> dict[str, list[float]]:
    """Time `requests` through each endpoint on one chain. Milliseconds, sorted."""
    main_module.build_router = lambda model: (chain, _Instant())

    # The bucket from the last chain is cleared: it persists in the scratch
    # database, so the second chain would inherit an empty one and be refused
    # from its first request regardless of the capacity set below.
    scratch = ratelimit_dependency._limiter._redis
    for key in await scratch.keys("ratelimit:*"):
        await scratch.delete(key)

    body = {"model": chain, "messages": [{"role": "user", "content": "hi"}]}

    async def one(endpoint: str) -> int:
        """Drive one request to completion and return its status."""
        if endpoint == "/v1/chat/completions":
            # The OpenAI-compatible endpoint streams on a body field rather
            # than a separate path, so it is the one that has to be told.
            return (await client.post(endpoint, json={**body, "stream": False})).status_code
        if endpoint == "/v1/chat":
            return (await client.post(endpoint, json=body)).status_code
        # A stream is not finished when the response arrives — its
        # bookkeeping runs at the tail, after the last chunk. Timing the
        # response alone would miss the settle entirely, which is most of
        # what this is here to measure.
        async with client.stream("POST", endpoint, json=body) as response:
            async for _ in response.aiter_bytes():
                pass
            return response.status_code

    results: dict[str, list[float]] = {}
    # /v1/chat/completions included because it is the endpoint real traffic
    # uses — an existing application points its base_url here and changes
    # nothing else. It shares _serve_chat with /v1/chat but carries its own
    # request parsing and response shaping, so it is not free by assumption.
    for endpoint in ("/v1/chat", "/v1/chat/stream", "/v1/chat/completions"):
        samples: list[float] = []
        # Warm the pools and let first-call imports settle, so the first
        # sample is not measuring startup.
        for _ in range(5):
            await one(endpoint)
        for _ in range(requests):
            started = time.perf_counter()
            status = await one(endpoint)
            samples.append((time.perf_counter() - started) * 1000)
            if status != 200:
                msg = f"{endpoint} on chain {chain!r} failed with {status}"
                raise RuntimeError(msg)
        samples.sort()
        results[endpoint] = samples
    return results


def _pct(samples: list[float], p: float) -> float:
    return samples[min(len(samples) - 1, int(len(samples) * p))]


def _print_table(title: str, results: dict[str, list[float]]) -> None:
    print(title)
    print()
    print(f"  {'':<22}{'mean':>9}{'median':>9}{'p90':>9}{'p95':>9}{'p99':>9}{'max':>9}")
    for endpoint, samples in results.items():
        print(
            f"  {endpoint:<22}"
            f"{statistics.fmean(samples):>8.2f} "
            f"{_pct(samples, 0.50):>8.2f} "
            f"{_pct(samples, 0.90):>8.2f} "
            f"{_pct(samples, 0.95):>8.2f} "
            f"{_pct(samples, 0.99):>8.2f} "
            f"{samples[-1]:>8.2f}",
        )
    print()


async def _run(requests: int, chain: str, compare: bool) -> int:
    # Same split as reconcile.py and purge_audit_rows.py: a store that cannot
    # be reached gets one line and its own exit code, not a traceback. The
    # ledgers fail closed, so an unreachable Redis means every request is
    # refused — which would otherwise read as the gateway being slow.
    try:
        await ratelimit_dependency._limiter._redis.ping()
    except Exception as exc:  # noqa: BLE001 - reported, not handled
        # Redacted, via reconcile's helper rather than a second copy: this
        # prints on failure, which is when output gets pasted into an issue.
        print(
            f"cannot reach Redis at {_redacted(settings.redis_url)}: {exc}",
            file=sys.stderr,
        )
        return _UNREACHABLE

    # Checked here rather than left to fail inside _reserve_chain, where a
    # typo surfaces as a bare KeyError from a dict lookup four frames down.
    for name in (chain, _FREE_CHAIN) if compare else (chain,):
        if name not in FALLBACK_CHAINS:
            print(
                f"no chain named {name!r}. Available: {', '.join(sorted(FALLBACK_CHAINS))}",
                file=sys.stderr,
            )
            return 1

    app.dependency_overrides[require_api_key] = lambda: "bench-key"
    # The rate limiter still runs — its Redis round-trip is part of what is
    # being measured — but with a bucket large enough not to refuse the run.
    # Left at the default it starts returning 429 after twenty requests, and
    # a 429 is not the path this is timing. Sized for every endpoint and
    # chain this run will drive, plus their warm-ups.
    ratelimit_dependency._limiter._capacity = (requests + 10) * 4 + 100
    # Same for the caps: an unmetered benchmark would otherwise settle enough
    # fake spend to trip them partway through and turn the tail into 402s.
    settings.monthly_budget_usd_per_key = 1e9
    budget_dependency.tracker._monthly_cap_usd = 1e9
    budget_dependency.provider_budget._cap_usd = 1e9

    logging.getLogger("httpx").setLevel(logging.WARNING)
    # No lifespan runs under ASGITransport, so the throwaway database has no
    # tables until this.
    await init_db()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
        try:
            billable = await _measure(client, requests, chain)
            free = await _measure(client, requests, _FREE_CHAIN) if compare else None
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1

    _print_table(
        f"{requests} requests per endpoint on the {chain!r} chain, provider stubbed",
        billable,
    )
    if free is not None:
        _print_table(
            f"The same on {_FREE_CHAIN!r}, which reserves nothing",
            free,
        )
        print("  Reservation cost, median, per request:")
        implausible = []
        for endpoint in billable:
            delta = _pct(billable[endpoint], 0.50) - _pct(free[endpoint], 0.50)
            print(f"    {endpoint:<22}{delta:>7.2f} ms")
            if delta < 0:
                implausible.append(endpoint)
        print()
        if implausible:
            # Reserving cannot make a request faster. A negative figure is not
            # a surprising result, it is proof the noise on this machine
            # exceeded what is being measured — which a loaded laptop will do
            # happily while still printing a confident-looking number.
            print(f"  !! Negative for {', '.join(implausible)}, which is impossible:")
            print("     reserving cannot make a request faster. The noise on this")
            print("     machine is larger than the thing being measured, so none of")
            print("     the deltas above mean anything. Re-run on an idle machine.")
            print()
            return _TOO_NOISY
        print("  A billable chain reserves and settles against each provider and")
        print(f"  again against the caller's key; {_FREE_CHAIN!r} does neither, because")
        print("  _reserve_chain skips free providers and then has nothing to claim")
        print("  against the key either. The difference is what that bookkeeping")
        print("  costs — the figure docs/load-test-results.md says it cannot give.")
        print()

    print("All in milliseconds. This is the gateway's own cost: routing, both")
    print("ledgers reserving and settling, the audit write, metrics. No provider")
    print("call is in it. The streaming row includes the tail bookkeeping, which")
    print("runs after the last chunk — timing the response alone would miss it.")
    return 0


def _positive_int(raw: str) -> int:
    """At least one sample, or argparse rejects it with the usual usage line."""
    value = int(raw)
    if value < 1:
        msg = f"need at least one request, got {value}"
        raise argparse.ArgumentTypeError(msg)
    return value


def main() -> None:
    """Parse arguments and run the benchmark."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--requests",
        # Rejected here rather than later: zero produces an empty table with
        # headers and no rows, and a negative one produces the same. Neither
        # looks like an error, and the percentiles below index into the list.
        type=_positive_int,
        default=500,
        help="samples to take per endpoint (default: 500)",
    )
    parser.add_argument("--chain", default="smart", help="which chain to route (default: smart)")
    parser.add_argument(
        "--compare",
        action="store_true",
        help=f"also measure the free {_FREE_CHAIN!r} chain and report the reservation cost",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.requests, args.chain, args.compare)))


if __name__ == "__main__":
    main()
