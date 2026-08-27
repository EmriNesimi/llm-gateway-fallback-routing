#!/usr/bin/env bash
# A guided walkthrough of the gateway's behavior: routing, rate limiting,
# budgets, the admin API, and the audit trail — all in one script.
#
# Requires: the gateway running (uvicorn app.main:app or `docker compose up`)
# and ADMIN_API_KEY set in .env. Uses python3 to parse JSON (no jq required),
# but will use jq for prettier output if it happens to be installed.
#
# Usage: ./scripts/demo.sh
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
ADMIN_KEY="${DEMO_ADMIN_KEY:?Set DEMO_ADMIN_KEY to your ADMIN_API_KEY value}"

step() { printf '\n\033[1;32m=== %s ===\033[0m\n' "$1"; }
jqp() { command -v jq >/dev/null 2>&1 && jq . || python3 -m json.tool || cat; }
json_field() { python3 -c "import sys, json; print(json.load(sys.stdin)['$1'])"; }

step "1. Health check"
curl -sf "$GATEWAY_URL/healthz" | jqp

step "2. Issue a fresh client key via the admin API"
create_response=$(curl -sf -X POST "$GATEWAY_URL/admin/keys" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"team": "demo-team"}')
echo "$create_response" | jqp
CLIENT_KEY=$(echo "$create_response" | json_field api_key)
echo "-> Using this freshly issued key (team: demo-team) for the rest of the demo."

step "3. A normal chat request (routed through the fallback chain)"
# Not using curl -f here on purpose: a failure response (e.g. no provider
# reachable) is itself informative and shouldn't just kill the script.
curl -s -X POST "$GATEWAY_URL/v1/chat" \
  -H "X-API-Key: $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "default", "messages": [{"role": "user", "content": "Say hi in five words."}]}' | jqp
echo "-> Check the 'provider' field above: if your primary provider is down or"
echo "   misconfigured, this will already show the fallback provider that answered."
echo "   (A 502 here means no configured provider — OpenAI/Anthropic/Ollama — is"
echo "   reachable at all; set at least one real key or run Ollama locally.)"

step "3b. The same request through the OpenAI-compatible endpoint"
echo "-> This is what an existing app points at: same routing, same budgets,"
echo "   same audit trail, but OpenAI's response shape so no client changes."
curl -s -X POST "$GATEWAY_URL/v1/chat/completions" \
  -H "X-API-Key: $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "fast", "messages": [{"role": "user", "content": "Say hi in five words."}], "max_tokens": 32}' | jqp

step "3c. Which chains this gateway will route"
curl -s "$GATEWAY_URL/v1/models" -H "X-API-Key: $CLIENT_KEY" | jqp
echo "-> \`model\` selects a chain, not a specific model, so a chain can be"
echo "   re-pointed at a newer model without any client changing."

step "4. Trip the rate limiter (bursting past RATE_LIMIT_CAPACITY)"
for i in $(seq 1 25); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$GATEWAY_URL/v1/chat" \
    -H "X-API-Key: $CLIENT_KEY" -H "Content-Type: application/json" \
    -d '{"model": "default", "messages": [{"role": "user", "content": "hi"}]}')
  printf 'request %02d -> %s\n' "$i" "$code"
  if [ "$code" = "429" ]; then
    echo "-> Rate limit engaged (429) after $i requests."
    break
  fi
done

step "5. Check gateway metrics (Prometheus format)"
curl -sf "$GATEWAY_URL/metrics" | grep -E '^gateway_' | head -20

step "6. Review the audit trail for this demo"
curl -sf "$GATEWAY_URL/admin/audit-log?team=demo-team&limit=10" \
  -H "X-Admin-Key: $ADMIN_KEY" | jqp

step "Done"
echo "Every request above — success, rate-limited, or fallback — is in the"
echo "audit log and reflected in /metrics. Point Grafana (:3000) at Prometheus"
echo "(:9090) for the same data as live dashboards."
