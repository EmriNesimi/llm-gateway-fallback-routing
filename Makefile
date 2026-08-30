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

test:
	pytest -q --cov=app --cov-report=term-missing --cov-fail-under=93

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
