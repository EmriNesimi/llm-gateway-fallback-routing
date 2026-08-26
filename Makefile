.PHONY: install lint typecheck audit test run migrate up down demo

install:
	pip install -r requirements.txt

lint:
	ruff check .

typecheck:
	mypy

audit:
	pip-audit -r requirements.txt

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
