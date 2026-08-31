.PHONY: install check lint typecheck audit test run migrate migrate-check up down demo

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
# The floor is 92 rather than 93 because the same code scores lower under
# the stricter measure — 92 against branches is a harder promise than 93
# against lines, not a relaxed one.
test:
	pytest -q --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=92

run:
	uvicorn app.main:app --reload

# Same check CI runs: fails if app/db/models.py has drifted from the committed
# migrations. Worth having locally, since otherwise drift is only ever caught
# after pushing.
migrate-check:
	alembic check

migrate:
	alembic upgrade head

up:
	docker compose up --build

down:
	docker compose down

demo:
	./scripts/demo.sh
