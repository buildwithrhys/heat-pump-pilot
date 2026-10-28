# Data model

## Units and conventions

Energy in kWh (electrical and thermal are never mixed in one column; thermal columns are named `kwh_thermal`), power in kW, temperature in degC, heat loss in W/K, thermal capacity in kWh/K, prices in pence per kWh including VAT, standing charges in pence per day including VAT, percentages 0 to 100. Native units are converted at ingest using the signal catalogue's `unit_scale` (Wh to kWh, W to kW, GBP/kWh to p/kWh, mph to m/s).

Timestamps are stored in UTC and rendered in the property's timezone (`Europe/London`). Half-hour slots are half-open `[start, end)` in UTC. Local days have 46, 48 or 50 slots.

## Quality and provenance

Every measurement, interval and cost row carries `quality` and `provenance`.

Quality values: `metered` (independent meter: smart meter, CT clamp, heat meter), `sensor` (physical temperature, pressure, humidity), `manufacturer_estimated` (appliance controller's own calculation, for example all Vaillant energy and COP figures), `model_derived` (Heat Pump Pilot calculation or allocation, including half-hour resampling of hourly buckets), `forecast`, `simulated`, `unavailable`.

Provenance values name the system that produced the value (`vaillant_mypyllant`, `octopus_ha_integration`, `openweather_ha_integration`, `solcast_ha_integration`, `huawei_solar_ha_integration`, `predheat`, `simulator`, `user`, plus direct API variants for later phases).

Reporting collapses quality into three labels: measured (metered or sensor), estimated (manufacturer estimated or model derived) and simulated. Forecasts are always shown as forecasts.

## Signal catalogue

`src/predheat/domain/signals.py` is the data dictionary. Each signal has a fixed unit, a kind (`instant`, `cumulative`, `state`), a default quality, discovery hints, a device class filter and unit conversions. Signals required before heat-loss learning can begin: indoor temperature, outdoor temperature, heat pump electricity for heating, heat delivered for heating.

## Tables

`property` holds location, timezone, comfort preferences and survey figures. `equipment` lists heat pump, cylinder, inverter, battery, chargers and meters. `integration_connection` records provider status without secrets. `sensor_mapping` is the confirmed link from a canonical signal to a native entity with its scale, quality, confidence and whether the user confirmed it.

`sensor_measurement` is the raw time series: one row per signal per timestamp per source, with value or text, quality, provenance and `ingested_at`. Unique on (property, signal, ts, source) so re-ingest is idempotent.

`weather_forecast` and `solar_forecast` keep `issued_at` and the valid time, unique per issue, so backtests can use the forecast that was available at decision time rather than the latest one.

`electricity_tariff`, `tariff_rate_interval` (tariff code, start, end, p/kWh, `is_intelligent_adjusted`, `is_capped`, provenance, `observed_at`) and `tariff_standing_charge` hold rate cards as observed. Rates are never overwritten by later versions of a product; a changed rate for the same slot updates the row and its `observed_at`, and the cost table records the `rate_observed_at` it used.

`smart_charging_dispatch` records planned, started and completed Intelligent Octopus windows with the time each was observed, so the difference between planned and realised cheap periods is auditable.

`energy_consumption_interval` and `thermal_energy_interval` hold half-hourly energy by category and end use, with quality, provenance and allocation method. Categories: `hp_elec_heating`, `hp_elec_dhw`, `aux_elec_heating`, `aux_elec_dhw`, `hp_standby`, `hp_unclassified`, `hp_total`, `grid_import`, `grid_export`, `pv`, `ev`, `household_other`. The heat pump categories must sum to `hp_total` within 0.01 kWh per slot; the residual is stored as `hp_unclassified`, never discarded.

`cost_calculation` is one row per category per interval per tariff per scenario (`actual`, `replay:<tariff>`, `optimised:<tariff>`), storing the kWh, the effective rate, the pence and the rate observation time, so any report can be reproduced.

`building_model_version` and `heat_pump_model_version` are append-only: each refit creates a new row with parameters, confidence interval, training window and metrics. `optimisation_run` stores each plan with its horizon, the model versions used, predicted and baseline cost and per-slot explanations. `control_execution_log` is the append-only audit log of proposed, blocked, executed, failed and reverted actions. `data_quality_event` records provider failures, stale data, resets and gaps.

## Handling imperfect data

Missing data leaves gaps rather than interpolated values; costing flags incomplete intervals. Delayed updates are normal (Vaillant energy lags up to two hours, Octopus consumption up to a day); daily reports show a completeness indicator. Duplicate readings are de-duplicated on ingest. Counter resets are detected as negative deltas and flagged. Retrospective corrections (for example Octopus re-publishing a day) update the interval rows and append a data quality event rather than silently changing history.
