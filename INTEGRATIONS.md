# Integrations

What each integration exposes on the pilot installation (live inventory of 25 October 2026, Home Assistant 2026.10.4), what Heat Pump Pilot uses, and the limits. The full entity list for Vaillant is in `docs/phase0-capability-report.md`.

## Vaillant via mypyllant (Home Assistant custom integration)

Pilot hardware: aroTHERM pro (device 1), VWZ AI hydraulic unit with backup heater (device 0), VRC720 controller, VR 940f gateway, firmware 404.1.29, one heating circuit (direct), one zone, one DHW cylinder with circulation pump. 58 entities.

| Heat Pump Pilot signal | Entity suffix | Quality | Notes |
|---|---|---|---|
| indoor_temperature | `zone_1_circuit_0_current_temperature` | sensor | Single zone sensor (room stat). Humidity also available. |
| indoor_setpoint | `zone_1_circuit_0_desired_heating_temperature` | sensor | Manual mode setpoint 23 degC at discovery; time programme setpoint 29 degC looks like a placeholder and is worth checking in the app. |
| outdoor_temperature | `glendale_house_outdoor_temperature` | sensor | Controller sensor. 24 h average in firmware attributes. |
| flow_temperature, flow_temperature_setpoint | `circuit_0_current_flow_temperature`, `circuit_0_flow_temperature_setpoint` | sensor | 35.5 degC at 5.8 degC outdoor with curve 0.5. |
| return_temperature | none | unavailable | Not exposed. |
| heating_curve, min_flow_setpoint, heat_demand_limit_outdoor | `circuit_0_heating_curve` (0.5), `min_flow_temperature_setpoint` (20), `heat_demand_limited_by_outside_temperature` (18) | sensor | Also writable numbers. |
| hp_electrical_power | `device_1_arotherm_pro_current_power` | manufacturer estimated | Live W from the `mpc` endpoint; present on this installation (735 W during heating). |
| aux_electrical_power | `device_0_vwz_ai_current_power` | manufacturer estimated | 5 W idle. |
| hp_elec_heating_kwh, hp_elec_dhw_kwh | `device_1_arotherm_pro_consumed_electrical_energy_heating / _domestic_hot_water` | manufacturer estimated | Wh, hourly buckets, published 30 to 90 minutes after the hour. |
| hp_heat_heating_kwh, hp_heat_dhw_kwh | `..._heat_generated_heating / _domestic_hot_water` | manufacturer estimated | Thermal output, same cadence. |
| hp_env_heating_kwh, hp_env_dhw_kwh | `..._earned_environment_energy_*` | manufacturer estimated | Consistency check: heat = electricity + environment. |
| aux_elec_heating_kwh | `device_0_vwz_ai_consumed_electrical_energy_heating` | manufacturer estimated | Backup heater; 0.1 kWh total so far. |
| hp_on_off_cycles, hp_operation_hours | `device_1_arotherm_pro_on_off_cycles` (245), `operation_time` (404 h) | manufacturer estimated | Cumulative since commissioning. |
| hp_operating_state | `energy_manager_state` (HEATING), `circuit_0_state` | sensor | No compressor on/off or defrost flag. |
| dhw_tank_temperature, dhw_setpoint, dhw_operating_mode, dhw_boost | `domestic_hot_water_0_tank_temperature` (41), `_setpoint` (46), `_operation_mode` (Manual), `_boost` | sensor | Legionella day Friday, hysteresis 7 K, max loading 120 min (firmware attributes). |
| system_water_pressure | `system_water_pressure` | sensor | 1.2 bar. |

Derived locally by the integration: `heating_energy_efficiency` (4.4 home, 4.5 device) is heat generated divided by electricity consumed from daily totals, so it is a ratio of manufacturer estimates, not a measured COP.

Writable controls visible (never used by Heat Pump Pilot in Phases 0 to 4): climate zone target (quick veto or time programme, 7 to 35 degC, 0.5 step, presets boost, away, system off, ventilation boost), water heater (35 to 70 degC, Manual, Time Controlled, Off, Cylinder Boost), DHW boost switch, heating curve 0.1 to 5, min flow setpoint, heat demand limit, away mode. No flow temperature write, no compressor or power limit command.

Energy management: `energy_manager_state` is HEATING; firmware reports `external_energy_management_activation: false`; no EEBUS entities exist on this system (gateway is VR 940f). EnergyPLUS therefore appears to act cloud-side through Vaillant's own scheduling rather than through a local EEBUS energy manager. Treat it as a competing controller until proven otherwise; see `SAFETY.md`.

Limits: unofficial API, per-account quota with hours-long lockouts, login flow broken several times in 2025 and 2026, default poll 30 minutes, energy lag means today's figures are incomplete until the next day. History in Home Assistant begins September 2026 (installation). Unsuitable as the foundation for a commercial product; the official Vaillant Energy Management API (partner programme, free trial on application) is the route for Phase 7.

## Octopus Energy (BottlecapDave integration)

Account on Intelligent Octopus Go, import tariff `E-1R-INTELLI-VAR-24-10-29-K` (region K), export `E-1R-OUTGOING-VAR-24-10-26-K` at 12 p/kWh, standing charge 54.3619 p/day. Home Mini present (`current_demand`, `current_consumption`, `current_total_consumption`). 110 entities.

Used: `current_rate` and the `previous/current/next_day_rates` events (half-hour slots including `is_intelligent_adjusted`; 50 slots on 25 October 2026), `current_standing_charge`, `previous_accumulative_consumption` with 48 half-hour `charges` (last complete day, published late), `intelligent_dispatching` on the Hypervolt device with `planned_dispatches` (start, end, charge_in_kwh, source SMART), `completed_dispatches` (30-minute pieces with location AT_HOME, retained about 3 days) and `started_dispatches`. The Ohme device shows as a second, unavailable intelligent device and is ignored.

Rules: Heat Pump Pilot prices consumption at the slot rate from the rates events. A slot in the fixed 23:30 to 05:30 window is guaranteed off-peak. A slot inside a started or completed dispatch is confirmed off-peak. A slot inside a planned dispatch only is reported as planned and assumed; it is never counted as a realised saving. The integration's own `is_intelligent_adjusted` flag is retained as corroboration.

Limits: shared 100 calls per hour across all tools, consumption published with a lag of up to a day or more, no public API terms, commercial third-party access is a partner OAuth arrangement. Historic rates for alternative products need the direct REST API (Milestone 1.2).

## OpenWeatherMap (core integration)

`weather.weather_forecast` with hourly forecasts for 48 hours (temperature, apparent temperature, dew point, humidity, cloud cover, wind speed and gust in mph, precipitation probability, condition) and daily for 8 days, via the `weather.get_forecasts` service. Current conditions as sensors. The `openweathermap_forecast_*` sensors are legacy and unavailable. Heat Pump Pilot stores each forecast with its issue time. Wind speed is converted to m/s. Irradiance is not provided; cloud cover is a proxy only.

## Solcast (HACS integration)

One site: 10 kW AC, 8 kW DC, azimuth 114 degrees (ESE), tilt 35, loss factor 0.95. `forecast_today`, `tomorrow` and `day_3` to `day_7` carry `detailedForecast` with 30-minute `pv_estimate`, `pv_estimate10`, `pv_estimate90` in kW. API limit on this account is 50 calls per day (hobbyist default is 10). No GHI or GTI irradiance on the rooftop endpoints. For passive solar gain Heat Pump Pilot will use Open-Meteo irradiance (free non-commercial, licensable) and keep Solcast for PV generation.

## Huawei Solar (HACS integration, Modbus)

SUN2000-5KTL-L1 inverter, 15 kWh battery. Live: `power_meter_active_power` (grid, negative on import in this installation's sign convention, 11.2 kW import during the overnight EV and battery charge at discovery), `power_meter_consumption` and `power_meter_exported` (cumulative kWh), battery SOC, charge and discharge power and cumulative energy, working mode. Inverter PV sensors are unavailable overnight. Energy dashboard uses the Huawei meter for grid and `api_meet_your_maker_active_energy_forward` (FusionSolar) for PV. These feed whole-home accounting and the battery value split later; they are not needed for the heat pump model.

## Other relevant systems

Predbat is running with the Hypervolt (monitoring only, Intelligent Octopus controls charging), a Tesla via Teslemetry and a Nissan Leaf via Ohme. Drayton Wiser (two hubs, nine room climates) is offline since the heat pump install; if re-powered, its room temperature sensors would turn the thermal model multi-zone. The OilFox tank sensor is offline. There is no CT clamp or heat meter on the heat pump.

## Recommended optional metering

A CT clamp or Shelly EM on the heat pump supply (outdoor unit plus hydraulic unit) would give metered total heat pump electricity, allowing Vaillant's estimates to be validated and standby and defrost consumption to be separated. A heat meter on the primary flow would make COP measured rather than manufacturer estimated. Both are optional for Phases 1 to 4 and strongly recommended before claiming savings in Phase 5.
