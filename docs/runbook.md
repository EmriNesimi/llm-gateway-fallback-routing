# Alert runbook

One section per rule in [`deploy/prometheus/alerts.yml`](../deploy/prometheus/alerts.yml).
Each says what the alert actually means, what to check first, and — where it
matters — what *not* to do.

Written because the alerts were added by someone who had the failure modes in
their head at the time, and that person is not the one who gets paged.

## ProviderCircuitOpen

**Means:** every replica reports the breaker open for one provider, so it is
being skipped without a network call. Requests are falling through to the next
provider in the chain.

**Check:** the provider's own status page, then `gateway_provider_attempts_total`
by outcome to see what the failures were before the breaker tripped.

**Do not** restart the gateway to "reset" it. The breaker half-opens on its own
after `CIRCUIT_BREAKER_COOLDOWN_SECONDS` and a restart only hides how long the
provider has been down.

## ProviderCircuitDisagreement

**Means:** one replica sees a provider as failing and another does not. The
breakers are deliberately per-process ([decision 010](decisions/010-per-process-circuit-breakers.md)),
so this is the signature of a replica-local fault — a wedged connection pool,
stale DNS, a credential that only one instance has.

**Check:** which instance disagrees, then that instance's logs. A shared
breaker would have hidden this by design; that it is visible is the point.

## GatewayTargetDown

**Means:** Prometheus cannot scrape the gateway. Every other rule here is
evaluated over metrics the gateway publishes, so while this is firing the rest
of the alerts are silent regardless of what is happening.

**Check:** whether the process is up at all, then whether `METRICS_TOKEN` was
set without adding the matching credentials to the scrape config — `/metrics`
starts returning 401 and the target reads as down with nothing pointing at the
token.

## GatewayRequestsFailingAcrossWholeChain

**Means:** more than 10% of requests are failing *every* provider. Fallback
exists to absorb one provider going bad, so this means the absorbing has
stopped working.

**Check:** whether `ProviderBudgetExhausted` is also firing — an exhausted
provider is dropped from the chain, and a chain with nothing left in it fails
this way. If so, that is the cause and this is the symptom.

## GatewayRefusingUnpricedRequests

**Means:** a routable model has no entry in `app/budget/pricing.py`, so its
cost cannot be bounded and the request is refused rather than run unmetered
([decision 012](decisions/012-uncostable-requests-are-refused.md)).

**Check:** the `unpriced` field in the 503 body names the providers. Add the
missing `_PRICING` entry.

**Do not** wait for it to clear. No balance is draining and retrying will never
help; this is a configuration error and only a deploy fixes it. The build guard
that makes it unreachable has already failed if this is firing.

## ProviderFallbackRateHigh

**Means:** more than a quarter of requests are being answered by a fallback
rather than the first provider in the chain. Requests are succeeding, which is
why nothing else is firing.

**Why no other alert catches it:** the breaker only opens on outright
failures. A provider that times out and succeeds on retry, or fails just under
`CIRCUIT_BREAKER_FAILURE_THRESHOLD`, never trips it — fallback absorbs the
problem and hides it. The cost is real though: every affected request pays for
a failed attempt before the one that works.

**Check:** `gateway_provider_attempts_total` by provider and outcome to see
which one is degrading, then that provider's status page. Compare
`gateway_provider_latency_seconds` across providers — a primary that is slow
but not failing is the usual cause.

**Do not** raise `PROVIDER_RETRY_ATTEMPTS` to make it go away. That multiplies
the reserved budget per request (decision 014) and spends more money on a
provider that is already not working.

## ProviderBudgetLow

**Means:** under $1 of the lifetime ceiling remains for a provider. It is still
serving.

**Check:** `gateway_provider_budget_spent_usd` against
`PROVIDER_LIFETIME_BUDGET_USD`. Decide whether to raise the ceiling or let it
stop. Note the ledger is lifetime and never resets
([decision 013](decisions/013-the-spend-ledger-is-persisted.md)) — this is not
a monthly cap that will roll over.

## ProviderBudgetExhausted

**Means:** a provider has spent its entire lifetime allowance and is now
dropped from every chain before it can be called. Requests fall through to the
next provider; if none is left, callers get a `402`.

**Check:** that the spend is real before raising the cap. Cross-check
`gateway_cost_usd_total` against the provider's own billing page. The ledger is
the gateway's belief about spend, not the provider's invoice, and the two
diverging is itself worth understanding.

**Do not** clear the Redis key to make it go away. That is the only copy of the
number.

## Working with the ledger

Not an alert. This is the section the two budget alerts above point at when
they say what not to do.

**Read it:** `make ledger` prints spend and remaining headroom per provider,
through the same settings the gateway uses — so it reports what the gateway
would enforce, not what some other Redis happens to hold.

**Raise the ceiling:** change `PROVIDER_LIFETIME_BUDGET_USD` and restart. The
ledger is untouched; only the limit it is compared against moves. This is the
right lever when the spend is real and you want to keep going.

**Reset it:** deleting `provider_budget:<provider>` in Redis sets that
provider's lifetime spend back to zero. There is no second copy, so this is
not undoable and it discards the record of money that was genuinely spent. It
is the right move only when the ledger is wrong — for example after testing
against a fake provider inflated it.

**Back it up:** the ledger lives in the `redis-data` volume. `docker compose
down` keeps it; `docker compose down -v` deletes it, and that is the usual way
this number is lost by accident.

## The gateway is up but refusing everything

Not an alert either — this is the shape of a misconfiguration that looks
healthy from the outside, so no rule catches it.

**Symptom:** `/healthz` returns 200, the process is running, and every request
to `/v1/chat` comes back refused. `GatewayTargetDown` does not fire, because
the gateway is up and being scraped.

**First check `/readyz`.** It reports Redis and the database separately, and
unlike `/healthz` it actually touches them.

**If Redis reports `error`:** the usual cause is credentials. The bundled
Redis runs with `--requirepass`, and `.env.example` ships a passwordless
`REDIS_URL`, so running the gateway on the host against the compose stack
fails with `NOAUTH` on every call. Fix by putting the password in the URL:

```
REDIS_URL=redis://:${REDIS_PASSWORD}@localhost:6379/0
```

The reason this presents as "refusing everything" rather than "crashing" is
deliberate: the rate limiter and both budget checks **fail closed**
([decision 004](decisions/004-best-effort-bookkeeping-vs-fail-closed-enforcement.md)).
Being unable to prove there is budget left is not the same as having budget
left, so an unreachable Redis refuses rather than waves requests through. The
alternative would spend money on the strength of a broken connection.

**If Redis reports `ok`:** check `gateway_requests_refused_total` by reason,
or the response body — a `402` names the spend, a `503` names the unpriced
providers, and a `429` is the rate limiter doing its job.

## A caller says they are being refused

Not an alert — this arrives as a message from whoever is using the gateway,
usually without the status code attached. Ask for it, because the five
outcomes need five different answers and only one of them is your problem.

| They see | Means | What to do |
|---|---|---|
| `401` | the key is not recognised | check it against `GATEWAY_API_KEYS`, or `/admin/keys` if it was issued through the admin API. A revoked key looks identical to a wrong one, by design |
| `429` | rate limited | `Retry-After` says how long. If it is constant, raise `RATE_LIMIT_CAPACITY` / `RATE_LIMIT_REFILL_PER_SEC` — not the budget |
| `402`, `"monthly budget exceeded for this API key"` | that caller has spent their own share | raise `MONTHLY_BUDGET_USD_PER_KEY`, or wait for the period to roll. The operator's balance is untouched |
| `402`, `"provider budget exhausted"` | the operator's lifetime ceiling is gone | `make ledger` to see the real numbers, then decide: raise `PROVIDER_LIFETIME_BUDGET_USD` or stop. Waiting will not help — this ledger never resets |
| `503`, `"no pricing configured"` | a routable model has no price, so its cost cannot be bounded | add the `_PRICING` entry and deploy. Nothing the caller does will fix it |

The two `402`s are worth separating carefully: the message is the only thing
distinguishing "this caller used their allowance" from "we are out of money".
Every refusal is also counted in `gateway_requests_refused_total`, so the
dashboard answers the same question without waiting to be asked. The labels
map one to one onto the rows above:

- `rate_limit` — a client hit its token bucket (`429`)
- `admin_rate_limit` — the admin API's shared bucket; unlike the others this
  one means something is hammering key issuance, not that a client is busy
- `key_budget_exhausted` — that caller's monthly share (`402`)
- `provider_budget_exhausted` — the operator's lifetime ceiling (`402`)
- `no_pricing_configured` — un-costable model (`503`)

The two `402` labels are the pair worth reading carefully: they are the only
thing distinguishing "this caller used their allowance" from "we are out of
money".

If they report no status at all and the gateway looks healthy from outside,
see [The gateway is up but refusing everything](#the-gateway-is-up-but-refusing-everything).
