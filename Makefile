.PHONY: install lint test run migrate up down demo

install:
	pip install -r requirements.txt

lint:
	ruff check .

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
