# Changelog

## 0.2.4

- Every tab passed against the "no unanswered questions" rule: each number carries its unit and period in plain words, technical units are translated with the technical form in a tooltip, badges say why they are there, unknown states say what they are waiting for, and every technical term has a tooltip drawn from one glossary. The list of what changed and why is in `docs/ui-audit-2026-10-26.md`; the rule itself is in `CLAUDE.md` and governs future UI work.
- Forecast legend speaks plainly (cheap hours, confirmed EV charging slot, planned slot that may move, standard rate); planner notes describe your house in kW and hours rather than W/K.
- System health: status pills read live / found, not updating / not found; data quality reads measured, metered, appliance estimate, PredHeat estimate, forecast.

## 0.2.3

- Fixed: whole-home import still showed a cumulative artefact (25,680 kWh in a day). Two root causes, both now guarded so they cannot regress. First, hourly rows from Home Assistant's long-term statistics can sit on a different baseline from the live sensor; they were stored under the same source and differenced against live readings, so the baseline gap became "consumption". Statistics rows now carry their own source tag, each source is differenced on its own and slots are merged with live readings preferred, and existing rows are re-tagged once on start. Second, there was no bound on how fast a counter may rise; any rise above 25 kWh per hour is now a discontinuity that contributes nothing. A domestic import above 150 kWh a day or 1,000 kWh a week is shown as "Data looks wrong, see System health" on the Overview, the daily table, the energy flow and the Tariffs tab instead of a number. A new `/api/samples?signal=` endpoint shows exactly what the accounting sees.
- Fixed: saving the add-on configuration failed on existing installs ("Missing option control_enabled"). The Configuration tab now has two optional options: collection interval and log level.
- Everything a person changes now lives on the Settings tab: Allow control is a toggle with a confirmation dialog listing the four preconditions and whether each is met; the climate entity is a dropdown of the climate entities PredHeat discovers, with the myVAILLANT zone preselected when there is exactly one; the operating mode is four explained cards; fields save as you change them; one "Reset all to defaults". Every gate change is written to the control log.
- Upgrade keeps your settings: values from the old Configuration tab (operating mode, gas price, boiler efficiency, offset, allow control, climate entity) are carried into the Settings tab once, with one log line per value.
- Stale badges judged by when PredHeat last read the entity successfully, not when its value last changed, so held states such as HEATING are no longer flagged; the tooltip shows the age. Unmapped signals show no leftover values.
- "Your house" wording: no numbers while waiting, fuller efficiency labels, the 7 °C figure only with readings near 7 °C and the −2 °C figure only with readings below 2 °C.
- Energy costs: period presets including this month, last month, this year and custom dates; rows by day, week or month; CSV download. Per-card "estimated" badges replaced by one line under the heading.
- Signal mapping can be changed on System health: pick from the candidates PredHeat scored, or go back to automatic.
- History on first start now covers 60 days (chunked), later starts top up two days; Octopus half-hourly smart meter data is used as the whole-home series when present; readings older than 90 days are thinned to hourly once a day.

## 0.2.2

- "Your house" rebuilt. Heat loss is quoted the way a survey quotes it, in kW at a regional design outdoor temperature taken from your home location (South Wales -3 °C; override in Settings), with the likely range, the surveyed figure beside it when you enter one, and the margin against the heat pump's rated output. Below 30% confidence every model card shows "Waiting for data" with what it is waiting for, how far along it is and when to expect a figure; no numbers, ranges or derived values are printed. The time constant is replaced by a cooling rate at 5 °C outside. Efficiency is the observed average COP over the period with its outdoor range, fitted separately for space heating and hot water, with the 7 °C figure only once there are readings near 7 °C. Every unit and term has a tooltip; W/K lives on the System health tab.
- Settings tab gains design outdoor temperature, surveyed heat loss and its outdoor temperature, and heat pump rated output. The survey is used as a prior for the model.
- Energy flow diagram scoped to the heat pump only: electricity by origin and free heat from outside air in, space heating, hot water and standby out, balanced with any residual shown as unaccounted; a plain-English ratio line and a one-sentence whole-house context; a sanity guard that refuses to draw implausible figures and points at the Signal mapping table.

## 0.2.1

- Fixed: whole-home import, costs, the Tariffs tab and the heat loss fit were inflated by lifetime meter readings. Hourly history rows were stamped at the start of the hour while carrying the end-of-hour value, so interleaved live readings looked like meter resets and the full register value was added each hour. History is now stamped correctly, a decrease only counts as a reset when the counter restarts near zero, and mis-stamped rows from earlier releases are removed on first start and re-fetched.
- The add-on Configuration tab now holds five options: data source, collection interval, log level, allow control, climate entity. Operating mode, setpoint offset, gas price and boiler efficiency moved to a new Settings tab in the dashboard and apply without a restart. Earlier values are migrated once. Location and time zone come from Home Assistant. The unused API key options are gone until a release uses them.
- Dashboard: wider layout, thousands separators, UK dates on the costs chart, dashes for days without data and a flag for days with missing prices, aligned forecast axes, heat loss shown as "still learning" until the range excludes zero, energy flow stays readable when the rest of the home dwarfs the heat pump, Tariffs tab identifies your current product correctly and shows your region, System health source column no longer wraps letter by letter and banners carry severity.
- Derived figures are cached between polls, so the Forecast and Tariffs tabs open at once.

## 0.2.0

- Model-based optimiser: a 24-hour dynamic programme on the fitted building and heat pump models, prices and weather. Explains each action, predicts the indoor temperature, and reports the saving against a plain thermostat on the same model. Takes over from the rule-based plan automatically once the building model is identified.
- Heat pump efficiency model: COP against outdoor temperature from your own data, with observed capacity. Shown on the Overview.
- Forecast accuracy on the System health tab: recorded plans are scored against measured indoor temperature once their horizon has passed.
- Controlled live optimisation, off by default: with "Allow control", an operating mode of balanced or max_savings and a named climate entity, PredHeat moves the room setpoint by small offsets. Hands back on stale data, manual change, cold house or shutdown. Every action is logged.
- Tariffs tab: replays your consumption on Intelligent Go, Go, Cosy and Flexible with indicative rates; editable through tariffs.yaml in the data directory.
- "Same heat on gas" comparison on the Overview and Energy costs tabs, with gas price and boiler efficiency options.
- Discovery fixes verified on the pilot: EV charger power, import and export rates, standing charge and off-peak now map to the right Octopus and Hypervolt entities; switches and energy counters can no longer be picked for power or boolean signals; duplicate "_2" entities are skipped.
- New look: light, airy, cyan accent, flat cards, pill tabs.

## 0.1.1

- Heat loss figure on the Overview, fitted from your own data, with range, cold-day kW, time constant and confidence. Saved as a building model version at most every six hours. New `/api/heat-loss` endpoint.
- "What PredHeat would do": a 24-hour rule-based plan (hot water, pre-heat, coast) with a reason per line, shown on the Overview and Forecast tabs in both modes. Shadow mode records every plan.
- Energy flow diagram on the Overview (today) and Energy costs (chosen period).
- Energy costs table and chart use UK date formatting.
- Add-on icon and logo, option names and descriptions on the Configuration page.
- Documentation rewritten for users.

## 0.1.0

- First add-on release. Phase 0 discovery and Phase 1 data platform.
- Reads Home Assistant through the Supervisor proxy, no long-lived token needed.
- Dashboard served through ingress from the add-on sidebar panel.
- Read-only: active control is disabled and cannot be enabled from the add-on.
