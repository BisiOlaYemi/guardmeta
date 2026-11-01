SHELL := /bin/sh

PYTHON ?= .venv/bin/python
PIP := $(PYTHON) -m pip
UVICORN := $(PYTHON) -m uvicorn
MODAL := .venv/bin/modal
MODAL_URL ?= https://yemiog--guardmeta-engine-fastapi-app.us-east.modal.run
MODAL_MODEL_ENDPOINT_ID ?= ep-FfSxv4LNVsCGmnAqPSBdeR
HOST ?= 127.0.0.1
PORT ?= 8080

.DEFAULT_GOAL := help

.PHONY: help venv install start dev demo test test-live check eval-live eval-treasury docker-build modal-auth modal-model modal-model-info modal-endpoints modal-secret deploy modal-smoke modal-logs

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create the local virtual environment
	python3 -m venv .venv

install: venv ## Install application and development dependencies
	$(PIP) install -r requirements-dev.txt

start: ## Start GuardMeta (HOST=127.0.0.1 PORT=8080)
	$(UVICORN) app.main:app --host $(HOST) --port $(PORT)

dev: ## Start GuardMeta with hot reload
	$(UVICORN) app.main:app --reload --host $(HOST) --port $(PORT)

demo: ## Start the zero-setup public demo locally
	DEMO_MODE=true $(UVICORN) app.main:app --reload --host $(HOST) --port $(PORT)

test: ## Run deterministic tests without API spend
	$(PYTHON) -m pytest -v

test-live: ## Run the live Meta Model API test
	$(PYTHON) -m pytest -v -m live

check: ## Compile, validate dependencies, and run deterministic tests
	$(PYTHON) -m compileall -q app tests
	$(PYTHON) scripts/check_ui.py
	$(PIP) check
	$(PYTHON) -m pytest -q

eval-live: ## Run the fixed E2E benchmark (URL=http://127.0.0.1:8080)
	$(PYTHON) scripts/evaluate.py --url $(or $(URL),http://127.0.0.1:8080) --output artifacts/evaluation.json

eval-treasury: ## Run the proof-carrying treasury benchmark
	$(PYTHON) scripts/evaluate_treasury.py --url $(or $(URL),http://127.0.0.1:8080) --output artifacts/treasury_evaluation.json

docker-build: ## Build the production Docker image
	docker build -t guardmeta .

modal-auth: ## Authenticate the Modal CLI
	$(MODAL) setup

modal-model: ## Create a billed Shared Endpoint for Meta Muse Spark Open weights
	$(MODAL) endpoint create --shared --model meta-models/Muse-Spark-Open-816B-A42B

modal-model-info: ## Show the configured open-weight Endpoint status and URL
	$(MODAL) endpoint info $(MODAL_MODEL_ENDPOINT_ID)

modal-endpoints: ## List the workspace's open-weight model endpoints
	$(MODAL) endpoint list

modal-secret: ## Securely sync .env into the named Modal Secret
	test -f .env
	$(MODAL) secret create --from-dotenv .env --force meta-hackathon-secret

deploy: ## Deploy GuardMeta to Modal
	$(MODAL) deploy modal_app.py

modal-smoke: ## Check public Modal health and readiness
	curl --fail --silent --show-error $(MODAL_URL)/health
	$(PYTHON) scripts/verify_readiness.py $(MODAL_URL)

modal-logs: ## Show the latest Modal application logs
	$(MODAL) app logs guardmeta-engine --tail 100 --timestamps
