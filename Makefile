.PHONY: up down logs test test-backend test-frontend lint migrate seed-pune \
	ingest-osm ingest-chargers ingest-ocm ingest-gov-chargers ingest-vahan-synthetic \
	ingest-population ingest-solar solar-coverage demand seed-corridor docker-clean \
	ingest-policy agent-evals ingest-discom ingest-ocpi calibrate onboard-region \
	export-bundle import-bundle

INGEST = docker compose exec api python -m app.ingestion.cli
OSM_PBF ?= /data/osm/mumbai_pune_full.osm.pbf

up:
	docker compose up -d --build --renew-anon-volumes

down:
	docker compose down

logs:
	docker compose logs -f

test: test-backend test-frontend

test-backend:
	docker compose run --rm api pytest

test-frontend:
	cd frontend && npm run test -- --run

lint:
	docker compose run --rm api ruff check .
	docker compose run --rm api mypy app
	cd frontend && npm run lint

migrate:
	docker compose exec api alembic upgrade head

# Everything needed for the Phase 2 Pune dataset, in dependency order.
seed-pune: migrate ingest-osm ingest-chargers ingest-ocm ingest-vahan-synthetic ingest-population ingest-solar demand seed-corridor ingest-policy

ingest-osm:
	$(INGEST) osm $(OSM_PBF)
	# Martin resolves its tile sources at startup; on a fresh database the OSM tables
	# didn't exist yet, so it has to pick them up again.
	docker compose restart martin

ingest-chargers:
	$(INGEST) osm-chargers
	$(INGEST) fuse

# OCM staging replaces every OCM record, so fetch the widest region (it contains the others).
OCM_REGION ?= mumbai_pune
ingest-ocm:
	$(INGEST) ocm --region $(OCM_REGION)
	$(INGEST) fuse

# Usage: make ingest-gov-chargers CSV=/data/raw/bee/export.csv
ingest-gov-chargers:
	$(INGEST) gov-chargers $(CSV)
	$(INGEST) fuse

ingest-vahan-synthetic:
	$(INGEST) vahan --synthetic

# 1km downloads in about a minute; GRID=100m is better but ~0.8 GB from a slow,
# non-resumable server (docs/methodology/population-h3.md).
GRID ?= 1km
ingest-population:
	$(INGEST) population --year 2025 --grid $(GRID) $(if $(REGION),--region $(REGION))

# Solar resource for the energy engine (Phase 8): NASA POWER hourly, one year.
SOLAR_YEAR ?= 2024
# DISCOM asset ratings/loadings (Phase 10); template in data/templates/.
ingest-discom:
	$(INGEST) discom $(CSV) --discom $(DISCOM)

# One operator's OCPI 2.2.1 feed (config/ocpi_operators.yaml + OCPI_TOKEN_<ID> in .env).
ingest-ocpi:
	$(INGEST) ocpi $(OPERATOR)

# LightGBM residual calibration against observed utilisation; logs to MLflow (:5050).
calibrate:
	$(INGEST) calibrate

# Onboard a registered region (Phase 12): docs/onboarding-regions.md. DRY=1 prints the plan.
onboard-region:
	docker compose exec api python -m app.ingestion.onboard $(REGION) --pbf $(PBF) $(if $(DRY),--dry-run,)

# Policy PDFs (data/raw/policy, config/policy/documents.yaml) -> policy.search corpus.
ingest-policy:
	$(INGEST) policy

# Golden agent evals (tests/golden/agent): calls the Claude API, needs ANTHROPIC_API_KEY.
agent-evals:
	docker compose exec api python tests/golden/agent/run.py $(if $(CASES),--cases $(CASES),)

ingest-solar:
	$(INGEST) solar --year $(SOLAR_YEAR)

# Google Solar API coverage check (verification item 3); needs a key and cost cap.
solar-coverage:
	$(INGEST) solar-coverage --points 20

# Demand forecast, allocation and gap (Phase 3). MULTIPLIER=1.3 adds a what-if scenario.
MULTIPLIER ?= 1.0

demand:
	$(INGEST) demand --multiplier $(MULTIPLIER) $(if $(REGION),--region $(REGION))

# Mumbai-Pune corridor region (Phase 9): its cells, population and demand runs.
seed-corridor:
	$(MAKE) ingest-population REGION=mumbai_pune GRID=1km
	$(MAKE) demand REGION=mumbai_pune
	$(MAKE) demand REGION=mumbai_pune MULTIPLIER=1.3

# Frees disk: stops the stack and removes this project's images and build cache.
docker-clean:
	docker compose down --rmi local
	docker builder prune -f

# Portable bundle of images + database + volumes (docs/RUNNING.md section 11).
export-bundle:
	scripts/export-bundle.sh $(OUT)

import-bundle:
	scripts/import-bundle.sh $(BUNDLE)
