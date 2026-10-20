.PHONY: help sync lint fmt typecheck test test-all check up down logs ps migrate scan kick backfill notify-test digest backtest simulate report verify-whitelist config

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
	@echo "Stack started. First scheduled scan runs after SNAPSHOT_INTERVAL_MINUTES; 'make kick' runs one now."

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) run --rm migrations

scan:  ## one scan inside the collector container
	$(COMPOSE) exec collector ballast scan

kick:  ## queue a scan now (beat otherwise starts after the first interval)
	$(COMPOSE) exec collector celery -A ballast.workers.celery_app call ballast.scan

backfill:  ## queue the matured-market history backfill
	$(COMPOSE) exec analytics celery -A ballast.workers.celery_app call ballast.backfill_history

notify-test:
	$(COMPOSE) exec notifier ballast notify-test

digest:
	$(COMPOSE) exec notifier ballast digest

backtest:
	$(COMPOSE) exec analytics ballast backtest

simulate:
	$(COMPOSE) exec simulator ballast simulate

report:
	$(COMPOSE) exec analytics ballast report

verify-whitelist:
	$(COMPOSE) exec collector ballast verify-whitelist

config:  ## validate compose file
	$(COMPOSE) config -q
