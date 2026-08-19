# 010 — Circuit breakers stay per-process, not shared through Redis

## Context

`app/routing/dependencies.py` keeps provider clients and their breakers in
plain module-level dicts:

```python
_PROVIDER_INSTANCES: dict[str, BaseProvider] = {}
_BREAKERS: dict[str, CircuitBreaker] = {}
```

So breaker state is per-process. That sits oddly beside the rest of the
gateway, where the deliberate choice went the other way: rate limits and
monthly budgets are Redis-backed precisely so that N replicas enforce one
shared allowance rather than N separate ones. Someone reading the code is
entitled to ask whether the breaker is a considered exception or an oversight,
and until now nothing said which.

The cost is real. With `CIRCUIT_BREAKER_FAILURE_THRESHOLD` at 3 and four
replicas running, a provider going down is discovered independently by each
one — twelve failed requests instead of three, repeated once per cooldown
window for as long as the outage lasts.

## Decision

Breakers stay per-process. Deliberately, and this is the record of why.

## Why sharing them is worse than it looks

**A shared breaker turns a local fault into a global outage.** This is the
argument that settles it. Provider failures are not always symmetric across
replicas: a wedged connection pool, a bad DNS answer cached on one node, a
network partition touching one availability zone, a single replica holding a
stale credential. With per-process breakers the sick replica opens its own
circuit, fails over to the next provider, and the healthy replicas carry on
using the primary. With a shared breaker, one sick replica reports the
failures that trip the circuit *for everyone*, and a fault affecting a
fraction of capacity becomes a fleet-wide provider outage. The blast radius
moves in exactly the wrong direction.

**It puts a network round-trip in the hot path.** `allow_request()` is
consulted for every provider on every request, before any provider is called.
Today that's a dictionary lookup. Backed by Redis it's a round-trip — added to
the latency of requests that are working fine, in service of an optimization
for requests that aren't.

**It creates a new failure mode where there wasn't one.** Redis being
unreachable currently means rate limiting and budgets fail closed, which is
correct: those are spend controls, and refusing service beats unmetered
spending (decision 004). A Redis-backed breaker needs its own answer to the
same question, and both answers are bad. Fail closed, and a Redis blip takes
routing down entirely. Fail open, and the breaker silently stops protecting
anything at precisely the moment the infrastructure is already unhealthy.

**Half-open needs coordination that shared state alone doesn't provide.** The
point of half-open is that exactly one trial request probes a recovering
provider. Shared state without a distributed lock gives every replica a trial
request simultaneously — the thundering herd the state exists to prevent — so
a correct implementation needs a lock, with lease and expiry handling of its
own. That is meaningful complexity for a component whose entire job is to make
failure simpler.

## What the cost actually is

The waste is bounded and self-limiting: `failure_threshold × replica_count`
requests per cooldown window, against a provider that is already failing, on
requests that were going to fail anyway. Those requests aren't lost — they
fall through to the next provider in the chain and get served. The user-facing
cost is added latency on a handful of requests during an outage, not errors.

Set against a fleet-wide outage triggered by one unhealthy replica, that is
the trade to take.

## Consequences

- `gateway_circuit_state` is per-replica, and that is the honest reading of
  it. Prometheus scrapes each instance separately, so divergence between
  replicas is visible rather than hidden — and divergence is *information*:
  one replica reporting OPEN while the rest report CLOSED is the signature of
  a replica-local fault, which a shared breaker would have concealed by
  design.
- Alert on the aggregate (`max by (provider) (gateway_circuit_state)`) to
  catch a genuinely down provider, and on the spread across instances to catch
  a single sick replica. Those are different incidents and should page
  differently.
- Tuning `CIRCUIT_BREAKER_FAILURE_THRESHOLD` should account for replica count:
  the requests spent discovering an outage are threshold × replicas, so a
  large fleet wants a lower threshold than a single instance does.
- If a future deployment genuinely needs coordinated breaking — a provider
  with a hard global concurrency quota, say, where one replica's overuse harms
  the others — revisit this. That's a different problem from the one described
  here, and it would justify the lock.
