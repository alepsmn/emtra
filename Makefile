.PHONY: setup lint test it accept up down demo-v1 baseline experiments demo-final dbt calibrate it-resilience docs passenger

COMPOSE = docker compose -f infra/docker-compose.yml

setup:
	uv sync

lint:
	uv run ruff check .
	uv run mypy contracts emtra_common

# unit tests only (no Docker); also runs the acceptance tests that need none
test:
	uv run pytest tests/unit tests/acceptance -q -m "not integration"

it:
	$(COMPOSE) up -d --wait --wait-timeout 90
	uv run pytest tests -q -m integration

# usage: make accept WP=03
accept:
	@test -n "$(WP)" || { echo "usage: make accept WP=03"; exit 2; }
	uv run pytest tests/acceptance/wp$(WP) -q

up:
	$(COMPOSE) up -d --wait --wait-timeout 90
	uv run python -m emtra_common.reference load
	uv run python -m infra.kafka.create_topics
	scripts/demo_v1.sh cloud

down:
	scripts/demo_v1.sh stop
	$(COMPOSE) down

demo-v1:
	scripts/demo_v1.sh

# ingestor grace, detector baseline, its residuals (+ hash) and the tuned L, h, z_max: always
# together (~1.5 min)
baseline:
	uv run python -m services.ingestor.tune_grace
	uv run python -m services.reliability_monitor.baseline
	uv run python -m services.reliability_monitor.tune_detectors

# usage: make experiments [SMOKE=1]
experiments:
	uv run python -m experiments $(if $(SMOKE),--smoke)

demo-final:
	@echo "TODO WP12"

dbt:
	mkdir -p data
	uv run dbt build --project-dir cloud/dbt --profiles-dir cloud/dbt

calibrate:
	@echo "TODO WP11"

# passenger page on http://127.0.0.1:8000; usage: make passenger [SOURCE=fogstore]
passenger:
	uv run python -m services.passenger_info $(if $(SOURCE),--source $(SOURCE))

it-resilience:
	@echo "TODO WP12"

docs:
	@echo "TODO WP13"
