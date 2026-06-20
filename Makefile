# LaunchGuard — Makefile
# Repo root: one level above this file
# Uses the existing ./venv (Python 3.12.3) — do NOT recreate it.
# Network installs may hang in the sandbox; all install targets use a 60s timeout.
#
# NOTE: This repo root path contains spaces. All shell invocations quote paths.

SHELL        := /bin/bash

# REPO_ROOT is set here; when passed to shell commands it must be quoted.
# Use the make variable in recipes as "$(REPO_ROOT)" (with shell quoting).
REPO_ROOT    := $(shell pwd)

PYTHON       := "$(REPO_ROOT)/venv/bin/python"
PIP          := "$(REPO_ROOT)/venv/bin/pip"
RUFF         := "$(REPO_ROOT)/venv/bin/ruff"
MYPY         := "$(REPO_ROOT)/venv/bin/mypy"
PYTEST       := "$(REPO_ROOT)/venv/bin/pytest"

COMPOSE_FILE := "$(REPO_ROOT)/infra/docker/docker-compose.dev.yml"

.PHONY: help verify install lint typecheck test db-up db-down db-logs clean

# ---------------------------------------------------------------
# help — list targets
# ---------------------------------------------------------------
help:
	@echo ""
	@echo "LaunchGuard — local dev targets"
	@echo "  make verify      Run full local-CI: lint + typecheck + pytest"
	@echo "  make install     pip install -r requirements.txt (60s timeout; may skip if network slow)"
	@echo "  make lint        ruff check launchguard/ eval/"
	@echo "  make typecheck   mypy launchguard/"
	@echo "  make test        pytest tests/ eval/"
	@echo "  make db-up       Start postgres+pgvector container (docker compose)"
	@echo "  make db-down     Stop and remove postgres+pgvector container"
	@echo "  make db-logs     Tail postgres container logs"
	@echo "  make clean       Remove pycache, .mypy_cache, .pytest_cache, .ruff_cache"
	@echo ""

# ---------------------------------------------------------------
# verify — canonical CI gate; calls local-ci.sh
# ---------------------------------------------------------------
verify:
	@bash "$(REPO_ROOT)/scripts/local-ci.sh"

# ---------------------------------------------------------------
# install — pip install with graceful timeout
# ---------------------------------------------------------------
install:
	@echo "[install] Attempting: pip install -r requirements.txt (timeout 60s)"
	@echo "[install] NOTE: pip installs may hang in constrained-network sandboxes."
	@echo "[install]       If this hangs, Ctrl-C and use locally cached wheels."
	@timeout 60 $(PIP) install -r "$(REPO_ROOT)/requirements.txt" \
		|| echo "[install] WARN: pip timed out or failed. Continuing with whatever is installed."

# ---------------------------------------------------------------
# lint — ruff only
# ---------------------------------------------------------------
lint:
	@if ! $(RUFF) --version >/dev/null 2>&1; then \
		echo "[SKIP] ruff not installed — run 'make install'"; exit 0; \
	fi; \
	TARGETS=""; \
	[ -d "$(REPO_ROOT)/launchguard" ] && TARGETS="$$TARGETS $(REPO_ROOT)/launchguard"; \
	[ -d "$(REPO_ROOT)/eval" ]        && TARGETS="$$TARGETS $(REPO_ROOT)/eval"; \
	if [ -z "$$TARGETS" ]; then \
		echo "[SKIP] lint — no source dirs (launchguard/ eval/) yet"; exit 0; \
	fi; \
	$(RUFF) check --select=E,F,W,I $$TARGETS

# ---------------------------------------------------------------
# typecheck — mypy only
# ---------------------------------------------------------------
typecheck:
	@if ! $(MYPY) --version >/dev/null 2>&1; then \
		echo "[SKIP] mypy not installed — run 'make install'"; exit 0; \
	fi; \
	if [ ! -d "$(REPO_ROOT)/launchguard" ]; then \
		echo "[SKIP] typecheck — launchguard/ does not exist yet"; exit 0; \
	fi; \
	$(MYPY) --python-executable $(PYTHON) --ignore-missing-imports --no-error-summary \
		"$(REPO_ROOT)/launchguard"

# ---------------------------------------------------------------
# test — pytest only
# ---------------------------------------------------------------
test:
	@if ! $(PYTEST) --version >/dev/null 2>&1; then \
		echo "[SKIP] pytest not installed — run 'make install'"; exit 0; \
	fi; \
	TESTS=""; \
	[ -d "$(REPO_ROOT)/tests" ] && TESTS="$$TESTS $(REPO_ROOT)/tests"; \
	[ -d "$(REPO_ROOT)/eval" ]  && TESTS="$$TESTS $(REPO_ROOT)/eval"; \
	if [ -z "$$TESTS" ]; then \
		echo "[SKIP] test — no test dirs (tests/ eval/) yet"; exit 0; \
	fi; \
	$(PYTEST) --tb=short -q $$TESTS

# ---------------------------------------------------------------
# db-up — start postgres+pgvector
# ---------------------------------------------------------------
db-up:
	@echo "[db-up] Starting postgres+pgvector..."
	@docker compose -f $(COMPOSE_FILE) up -d
	@echo "[db-up] Container started. DATABASE_URL in .env.example for connection string."
	@echo "[db-up] Postgres readiness: watch 'make db-logs' until 'ready to accept connections'."

# ---------------------------------------------------------------
# db-down — stop and remove containers + volumes
# ---------------------------------------------------------------
db-down:
	@echo "[db-down] Stopping postgres+pgvector..."
	@docker compose -f $(COMPOSE_FILE) down -v
	@echo "[db-down] Done."

# ---------------------------------------------------------------
# db-logs — tail postgres logs
# ---------------------------------------------------------------
db-logs:
	@docker compose -f $(COMPOSE_FILE) logs -f postgres

# ---------------------------------------------------------------
# clean — remove all stale build artefacts
# ---------------------------------------------------------------
clean:
	@echo "[clean] Removing pycache, .mypy_cache, .pytest_cache, .ruff_cache..."
	@find "$(REPO_ROOT)" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find "$(REPO_ROOT)" -name "*.pyc" -delete 2>/dev/null || true
	@find "$(REPO_ROOT)" -name ".mypy_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find "$(REPO_ROOT)" -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find "$(REPO_ROOT)" -name ".ruff_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "[clean] Done."
