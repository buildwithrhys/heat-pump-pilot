# Roadmap

Status at 0.3.0 (26 October 2026): everything in Phases 0 to 6 that can be built without live running time is built: mapping overrides, 60-day backfill, smart-meter half-hourly import, retention, Octopus API rates and Open-Meteo irradiance, period reports and CSV, the learned house and heat pump models, the 48-hour optimiser with backtesting, guarded control of the zone and the hot water with alternating-day trials, tariff replay with EV and battery scenarios and an annual estimate. What remains needs weeks on the pilot: the model exit criteria (0.5 °C), the trial's saving with a credible margin, and the Vaillant-specific checks (quick veto duration, EnergyPLUS or EEBUS ownership). Phase 7 is commercial work and is not planned.

Each phase ends with working software and a milestone report covering what was implemented, tested, works, remains unverified, and what blocks the next step. Live equipment control is impossible before Phase 5 and remains off by default afterwards.

## Phase 0: Technical discovery (complete, 25 October 2026)

Done: live read-only inventory of the pilot Home Assistant (3,734 registry entities), evaluation of mypyllant, Octopus Energy, OpenWeatherMap, Solcast and Huawei Solar integrations, EnergyPLUS and EEBUS review, external API and licensing research, data dictionary (`domain/signals.py`), architecture. Output: `docs/phase0-capability-report.md`, `docs/risk-register.md`, `INTEGRATIONS.md`.

Findings that change the plan: no compressor state, defrost flag or return temperature are exposed; energy is hourly and lags one to two hours; a live power figure is available; only one indoor temperature sensor is live; Vaillant's unofficial API is throttled and periodically broken, so the official Energy Management API trial should be requested now for later phases.

## Phase 1: Working data platform (skeleton delivered, hardening in progress)

Delivered: Home Assistant connector (REST and WebSocket, read-only), capability discovery and sensor mapping, Octopus (via HA) tariff, dispatch and standing charge ingestion, OpenWeather hourly forecasts and observations, Solcast 30-minute forecasts, SQLite storage with provenance, simulated-data mode, pricing engine, energy accounting, API, basic dashboard (Overview, Energy Costs, Forecast timeline, System Health), 27 automated tests.

Milestone 1.1 (next): run against the live pilot for 7 days. Confirm discovery mapping, backfill from long-term statistics since September 2026, verify hourly energy lag handling and midnight gaps, add Home Mini half-hourly consumption import as the whole-home metered series, add a sensor mapping override UI. Exit: System Health shows every mapped signal fresh for 7 consecutive days and daily heat pump kWh matches the myVAILLANT app within 2 percent.

Milestone 1.2: direct Octopus API client (rates for arbitrary products by region, consumption history), Open-Meteo irradiance provider, scheduled retention and compaction, Docker image.

## Phase 2: Energy analytics

Space heating and hot water accounting with reconciliation (started), tariff pricing with historic rate retention (started), day, week, month, year and custom reports, COP monitoring from aligned thermal and electrical energy, cost per kWh of useful heat, initial Heat Pump vs Gas comparison with configurable boiler efficiency and gas tariff, labelled as a modelled counterfactual. Exit: a month of reports that reconcile to Octopus billed consumption within tolerance and a documented split method where heating and hot water overlap.

## Phase 3: Self-learning thermal model

Survey prior, learned heat-loss coefficient with confidence interval, thermal capacity, solar gain estimator using Solcast or Open-Meteo irradiance and optional window orientation, heat pump efficiency model (COP against outdoor and flow temperature), forecast accuracy monitoring against held-out days, confidence score. The 1R1C fitter already recovers simulator ground truth within 4 percent for UA and 1 percent for capacity; the work here is robustness on real, lagged, single-sensor data. Exit: 48-hour indoor temperature forecast error under 0.5 degC RMSE on held-out days in shadow.

## Phase 4: Shadow optimisation

48-hour, 30-minute MPC formulated as a mixed-integer or convex programme (library selected after formulation; candidates are HiGHS via scipy or OR-Tools), hot water scheduler with hygiene constraints, explainable per-slot decisions, comparison against unmodified weather compensation, Predbat-style forecast charts, backtesting against stored forecasts. Exit: predicted versus actual electricity and temperature errors reported daily for a month; no unrealistic schedules.

## Phase 5: Controlled live optimisation

Only after Vaillant compatibility, control ownership (EnergyPLUS and EEBUS off or arbitrated) and safety review. Control adapter limited to documented capabilities (zone setpoint via quick veto or time programme, DHW setpoint and boost), explicit enable, comfort limits, minimum runtime and cycling protection, stale-data fallback, manual override, full audit log, alternating baseline and optimised trials with uncertainty reporting. Exit: statistically credible cost reduction without comfort loss, or an honest finding that there is none.

## Phase 6: Tariff intelligence

Historic replay of actual half-hourly consumption on Intelligent Go, Go, Cosy, Agile, fixed and flexible; optimised re-simulation per tariff; whole-home modelling including EV charging scenarios and battery; annual forecasting from history and representative weather; ranked results with ranges, eligibility and exit fees; monthly review.

## Phase 7: Commercial preparation

Official Vaillant Energy Management API, Octopus partner OAuth, licensed irradiance, multi-property, onboarding wizard, installer dashboard, credentials and GDPR, billing hypothesis. Pricing treated as a hypothesis to test.
