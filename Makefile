.PHONY: help install check lint typecheck shellcheck audit test run migrate migrate-check up down demo ledger reconcile purge-audit

# Default target. A `## text` on the same line as a target is its help line.
help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/^([a-z-]+):.*## /  \1\t/' | column -t -s $$'\t'

install:  ## install runtime and dev dependencies
	pip install -r requirements-dev.txt

# Every check CI runs that doesn't need Docker, in one command. CI has
# steadily grown steps with no local equivalent, so "it passed locally"
# drifted away from "it will pass".
#
# The Docker-dependent CI steps — promtool on the alert rules, compose
# validation, the image build — are deliberately left out: they'd make this
# target fail whenever Docker happens not to be running, which is the fastest
# way to get people to stop running it.
check: lint typecheck audit migrate-check test  ## everything CI runs that needs no Docker: lint, types, audit, migrations, tests
	@echo "All checks passed."

lint:  ## ruff
	ruff check .

typecheck:  ## mypy
	mypy

# The same image and version CI uses, so a pass here is a pass there. Needs
# Docker, which is why it is not in `check`: that target is the one that
# must work with nothing but the venv.
shellcheck:  ## lint shell scripts with CI's exact shellcheck (needs Docker)
	git ls-files -z '*.sh' | xargs -0 docker run --rm -v "$$PWD:/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0

audit:  ## pip-audit for known-vulnerable dependencies
	pip-audit -r requirements.txt -r requirements-dev.txt

# --cov-branch counts each if/else edge, not just whether the line ran. A
# line like `if x: return` scores as covered when only the true side is
# ever taken, which is how an untested early-return hides.
#
# The floor tracks reality rather than sitting where it was first set: 92
# when branch coverage arrived, 97 once the real gaps were closed, 99 for a
# long while, and now 100 — every statement and every branch in app/ is
# exercised. A floor below what the suite achieves is not a safety net; a
# whole module can rot out without the build noticing.
#
# 100 is measured identically with and without a real Redis, so the Redis
# integration tests skipping locally no longer moves the number.
#
# The escape hatch is `# pragma: no cover` (or `no branch`) with a comment
# saying why, which this codebase already uses where a line is genuinely
# unreachable — app/providers/base.py and app/routing/fallback.py both do.
# Marking something unreachable is a claim a reader can check. Lowering the
# floor is not.
test:  ## pytest with the 99% branch-coverage floor
	pytest -q --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=100

run:  ## uvicorn with reload
	uvicorn app.main:app --reload

# Same check CI runs: fails if app/db/models.py has drifted from the committed
# migrations. Worth having locally, since otherwise drift is only ever caught
# after pushing.
#
# "Target database is not up to date" on the default SQLite usually is NOT
# drift. Starting the gateway creates its tables with create_all (see
# init_db), which bypasses Alembic, so the file has every table but no
# alembic_version row and `alembic current` comes back empty. `alembic stamp
# head` records the revision without re-running anything and this passes
# again. CI never hits it: its database is fresh and reached head by
# migration.
migrate-check:  ## fail if models have drifted from the migrations
	alembic check

# Read the lifetime spend ledger. The runbook tells whoever is on the end of
# a ProviderBudgetExhausted page not to clear the Redis key, since it is the
# only copy of the number — so there needs to be a sanctioned way to look at
# it that isn't redis-cli and a guess at the key name.
#
# Reads the same settings the gateway uses, so it reports what the gateway
# would enforce rather than what a different Redis happens to hold.
ledger:  ## print lifetime spend and headroom per provider
	@python -c "import asyncio, sys; \
	from app.budget.dependency import provider_budget as b; \
	from app.routing.model_map import billable_providers as bp; \
	snap = asyncio.run(b.snapshot(bp())); \
	cap = b.cap_usd; \
	[print(f'{p:<12} spent \$${s:>8.4f}  of \$${cap:.2f}   remaining \$${max(0.0, cap-s):>8.4f}') for p, s in snap.items()]"

# Cross-check the ledger against the audit log. The runbook tells the reader
# to do this before trusting a ProviderBudgetExhausted page or resetting the
# ledger, and until now that meant ad-hoc SQL against one store and redis-cli
# against the other. Exits 1 on a gap, so it can gate a deploy.
reconcile:  ## check the ledger against the audit log; 1 = gap, 2 = unreadable
	python -m scripts.reconcile

# Remove audit rows carrying one exact request_id, exporting a JSON copy of
# each first. Dry run unless APPLY=1. Exists for the case that happened: the
# suite wrote 150 rows of stub traffic into the production audit log, and
# reconcile could not go green until they were gone.
#
# Exit 0 means it ran (whether or not anything matched); 2 means the database
# could not be read, so the rows are still there.
#   make purge-audit ID=client-hung-up
#   make purge-audit ID=client-hung-up APPLY=1
purge-audit:  ## remove audit rows by exact ID=...; dry run unless APPLY=1
	python -m scripts.purge_audit_rows "$(ID)" $(if $(APPLY),--apply,)

migrate:  ## alembic upgrade head
	alembic upgrade head

up:  ## docker compose up --build
	docker compose up --build

down:  ## docker compose down
	docker compose down

demo:  ## the guided demo script
	./scripts/demo.sh
