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
def _redis_url_from_env_file() -> str:
    """REDIS_URL as .env sets it, or the default if there is no .env."""
    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "REDIS_URL":
                return value.strip().strip("\"'")
    return "redis://localhost:6379/0"


os.environ["REDIS_URL"] = _scratch_redis_url(_redis_url_from_env_file())
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
from app.providers.base import ChatResponse  # noqa: E402 - see above
from app.ratelimit import dependency as ratelimit_dependency  # noqa: E402 - see above
from app.security.auth import require_api_key  # noqa: E402 - see above


class _Instant:
    """Answers at once, with token counts the ledgers will price."""

    async def chat(self, messages, request_id="", params=None, skip_providers=None):
        """Return immediately. No network, no provider, no cost."""
        return ChatResponse(
            content="ok", provider="anthropic", model="claude-opus-5",
            input_tokens=100, output_tokens=20,
        )


async def _run(requests: int, chain: str) -> int:
    main_module.build_router = lambda model: (chain, _Instant())
    app.dependency_overrides[require_api_key] = lambda: "bench-key"

    # The rate limiter still runs — its Redis round-trip is part of what is
    # being measured — but with a bucket large enough not to refuse the run.
    # Left at the default it starts returning 429 after twenty requests, and
    # a 429 is not the path this is timing.
    ratelimit_dependency._limiter._capacity = requests * 2
    # And the bucket from the last run is cleared: it persists in the scratch
    # database, so a second run inherits an empty bucket and is refused from
    # the first request regardless of the capacity set above.
    scratch = ratelimit_dependency._limiter._redis
    for key in await scratch.keys("ratelimit:*"):
        await scratch.delete(key)
    # Same for the caps: an unmetered benchmark would otherwise settle enough
    # fake spend to trip them partway through and turn the tail into 402s.
    settings.monthly_budget_usd_per_key = 1e9
    budget_dependency.tracker._monthly_cap_usd = 1e9
    budget_dependency.provider_budget._cap_usd = 1e9

    logging.getLogger("httpx").setLevel(logging.WARNING)
    # No lifespan runs under ASGITransport, so the throwaway database has no
    # tables until this.
    await init_db()

    body = {"model": chain, "messages": [{"role": "user", "content": "hi"}]}
    transport = httpx.ASGITransport(app=app)
    samples: list[float] = []

    async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
        # Warm the pools and let first-call imports settle, so the first
        # sample is not measuring startup.
        for _ in range(5):
            await client.post("/v1/chat", json=body)
        for _ in range(requests):
            started = time.perf_counter()
            response = await client.post("/v1/chat", json=body)
            samples.append((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                print(
                    f"request failed with {response.status_code}: {response.text[:200]}",
                    file=sys.stderr,
                )
                return 1

    samples.sort()

    def pct(p: float) -> float:
        return samples[min(len(samples) - 1, int(len(samples) * p))]

    print(f"{requests} requests through /v1/chat on the {chain!r} chain, provider stubbed")
    print(f"  mean   {statistics.fmean(samples):7.2f} ms")
    print(f"  median {pct(0.50):7.2f} ms")
    print(f"  p90    {pct(0.90):7.2f} ms")
    print(f"  p95    {pct(0.95):7.2f} ms")
    print(f"  p99    {pct(0.99):7.2f} ms")
    print(f"  max    {samples[-1]:7.2f} ms")
    print()
    print("This is the gateway's own cost: routing, both ledgers reserving and")
    print("settling, the audit write, metrics. No provider call is in it.")
    return 0


def main() -> None:
    """Parse arguments and run the benchmark."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--requests", type=int, default=500, help="samples to take")
    parser.add_argument("--chain", default="smart", help="which chain to route (default: smart)")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.requests, args.chain)))


if __name__ == "__main__":
    main()
