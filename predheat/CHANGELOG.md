# Changelog

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
