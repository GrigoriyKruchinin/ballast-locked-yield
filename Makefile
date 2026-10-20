.PHONY: help sync lint fmt typecheck test test-all check up down logs ps migrate scan backtest simulate report verify-whitelist

COMPOSE := docker compose -f docker-compose.local.yml
RUN := uv run

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1

sync:
	uv sync

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .

fmt:
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

typecheck:
	$(RUN) mypy

test:
	$(RUN) pytest -m "not integration and not fork"

test-all:
	$(RUN) pytest

check: lint typecheck test

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) run --rm migrations

scan:
	$(RUN) ballast scan

backtest:
	$(RUN) ballast backtest

simulate:
	$(RUN) ballast simulate

report:
	$(RUN) ballast report

verify-whitelist:
	$(RUN) ballast verify-whitelist
