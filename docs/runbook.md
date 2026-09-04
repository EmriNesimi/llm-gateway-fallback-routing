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
