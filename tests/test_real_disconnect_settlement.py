"""A client disconnect, through a real server and a real socket.

tests/test_stream_disconnect_accounting.py drives the generator directly and
calls aclose(). That exercises the control flow but not the mechanism a real
disconnect goes through: Starlette runs the streaming body inside an anyio
cancel scope, and once that scope is cancelled anyio re-raises CancelledError
at every checkpoint — including awaits inside the handler that just caught it.

Unshielded, the first Redis write in _settle_stream_on_abort raised before
doing anything, so the whole abort path was inert in production while the
aclose() tests passed. This test is the one that can tell the difference, so
it pays for its slowness.
"""

import asyncio
import contextlib

import pytest
import uvicorn
from redis.asyncio import Redis

import app.main as main_module
from app.budget.dependency import enforce_budget
from app.budget.provider_budget import ProviderBudget
from app.main import app
from app.providers.base import StreamChunk


def _redis_url() -> str:
    import os

    return os.environ.get("REDIS_URL", "redis://:localdevpassword@localhost:6379/0")


class _EndlessRouter:
    async def chat_stream(self, messages, request_id="", params=None, skip_providers=None):
        while True:
            yield StreamChunk(
                content="x" * 400, provider="anthropic", model="claude-opus-5"
            )
            await asyncio.sleep(0.01)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN BUG, verified not hypothetical: on a real client disconnect the"
        " streaming generator is never resumed — instrumenting every handler"
        " showed no exception of any kind is delivered to it — so the"
        " `except (GeneratorExit, CancelledError)` branch that charges for"
        " already-generated tokens is dead code in production. The reservation"
        " stays stranded at its worst-case value and the spend is never"
        " recorded. The aclose()-based tests in"
        " tests/test_stream_disconnect_accounting.py pass because aclose()"
        " does resume the generator, which a disconnect does not."
        " strict=True so this fails loudly the day it is fixed."
    ),
)
@pytest.mark.asyncio
async def test_a_real_client_disconnect_still_settles_the_ledger(monkeypatch, isolated_db):
    # A REAL Redis, deliberately. fakeredis resolves without touching a
    # socket, so its awaits are not cancellation checkpoints — which masks the
    # exact bug this test exists to catch. Skipped when none is reachable.
    client = Redis.from_url(_redis_url(), socket_connect_timeout=1)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("no reachable Redis; this test needs real I/O to be meaningful")
    key = "provider_budget:anthropic"
    await client.delete(key)
    budget = ProviderBudget(redis=client, cap_usd=4.0)
    monkeypatch.setattr(main_module, "provider_budget", budget)
    monkeypatch.setattr(
        main_module, "build_router", lambda m: ("smart", _EndlessRouter())
    )
    app.dependency_overrides[enforce_budget] = lambda: "test-client-key"

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):  # wait for a bound socket
            if server.started and server.servers:
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn did not start"
        port = server.servers[0].sockets[0].getsockname()[1]

        body = b'{"model":"smart","messages":[{"role":"user","content":"hi"}]}'
        request = (
            b"POST /v1/chat/stream HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(request)
        await writer.drain()

        # Read enough that the provider has "generated" something worth money.
        streamed = b""
        while len(streamed) < 1200:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
            if not chunk:
                break
            streamed += chunk

        # What the ledger holds right now is the RESERVATION: _reserve_chain
        # claimed the worst case before the first provider call. Asserting
        # "spent > 0" after the disconnect would therefore pass whether or not
        # anything settled, which is the trap the first version of this test
        # fell into.
        reserved = await budget.spent("anthropic")
        assert reserved > 0, "nothing was reserved; the test is not exercising the ceiling"

        writer.transport.abort()  # hard close — no FIN, exactly like a dead client

        settled = reserved
        for _ in range(100):
            await asyncio.sleep(0.05)
            settled = await budget.spent("anthropic")
            if settled != reserved:
                break

        # Settling replaces the worst-case reservation with the estimate from
        # what was actually streamed, which is far smaller.
        assert settled != reserved, (
            f"the ledger still holds the untouched reservation (${reserved:.6f})."
            " The abort handler never ran, so the reservation is stranded and"
            " the tokens the provider already generated were never charged."
        )
        assert 0 < settled < reserved
    finally:
        app.dependency_overrides.pop(enforce_budget, None)
        await client.aclose()
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(serve_task, timeout=10)
