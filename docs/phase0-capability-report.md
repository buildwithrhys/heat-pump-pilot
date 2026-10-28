# Phase 0 capability report

Read-only inventory of the pilot Home Assistant taken on 25 October 2026 at about 22:45 UTC, the evening of the autumn clock change. Nothing was changed. Entity identifiers that embed the account email are shortened here.

## Installation summary

Home Assistant 2026.10.4, Europe/London, 3,734 registered entities, 3,423 with state. Relevant integrations: mypyllant (58 entities), octopus_energy (110), openweathermap (29), solcast_solar (27), huawei_solar (140), fusion_solar (324), teslemetry (196), ohme (17), wiser (258, offline), oilfox (10, offline), plus Predbat (about 290 states, not in the registry).

Vaillant system: aroTHERM pro outdoor unit (device 1), VWZ AI hydraulic unit with condensing backup heater configuration (device 0), VRC720 controller, VR 940f gateway, firmware 404.1.29, system scheme 8, one direct heating circuit, one zone, one cylinder with circulation pump. Controller settings at discovery: heating curve 0.5, min flow setpoint 20 degC, heat demand limited above 18 degC outdoor, adaptive heating curve off, legionella protection Fridays, DHW hysteresis 7 K, DHW max loading time 120 min, bivalence points -20 degC, `external_energy_management_activation` false, `energy_manager_state` HEATING, `smart_photovoltaic_buffer_offset` 10.

Operating point at discovery: outdoor 5.8 degC (24 h average 9.6), room 23.6 degC at a manual setpoint of 23, humidity 50 percent, flow 35.5 degC against a setpoint of 36.0, cylinder 41 degC against 46, outdoor unit drawing 735 W, hydraulic unit 5 W, 404 compressor hours and 245 cycles since commissioning. Today's counters: 8.1 kWh electricity and 38.4 kWh heat for space heating (ratio 4.7), 1.1 kWh electricity and 4.8 kWh heat for hot water (ratio 4.4). The hourly statistics for the morning of 24 October show zero heating energy until midday, consistent with a mild day, and confirm that long-term statistics exist back to September 2026.

## Signal availability

| Signal | Status | Quality | Source |
|---|---|---|---|
| Indoor temperature, humidity, setpoint | available | sensor | zone 1 circuit 0 (one sensor only) |
| Outdoor temperature | available | sensor | controller sensor; OpenWeather as secondary |
| Flow temperature and setpoint | available | sensor | circuit 0 |
| Return temperature | unavailable | | not exposed |
| Heating curve, min flow, demand limit | available | sensor | circuit 0 (also writable) |
| Heat pump live power | available | manufacturer estimated | device 1 current_power |
| Hydraulic unit live power | available | manufacturer estimated | device 0 current_power |
| Electricity for heating and DHW | available | manufacturer estimated | hourly Wh buckets, 0.5 to 2 h lag |
| Heat generated for heating and DHW | available | manufacturer estimated | hourly Wh buckets |
| Environmental energy | available | manufacturer estimated | hourly Wh buckets |
| Backup heater electricity | available | manufacturer estimated | device 0, near zero so far |
| Compressor state, defrost | unavailable | | not exposed; infer from power and flow later, labelled |
| Cycles and run hours | available | manufacturer estimated | cumulative |
| Operating state | available | sensor | energy_manager_state, circuit state |
| Cylinder temperature, setpoint, mode, boost | available | sensor | DHW 0 |
| Import and export rates, standing charge | available | metered (tariff) | Octopus, 50 slots on 25 Oct |
| Smart charge dispatches | available | | Hypervolt device: planned, started, completed |
| Whole-home import (live and half-hourly) | available | metered | Home Mini and Huawei meter |
| PV and battery | available | metered | Huawei (inverter asleep overnight) |
| Weather forecast 48 h hourly | available | forecast | OpenWeather weather entity |
| PV forecast 30 min, 7 days | available | forecast | Solcast, 10 kW ESE 35 degrees, 50 calls/day |
| Irradiance | unavailable | | not on Solcast rooftop tier; Open-Meteo planned |

Minimum set for thermal modelling (indoor, outdoor, heating electricity, heating heat) is satisfied.

## Writable controls observed (not used)

Zone climate target 7 to 35 degC in 0.5 steps with presets boost, away, system off and ventilation boost; water heater 35 to 70 degC with modes Manual, Time Controlled, Off and Cylinder Boost; DHW boost switch; heating curve 0.1 to 5; min flow setpoint; heat demand limit; away mode dates; quick veto duration. No flow temperature, compressor or power-limit controls.

## EnergyPLUS and EEBUS

No EEBUS entities are present and the firmware reports external energy management inactive, so no local energy manager is paired. EnergyPLUS, if active on the account, acts through Vaillant's cloud and the controller's own schedules; its decisions would show up as setpoint and DHW changes Heat Pump Pilot did not make. Recommendation: keep Heat Pump Pilot in monitor mode, log every setpoint change observed with the `energy_manager_state`, and before Phase 5 either disable EnergyPLUS for the trial or document how its actions and Heat Pump Pilot's are arbitrated.

## Data quality observations

Vaillant energy is hourly and arrives late; Heat Pump Pilot resamples to half hours and labels the result model derived. The time programme setpoint of 29 degC while in Manual mode at 23 degC looks like a placeholder; switching the zone to time-controlled would apply it, so it should be checked in the app before any control experiments. The Ohme charger appears as a second unavailable intelligent device in Octopus; ignored. Wiser TRVs are all offline; re-powering them would provide nine room temperatures. The Huawei grid meter reports import as negative power; sign is normalised in the mapping.

## Recommendations carried into Phase 1

Request the Vaillant Energy Management API trial now, since approval takes time and it is the only commercially viable route. Fit a CT clamp on the heat pump supply before Phase 5. Add Open-Meteo for irradiance. Import Octopus half-hourly consumption as the whole-home metered series. Confirm the 29 degC time programme value. Consider re-powering the Wiser hubs for multi-zone data.
