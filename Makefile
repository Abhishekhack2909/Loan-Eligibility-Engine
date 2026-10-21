PYTHON ?= python

.PHONY: test pytest validate compile syntax compose-config up down bootstrap

TEST ?= uv run --project backend pytest tests/backend

test: pytest validate compile

pytest:
	$(TEST)

validate:
	$(PYTHON) local/validate_exports.py

compile:
	$(PYTHON) -m compileall -q local llm_stub backend/app

syntax: compile validate

compose-config:
	docker compose config

up:
	docker compose up --build

down:
	docker compose down

bootstrap:
	docker compose run --rm minio-bootstrap
