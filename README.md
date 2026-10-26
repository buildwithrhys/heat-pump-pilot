# PredHeat

Self-learning heat pump and home energy optimiser. Home Assistant proof of concept with a path to a standalone commercial product.

PredHeat learns how a property behaves thermally, works out how efficiently its heat pump produces heat, prices every kWh at the rate in force when it was used, and (from Phase 4) calculates the cheapest safe way to meet comfort and hot water needs against time-of-use tariffs and weather forecasts.

Pilot installation: Vaillant aroTHERM pro 7 kW (VRC720 controller, VR 940f gateway), Octopus Intelligent Go, OpenWeather, Solcast, Huawei PV and battery, Home Assistant 2026.10.

## Status

Phase 0 (technical discovery) is complete and Phase 1 (working data platform) is implemented as a skeleton: discovery, collection, storage, pricing engine, energy accounting, a simulated home, an API and a basic dashboard. Everything is read-only. There is no code path that writes to heating equipment. See `ROADMAP.md`, `SAFETY.md` and `docs/phase0-capability-report.md`.

## Install as a Home Assistant add-on

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbuildwithrhys%2Fpredheat)

Click the badge, or add `https://github.com/buildwithrhys/predheat` under Settings, Add-ons, Add-on store, Repositories. Install PredHeat, start it, and open it from the sidebar. The add-on reads Home Assistant through the Supervisor, so no access token is needed. Options are documented in `predheat/DOCS.md`.

The badge needs the repository to be public. Until then, install from a local folder: copy `predheat/` to `/addons/predheat` on the Home Assistant host and refresh the add-on store.

## Repository layout

- `repository.yaml` marks this as a Home Assistant add-on repository.
- `predheat/` is the add-on: `config.yaml`, `Dockerfile`, `DOCS.md`, and inside it the Python package (`src/`), tests and the React dashboard (`frontend/`). The add-on folder is also the Docker build context, which is why the application lives there.
- `docs/` and the top-level `*.md` files are the design documents.

## Quick start outside Home Assistant (simulated data, no hardware needed)

```bash
cd predheat
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest                                  # 33 tests
predheat bootstrap                      # creates data/predheat.sqlite, backfills 14 simulated days
predheat serve                          # API on http://127.0.0.1:8750 (docs at /docs)
cd frontend && npm install && npm run dev   # dashboard on http://localhost:5173
```

The default `PREDHEAT_DATA_MODE=simulated` runs a synthetic house with known physics (`/api/ground-truth`), an Intelligent Go style tariff with random smart-charge dispatches, weather and solar. Switch to live telemetry with:

```bash
cp .env.example .env         # then set PREDHEAT_DATA_MODE=home_assistant, PREDHEAT_HA_URL, PREDHEAT_HA_TOKEN
predheat discover            # writes docs/discovery/latest.json and .md from your real entities
predheat bootstrap --backfill-days 30
predheat serve
```

Or with Docker, from the repository root: `docker compose up --build`, which builds the same image as the add-on and serves the dashboard on port 8750.

`predheat discover` needs only a long-lived access token with read access. It does not call any service that changes equipment.

## Repository layout

```
src/predheat/
  config.py            environment-driven settings (no secrets in code)
  timeutils.py         UTC and Europe/London helpers, 46/48/50 slot days
  domain/              canonical signals, quality and provenance, pydantic schemas
  db/                  SQLAlchemy models and session (SQLite now, PostgreSQL later)
  integrations/
    base.py            provider Protocols (telemetry, tariff, weather, solar, control)
    homeassistant/     HA client, capability discovery, HA-backed providers
    simulated/         synthetic home, tariff, weather, solar
  pricing/             tariff rate books, IOG dispatch classification, costing
  analytics/           counters to half-hourly intervals, category allocation, reconciliation
  model/               1R1C building model: simulate and fit (Phase 3 foundation)
  service/             platform wiring, collector, repository, reporting queries
  api/                 FastAPI app (read-only; /api/control is blocked)
  cli.py               predheat serve | discover | bootstrap | costs | fixtures
frontend/              React + TypeScript dashboard (Vite)
tests/                 pytest suite and fixtures (anonymised pilot entities, simulated data)
docs/                  phase 0 capability report, risk register, decisions
```

## Documents

- `ARCHITECTURE.md` components, interfaces, decisions, repository plan
- `ROADMAP.md` phases, milestones and exit criteria
- `INTEGRATIONS.md` what each integration exposes on the pilot, limits and licensing
- `DATA_MODEL.md` units, provenance, schema
- `SAFETY.md` control boundaries and fail-safe rules
- `VALIDATION.md` how model accuracy and savings are measured
- `docs/phase0-capability-report.md` findings from the live discovery of 25 October 2026
- `docs/risk-register.md` integration risks and assumptions

## Configuration

All settings are environment variables prefixed `PREDHEAT_` (see `.env.example`). Credentials are never logged. The API binds to localhost by default.

## Licence

Proprietary, coLaunch Limited. PredHeat is an independent implementation; it does not reuse Predbat source code.
