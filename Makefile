.DEFAULT_GOAL := help
SHELL := /bin/bash
.ONESHELL:

UV ?= uv
DBT := $(UV) run dbt --project-dir transform --profiles-dir transform
SCALE ?= 1.0
NPM ?= npm

.PHONY: help
help:  ## Print the targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------

.PHONY: install
install:  ## Sync the Python workspace and the web dependencies
	$(UV) sync --all-extras
	cd web && $(NPM) ci

.PHONY: doctor
doctor:  ## Report what this machine can and cannot do, before a build fails
	$(UV) run cityflow doctor

# ----------------------------------------------------------------------------
# The pipeline. Each target is idempotent and resumable.
# ----------------------------------------------------------------------------

.PHONY: zones
zones:  ## Build the zone geometry and dim_zone from the TLC shapefile
	$(UV) run cityflow zones

.PHONY: ingest
ingest:  ## Ingest the configured window (backend from config/cityflow.yml)
	$(UV) run cityflow ingest --scale $(SCALE)

.PHONY: ingest-tlc
ingest-tlc:  ## Ingest the real published TLC parquet
	CITYFLOW_BACKEND=tlc $(UV) run cityflow ingest

.PHONY: ingest-synthetic
ingest-synthetic:  ## Ingest from the seeded generator, no network needed
	CITYFLOW_BACKEND=synthetic $(UV) run cityflow ingest --scale $(SCALE)

.PHONY: transform
transform:  ## Build and test the dbt warehouse
	$(DBT) seed
	$(DBT) build

.PHONY: publish
publish:  ## Build the shipped aggregate layer the browser queries
	$(UV) run cityflow publish
	$(UV) run cityflow manifest

.PHONY: pipeline
pipeline: zones ingest transform publish reconcile tableau docs  ## The whole warehouse, end to end

# ----------------------------------------------------------------------------
# The gates. Every one of these fails the build in CI.
# ----------------------------------------------------------------------------

.PHONY: lint
lint:  ## ruff, mypy strict, sqlfluff, and the em dash gate
	$(UV) run ruff check packages src scripts tests metrics
	$(UV) run ruff format --check packages src scripts tests
	$(UV) run mypy packages src
	$(UV) run sqlfluff lint transform/models transform/tests
	$(UV) run python scripts/check_no_em_dash.py

.PHONY: test
test:  ## The Python suite with an 80 percent coverage floor
	$(UV) run pytest --cov --cov-report=term-missing

.PHONY: reconcile
reconcile:  ## Run every metric from the warehouse and from the shipped layer
	$(UV) run cityflow reconcile

.PHONY: claims
claims:  ## Re-derive every published figure and diff it against the documents
	$(UV) run python scripts/check_published_numbers.py

.PHONY: size
size:  ## Fail if a shipped file has grown past the budget
	$(UV) run python scripts/check_shipped_size.py

.PHONY: metric-gate
metric-gate:  ## Fail if the dashboard computes a metric the layer defines
	$(UV) run python scripts/check_metric_layer.py

.PHONY: palette
palette:  ## Re-validate every colour scale in both modes
	@set -e
	node scripts/validate_palette.js \
	  "#0891B2,#D97706,#8B5CF6,#059669,#EF4444,#2563EB,#EC4899,#65A30D" \
	  --mode dark --surface "#0B0F14"
	node scripts/validate_palette.js \
	  "#0891B2,#B45309,#7C3AED,#047857,#DC2626,#1D4ED8,#DB2777,#4D7C0F" \
	  --mode light --surface "#FAFAFA"
	node scripts/validate_palette.js "#0891B2,#D97706,#8B5CF6" \
	  --mode dark --surface "#0B0F14" --pairs all
	node scripts/validate_palette.js "#0891B2,#B45309,#7C3AED" \
	  --mode light --surface "#FAFAFA" --pairs all
	node scripts/validate_palette.js \
	  "#155E75,#0E7490,#0891B2,#06B6D4,#22D3EE,#67E8F9" \
	  --mode dark --surface "#0B0F14" --ordinal
	node scripts/validate_palette.js \
	  "#083344,#155E75,#0E7490,#0891B2,#06B6D4,#22D3EE" \
	  --mode light --surface "#FAFAFA" --ordinal
	node scripts/validate_palette.js \
	  "#67E8F9,#22D3EE,#0891B2,#383835,#D97706,#B45309,#92400E" \
	  --mode dark --surface "#0B0F14" --diverging
	node scripts/validate_palette.js \
	  "#155E75,#0E7490,#0891B2,#F0EFEC,#D97706,#B45309,#92400E" \
	  --mode light --surface "#FAFAFA" --diverging

.PHONY: gates
gates: lint test reconcile claims size metric-gate palette  ## Every gate

# ----------------------------------------------------------------------------
# Documents. Rendered from templates against the manifest, never hand edited.
# ----------------------------------------------------------------------------

.PHONY: docs
docs:  ## Re-render every document from the measured manifest
	$(UV) run python scripts/check_published_numbers.py --write

# ----------------------------------------------------------------------------
# Web
# ----------------------------------------------------------------------------

.PHONY: web
web:  ## Build the static dashboard export
	cd web && $(NPM) run lint && $(NPM) run typecheck && $(NPM) run build

.PHONY: web-dev
web-dev:  ## Run the dashboard locally
	cd web && $(NPM) run dev

.PHONY: web-test
web-test:  ## Playwright smoke test against the built export
	cd web && $(NPM) run test:smoke

.PHONY: tableau
tableau:  ## Rebuild the three CSV extracts the Tableau companion reads
	$(UV) run python scripts/build_tableau_extracts.py

.PHONY: demo
demo:  ## Record the demo animation from the built export
	cd web && node tests/record-demo.mjs tests/demo-frames
	$(UV) run python scripts/build_demo_gif.py web/tests/demo-frames docs/demo.gif

.PHONY: bench
bench:  ## Measure every panel query: latency, bytes pulled, row groups skipped
	$(UV) run python scripts/bench_queries.py

.PHONY: clean
clean:  ## Remove the warehouse and build output, keep the shipped layer
	rm -rf warehouse raw transform/target transform/logs logs web/.next web/out
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

.PHONY: all
all: pipeline gates web web-test  ## Everything, in order
