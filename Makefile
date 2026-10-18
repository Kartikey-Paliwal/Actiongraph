# Everyday commands. Run `make help` to list them.

PY ?= .venv/bin/python

.PHONY: help install up down reset test test-ci bench run mcp check

help:
	@echo "install   create .venv and install requirements"
	@echo "up        start FalkorDB (docker compose)"
	@echo "down      stop FalkorDB"
	@echo "reset     reseed the synthetic graph"
	@echo "test      run the tests (graph tests skip if FalkorDB is down)"
	@echo "test-ci   run the tests the way CI does (a skip is a failure)"
	@echo "bench     run the benchmark"
	@echo "run       start the API and demo page on http://127.0.0.1:8000"
	@echo "mcp       start the MCP server on stdio"
	@echo "check     everything CI checks, before you push"

install:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements.txt

up:
	docker compose up -d

down:
	docker compose down

reset:
	$(PY) -m app.cli reset

test:
	$(PY) -m pytest -q

test-ci:
	REQUIRE_GRAPH=1 $(PY) -m pytest -q -rs

bench:
	$(PY) scripts/benchmark.py --iterations 20

run:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

mcp:
	$(PY) -m app.mcp_server

check: test-ci
	$(PY) scripts/benchmark.py --iterations 3
	sh -n deploy/lightsail-user-data.sh
	sh -n deploy/deploy.sh
	docker compose config -q
	SITE_ADDRESS=example.sslip.io docker compose -f deploy/docker-compose.prod.yml config -q
	docker build -q -t actiongraph-api:check . > /dev/null
	$(PY) -m app.cli reset > /dev/null
	@echo "all CI checks passed locally"
