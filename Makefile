.PHONY: install check lint typecheck audit test run migrate migrate-check up down demo ledger

install:
	pip install -r requirements-dev.txt

# Every check CI runs that doesn't need Docker, in one command. CI has
# steadily grown steps with no local equivalent, so "it passed locally"
# drifted away from "it will pass".
#
# The Docker-dependent CI steps — promtool on the alert rules, compose
# validation, the image build — are deliberately left out: they'd make this
# target fail whenever Docker happens not to be running, which is the fastest
# way to get people to stop running it.
check: lint typecheck audit migrate-check test
	@echo "All checks passed."

lint:
	ruff check .

typecheck:
	mypy

audit:
	pip-audit -r requirements.txt -r requirements-dev.txt

# --cov-branch counts each if/else edge, not just whether the line ran. A
# line like `if x: return` scores as covered when only the true side is
# ever taken, which is how an untested early-return hides.
#
# The floor tracks reality rather than sitting where it was first set: 92
# when branch coverage arrived, 97 once the real gaps were closed, 99 now
# that only one branch is left uncovered. A floor well below what the suite
# actually achieves is not a safety net — a whole module can rot out without
# the build noticing.
#
# 99 still allows for the Redis integration tests skipping locally, which is
# the only legitimate reason the number moves between environments.
test:
	pytest -q --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=99

run:
	uvicorn app.main:app --reload

# Same check CI runs: fails if app/db/models.py has drifted from the committed
# migrations. Worth having locally, since otherwise drift is only ever caught
# after pushing.
migrate-check:
	alembic check

# Read the lifetime spend ledger. The runbook tells whoever is on the end of
# a ProviderBudgetExhausted page not to clear the Redis key, since it is the
# only copy of the number — so there needs to be a sanctioned way to look at
# it that isn't redis-cli and a guess at the key name.
#
# Reads the same settings the gateway uses, so it reports what the gateway
# would enforce rather than what a different Redis happens to hold.
ledger:
	@python -c "import asyncio, sys; \
	from app.budget.dependency import provider_budget as b; \
	from app.routing.model_map import billable_providers as bp; \
	snap = asyncio.run(b.snapshot(bp())); \
	cap = b.cap_usd; \
	[print(f'{p:<12} spent \$${s:>8.4f}  of \$${cap:.2f}   remaining \$${max(0.0, cap-s):>8.4f}') for p, s in snap.items()]"

migrate:
	alembic upgrade head

up:
	docker compose up --build

down:
	docker compose down

demo:
	./scripts/demo.sh
