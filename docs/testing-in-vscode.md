# Testing this project locally in VS Code

## 1. Open and set up the environment

1. `File > Open Folder…` → select `llm-gateway-fallback-routing`.
2. Open the integrated terminal (`` Ctrl+` ``) and create the virtualenv:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Install the **Python** extension (ms-python.python) if you don't have it —
   VS Code will prompt you to select an interpreter; pick `.venv/bin/python`
   (Command Palette → "Python: Select Interpreter").
4. Copy the env template and fill in what you have:
   ```bash
   cp .env.example .env
   ```
   You don't need real provider keys to run the test suite or explore the
   code — only to actually get a chat response back from `/v1/chat`. At
   minimum, set `GATEWAY_API_KEYS` and `ADMIN_API_KEY` to any random strings
   so the auth-gated endpoints don't fail closed.

## 2. Run the test suite from VS Code

- Command Palette → **"Testing: Focus on Test Explorer View"**, or click the
  flask icon in the sidebar.
- If tests aren't discovered: Command Palette → **"Python: Configure
  Tests"** → select **pytest** → select the repo root. (`pyproject.toml`
  already has `pythonpath = ["."]` and `testpaths = ["tests"]` configured,
  so no further setup is needed.)
- Click the play button next to any test, file, or the whole suite. Failures
  show inline with a jump-to-line link.
- Equivalent from the terminal: `make test` (runs with coverage).

Two tests will **skip** unless a real Redis is reachable at `REDIS_URL`
(`tests/test_redis_integration.py`) — that's expected; the rest of the suite
uses `fakeredis` and an in-memory SQLite DB, so it needs nothing external.

## 3. Lint and type-check

```bash
make lint       # ruff
make typecheck  # mypy
```
Both are fast enough to bind to a keyboard shortcut or run on save via the
Python extension's settings if you want live feedback while editing.

## 4. Run the gateway itself

Simplest path — no external services beyond what you install:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for interactive Swagger UI — you can
issue requests to `/v1/chat`, `/admin/keys`, etc. directly from the browser
using the `Authorize` button (paste your `GATEWAY_API_KEYS` / `ADMIN_API_KEY`
value).

To debug with breakpoints instead: create `.vscode/launch.json` with:
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Gateway (uvicorn)",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["app.main:app", "--reload", "--port", "8000"],
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal"
    }
  ]
}
```
Then set breakpoints in, say, `app/routing/fallback.py`, hit `F5`, and send
a request — execution will stop right where you'd expect.

Note: `--reload`'s file-watcher subprocess doesn't always hit breakpoints
set before the reload; if a breakpoint seems to be skipped, drop `--reload`
from the debug config args.

## 5. Full stack with Docker (Redis, Postgres, Prometheus, Grafana)

Needs Docker Desktop running, not just VS Code:

```bash
make up   # or: docker compose up --build
```

Then:
- `http://localhost:8000/docs` — the gateway
- `http://localhost:9090` — Prometheus
- `http://localhost:3000` — Grafana (`admin`/`admin`), with the gateway
  dashboard already provisioned — no manual setup

The **Docker** extension (ms-azuretools.vscode-docker) adds a sidebar view
of running containers and their logs, which is handy for watching `gateway`
and `redis` output side by side without juggling terminal panes.

## 6. Guided end-to-end walkthrough

With the gateway running and `ADMIN_API_KEY` set:

```bash
DEMO_ADMIN_KEY=<your ADMIN_API_KEY> make demo
```

Issues a key, sends a chat request, deliberately trips the rate limiter, and
pulls the resulting audit log back out — the fastest way to see the whole
system work end to end in one shot.

## 7. Load testing

```bash
brew install k6   # macOS; see k6.io/docs for other platforms
GATEWAY_URL=http://localhost:8000 CLIENT_KEY=<a key from /admin/keys> k6 run scripts/load_test.js
```

Run this while the Grafana dashboard is open (`:3000`) to watch request
rate, latency percentiles, and fallback behavior move in real time.
