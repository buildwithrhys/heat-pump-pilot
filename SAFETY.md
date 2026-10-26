# Safety and control boundaries

Control of heating equipment is treated as safety-sensitive. These rules bind the code, not just the documentation.

## Current state: read-only

Phases 0 to 4 contain no code path that writes to equipment. Specifically: the Home Assistant client has no generic `call_service`; the only service it invokes is `weather.get_forecasts`, which returns data. The API's `/api/control/*` routes return HTTP 403 unconditionally. `PREDHEAT_CONTROL_ENABLED` exists so that the guard can be tested, but nothing reads it to perform an action. The capability discovery lists writable Vaillant entities so that the Phase 5 design is grounded in reality; listing is not using.

## Pre-conditions for any active control (Phase 5)

Active control requires all of the following, checked at every action: the operating mode is `balanced` or `max_savings`; `PREDHEAT_CONTROL_ENABLED=true`; the control adapter's `ownership_verified()` is true, which means EnergyPLUS and any EEBUS energy manager are disabled or a documented arbitration exists (the pilot reports `energy_manager_state` and `external_energy_management_activation`, which will be monitored); the target entity is in the documented capability list; fresh measurements exist (indoor temperature and heat pump state younger than 30 minutes); and the user has not engaged the manual override.

## What may be controlled, and what may not

Permitted targets once Phase 5 is authorised: zone room setpoint within the user's comfort band (via quick veto with a bounded duration or a time programme change), DHW setpoint within 40 to 60 degC and DHW boost, and nothing else without a further review. Never: heating curve, minimum flow setpoint, system off, away mode, legionella settings, backup heater settings, or any manufacturer protection. The compressor and flow temperature are not directly controllable on this installation and PredHeat will not emulate that by rapid setpoint changes.

## Comfort and equipment protection

User-defined comfort band enforced on every proposed slot. Minimum compressor runtime and off time, and a cap on setpoint changes per hour, to avoid cycling. Hot water hygiene: never suppress the legionella cycle; never let the cylinder sit below the user's minimum. Respect manufacturer limits reported by the controller (min and max setpoints).

## Fail-safe behaviour

If telemetry is stale, a control call fails, the optimiser stops responding, or the provider reports an error, PredHeat withdraws any active override (ends the quick veto) and leaves the manufacturer controls in charge. Manual override is immediate and persists until the user clears it. All proposed, blocked, executed, failed and reverted actions are appended to `control_execution_log` with reasons.

## Conflict prevention

Only one automation may own heating setpoints. While EnergyPLUS retains control, PredHeat remains in monitor or shadow mode. Predbat controls the battery, not the heat pump, and the EV is managed by Intelligent Octopus; PredHeat reads those systems and never writes to them.

## Credentials

Tokens and API keys are read from environment variables, held as `SecretStr`, never logged and never written to the database.


## Controlled live optimisation (from 0.2.0)

Control is implemented and off by default. It becomes possible only when three add-on options are set together: `control_enabled`, an operating mode of `balanced` or `max_savings`, and `control_climate_entity`. The add-on entry point forces control off if any of the three is missing.

The only write path is `predheat.integrations.homeassistant.control.HAControlAdapter.set_target`, which calls `climate.set_temperature` on that one entity. It is imported solely by `predheat.control.Controller`, which on every cycle checks, in order: configuration, a readable current target, no manual change since the last command (otherwise release and back off for `control_override_hours`), a model-based plan, building model confidence of at least 50%, an indoor reading under 30 minutes old, and an indoor temperature not already below the comfort band. Any failure while holding an offset restores the occupant's own setpoint. Changes are limited to `control_max_offset_c` (max 2 °C), at most one per 25 minutes. Every proposal, write, failure and release is appended to `control_execution_log`. The API has no endpoint that enables control or writes to equipment.
