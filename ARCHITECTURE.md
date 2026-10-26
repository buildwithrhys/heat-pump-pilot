# Architecture

## Goals that shape the design

PredHeat must be portable (the engine cannot depend on Home Assistant), honest (measured, estimated, modelled and forecast values never mix), safe (read-only until control ownership is proven) and reproducible (costs never change when a rate card is updated). The architecture is therefore layered around a canonical domain model with integrations at the edge, and every stored number carries a quality and a provenance.

## Components

The integration layer (`predheat.integrations`) contains providers that implement small Protocols: `TelemetryProvider` (measurements for mapped signals plus capability discovery), `TariffProvider` (rates, standing charges, dispatches), `WeatherProvider`, `SolarProvider` and a `ControlAdapter` that in this phase only describes capabilities. There are two families of implementation: Home Assistant backed providers that read the mypyllant, Octopus Energy, OpenWeatherMap, Solcast and Huawei Solar integrations, and simulated providers backed by a synthetic home with known physics. Direct API clients (Octopus REST and GraphQL, Open-Meteo, Vaillant Energy Management API) slot in behind the same Protocols later without touching the engine.

Capability discovery (`integrations/homeassistant/discovery.py`) is a pure function from a Home Assistant state dump to a `CapabilityReport`. It scores each entity against the signal catalogue's hints, device classes and units, excludes known distractors (vehicle temperatures, battery packs, legacy forecast sensors), classifies quality (metered, manufacturer estimated, sensor) and lists the writable controls it can see without using them. The report drives the sensor mapping table and the System Health page, and it is what the eventual onboarding wizard will present for confirmation.

Data collection and storage (`predheat.service` and `predheat.db`) run a collector that polls each provider on a timer, isolates failures per provider, logs data quality events and writes through idempotent upserts keyed on natural keys. SQLite is used now; the models avoid SQLite-only features so PostgreSQL is a connection string change. Forecasts are stored with their `issued_at`, rates with their `observed_at`.

The pricing engine (`predheat.pricing`) builds a `RateBook` per tariff from observed rate intervals, prices an energy interval pro rata across rate boundaries, flags gaps instead of pricing them at zero, pro-rates standing charges per local day and classifies Intelligent Go slots as guaranteed off-peak, confirmed dispatch (started or completed), planned dispatch (assumed, never counted as a saving) or peak.

Energy accounting (`predheat.analytics`) converts cumulative counters to half-hour intervals, handling resets, duplicates and coarse hourly buckets (marking resampled values as model derived), allocates heat pump electricity to space heating, hot water, auxiliary, standby and unclassified, and reconciles the categories to the total within a stated tolerance, exposing any residual rather than hiding it.

The building thermal model (`predheat.model`) is a single-zone 1R1C model with a regularised least-squares fitter that recovers the heat-loss coefficient, effective thermal capacity, solar aperture and internal gains, with a survey prior and a delta-method confidence interval. It is validated against the simulator's ground truth in the test suite. The heat pump performance model, the prediction and optimisation engine and the financial comparison engines are reserved packages that will reuse the same inputs.

The API (`predheat.api`) is FastAPI, read-only, with the `/api/control` namespace deliberately returning 403. The dashboard (`frontend/`) is React and TypeScript, mobile first, and only reads the API.

## Repository and application structure

A single repository with a Python package (`src/predheat`), a frontend (`frontend/`), tests and documents. The package boundary that matters is that `domain`, `pricing`, `analytics` and `model` import nothing from `integrations` or `api`. A later commercial backend can take those four packages unchanged and give them different providers and a different persistence layer.

## Integration interfaces

Every provider returns domain objects, never raw payloads. `Measurement` (signal, timestamp, value or text, quality, provenance, source reference), `RateInterval`, `StandingCharge`, `Dispatch`, `WeatherPoint` (with `issued_at`), `SolarPoint` and `CapabilityReport` are the complete contract. The signal catalogue in `domain/signals.py` is the data dictionary: each signal has a fixed unit, a kind (instant, cumulative or state), a default quality, discovery hints and unit conversions.

## Key decisions

SQLite with SQLAlchemy 2.0 was chosen for a single-property PoC, with the migration path to PostgreSQL kept open by avoiding dialect-specific features. Pydantic models sit at the boundaries and SQLAlchemy models in storage, rather than one shared class, because the two change for different reasons.

Half-hour slots in UTC are the unit of account. Local time is used only for day boundaries and the fixed off-peak window. Tests cover the 46 and 50 slot days.

Home Assistant long-term statistics are the history source. They give hourly means and end-of-hour counter states, which is why half-hour intervals derived from them are labelled model derived rather than metered.

The Vaillant energy figures are stored as manufacturer estimated, not metered. The pilot has no independent heat meter or CT clamp on the heat pump; the Octopus Home Mini and Huawei meter measure the whole home. Adding a dedicated CT clamp on the heat pump supply is the single most valuable metering improvement (see `INTEGRATIONS.md`).

The first learner is interpretable physics plus least squares, not a neural model. It makes identifiability failures visible (little temperature variation, little heating variation) and gives confidence intervals. More complex models are justified only once multiple indoor sensors exist.

The simulator is part of the product, not just a test aid. It lets tariff replay, optimisation and the dashboard be developed and demonstrated without equipment, and it provides ground truth for validating learners.

## Non-functional notes

Secrets come only from environment variables. The API has no write endpoints and binds to localhost. Polling intervals default to five minutes for Home Assistant, which is itself rate limited against Vaillant and Octopus; PredHeat never calls those services directly in this phase. Every provider failure becomes a data quality event visible on System Health. Timestamps are UTC in storage and rendered in the property's timezone.
