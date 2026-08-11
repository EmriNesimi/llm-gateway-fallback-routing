.PHONY: install lint typecheck test run migrate up down demo

install:
	pip install -r requirements.txt

lint:
	ruff check .

typecheck:
	mypy

test:
	pytest -q --cov=app --cov-report=term-missing

run:
	uvicorn app.main:app --reload

migrate:
	alembic upgrade head

up:
	docker compose up --build

down:
	docker compose down

demo:
	./scripts/demo.sh
