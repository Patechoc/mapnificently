# Mapnificently - development and run commands.
#
# Run `make` (or `make help`) to list everything. Variables can be overridden on the command
# line, e.g. `make serve PORT=9000` or `make reach CITY=moss FROM="Moss stasjon" MINUTES=45`.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# uv warns when an unrelated virtualenv is active; the project always uses .venv.
unexport VIRTUAL_ENV

UV  ?= uv
RUN := $(UV) run
APP := $(RUN) mapnificently

# Server
HOST ?= 127.0.0.1
PORT ?= 8000
export MAPNIFICENTLY_API_URL ?= http://$(HOST):$(PORT)

# Queries. FROM takes "lat,lon" or a place name; separate several start points with ";".
CITY    ?= oslo
FROM    ?= Jernbanetorget
AT      ?=
MINUTES ?= 30
MODE    ?= union
TOP     ?= 20
TO      ?= Oslo S
CHANGE  ?= 2
OUT     ?= $(CITY)-$(MINUTES)min.geojson
Q       ?= $(FROM)


# Expands FROM="a;b" into: --from "a" --from "b" (inside a recipe, needs bash).
define from_args
IFS=';' read -ra origins <<< "$(FROM)"; args=(); \
for o in "$${origins[@]}"; do args+=(--from "$$o"); done
endef

AT_ARG = $(if $(AT),--at $(AT)) --min-change $(CHANGE)

##@ Help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m [VAR=value ...]\n"} \
		/^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@printf "\n\033[1mVariables\033[0m (now)\n"
	@printf "  CITY=%-14s a city, or a region, e.g. CITY=moss  (names: make data-status)\n" "$(CITY)"
	@printf "  CITIES=%-12s several cities, for static: CITIES=\"oslo moss\"\n" "$(CITIES)"
	@printf "  FROM, TO            \"lat,lon\" or a place name; several starts: FROM=\"A;B\"\n"
	@printf "  AT=%s MINUTES=%s MODE=%s CHANGE=%s PORT=%s\n\n" "$(AT)" "$(MINUTES)" "$(MODE)" "$(CHANGE)" "$(PORT)"

##@ Setup

.PHONY: install
install: ## Install the app with all development tools into .venv
	$(UV) sync --all-extras

.PHONY: install-hooks
install-hooks: install ## Install the git pre-commit hooks
	$(RUN) pre-commit install

.PHONY: lock
lock: ## Update uv.lock to the newest allowed versions
	$(UV) lock --upgrade

##@ Quality

.PHONY: lint
lint: ## Lint with ruff
	$(RUN) ruff check .

.PHONY: lint-fix
lint-fix: ## Lint with ruff and apply safe fixes
	$(RUN) ruff check --fix .

.PHONY: format
format: ## Format the code with ruff
	$(RUN) ruff format .

.PHONY: format-check
format-check: ## Check formatting without changing files
	$(RUN) ruff format --check .

.PHONY: typecheck
typecheck: ## Type-check src/ with mypy (strict)
	$(RUN) mypy src/

.PHONY: security
security: ## Scan the code with bandit and the dependencies with pip-audit
	$(RUN) bandit -c pyproject.toml -r src/
	$(RUN) pip-audit

.PHONY: check
check: lint format-check typecheck test ## Run every check CI should run (lint, format, types, tests)

##@ Tests

.PHONY: test
test: ## Run unit tests (synthetic GTFS feed, no network)
	$(RUN) pytest

.PHONY: test-integration
test-integration: ## Run all tests, including those downloading real Entur data (~40 MB)
	$(RUN) pytest --run-integration

.PHONY: coverage
coverage: ## Run unit tests with a coverage report (HTML in htmlcov/)
	$(RUN) pytest --cov --cov-report=term-missing --cov-report=html

##@ Data

.PHONY: data-fetch
data-fetch: ## Download and prepare CITY's region (e.g. CITY=moss)
	$(APP) data fetch $(CITY)

.PHONY: data-fetch-all
data-fetch-all: ## Download and prepare every configured region
	$(APP) data fetch --all

.PHONY: data-refresh
data-refresh: ## Re-download CITY's region even if unchanged (e.g. CITY=bergen)
	$(APP) data fetch --force $(CITY)

.PHONY: data-status
data-status: ## Regions, their cities, dates and disk use (PATHS=1: where files are)
	$(APP) data status $(if $(PATHS),--paths)

.PHONY: data-clean
data-clean: ## Delete CITY's region and feeds only it uses; asks (CITY=… required)
	@if [ "$(origin CITY)" = "file" ] || [ -z "$(strip $(CITY))" ]; then \
		echo "usage: make data-clean CITY=<city or region>"; exit 2; fi
	$(APP) data remove $(CITY)

.PHONY: data-prune
data-prune: ## Delete data no region uses (old layouts, unused feeds); asks first
	$(APP) data prune

##@ Run

.PHONY: serve
serve: ## Run the API and web map on HOST:PORT
	$(APP) serve --host $(HOST) --port $(PORT)

.PHONY: dev
dev: ## Run the server with auto-reload on code changes
	$(APP) serve --host $(HOST) --port $(PORT) --reload

.PHONY: open
open: ## Open the web map in the browser (server must be running)
	@url="http://$(HOST):$(PORT)/"; \
	if command -v wslview >/dev/null; then wslview "$$url"; \
	elif command -v xdg-open >/dev/null; then xdg-open "$$url"; \
	elif command -v open >/dev/null; then open "$$url"; \
	else echo "open $$url in your browser"; fi

.PHONY: openapi
openapi: ## Write the OpenAPI schema to openapi.json
	$(RUN) python -c "import json; from mapnificently.api import create_app; \
	print(json.dumps(create_app().openapi(), indent=2))" > openapi.json
	@echo "wrote openapi.json"

##@ Static site (offline, e.g. GitHub Pages)

SITE   ?= site
DAYS   ?= 7
CITIES ?= $(CITY)
SITE_PORT ?= 8080

.PHONY: static
static: ## Export CITIES for DAYS days into SITE (e.g. CITIES="oslo moss" DAYS=3)
	$(APP) export-static $(CITIES) --days $(DAYS) -o $(SITE)

.PHONY: static-serve
static-serve: ## Serve SITE on SITE_PORT, like GitHub Pages would
	$(RUN) python -m http.server $(SITE_PORT) --bind $(HOST) --directory $(SITE)

##@ Queries (use `make serve` if it runs, otherwise answer locally)

.PHONY: cities
cities: ## List cities known to the API
	$(APP) cities

.PHONY: geocode
geocode: ## Search places in CITY (e.g. CITY=moss Q="Moss stasjon")
	$(APP) geocode $(CITY) "$(Q)"

.PHONY: reach
reach: ## Stops reachable from FROM in CITY within MINUTES (TOP rows shown)
	@$(from_args); \
	$(APP) reach $(CITY) "$${args[@]}" --minutes $(MINUTES) --top $(TOP) $(AT_ARG)

.PHONY: route
route: ## Fastest journey FROM → TO in CITY, leg by leg (e.g. CITY=moss TO=Sognsvann)
	@$(from_args); \
	$(APP) route $(CITY) "$${args[@]}" --to "$(TO)" $(AT_ARG)

.PHONY: isochrone
isochrone: ## Area reachable from FROM in CITY, as GeoJSON in OUT (MODE=union|intersection)
	@$(from_args); \
	$(APP) isochrone $(CITY) "$${args[@]}" --minutes $(MINUTES) --mode $(MODE) $(AT_ARG) -o "$(OUT)"

##@ Documentation

MEDIA_PORT ?= 8799
MEDIA_TOOLS = --no-project --python 3.12 --with playwright==1.48.0 --with pillow==11.3.0 --with numpy

.PHONY: readme-media
readme-media: ## Re-record the README's GIFs and screenshot into docs/media (needs fetched data)
	@test -x .venv/bin/mapnificently || $(UV) sync --all-extras
	@# Run the server binary itself (not through `uv run`), so stopping it stops the server.
	@.venv/bin/mapnificently serve --port $(MEDIA_PORT) >/dev/null 2>&1 & server=$$!; \
	trap 'kill $$server 2>/dev/null' EXIT; \
	for _ in $$(seq 60); do curl -sf http://127.0.0.1:$(MEDIA_PORT)/api/config >/dev/null && break; sleep 1; done; \
	$(UV) run $(MEDIA_TOOLS) python scripts/readme_media.py --base-url http://127.0.0.1:$(MEDIA_PORT) --out docs/media \
	  || { echo "If Chromium is missing: $(UV) run $(MEDIA_TOOLS) playwright install chromium"; exit 1; }

##@ Housekeeping

.PHONY: build
build: ## Build the wheel and sdist into dist/
	$(UV) build

.PHONY: clean
clean: ## Remove caches, coverage files and build output (keeps .venv and timetable data)
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} +
