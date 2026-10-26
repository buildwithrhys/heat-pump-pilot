# PredHeat

PredHeat watches your heat pump, learns how your house holds heat, and works out the cheapest way to keep it warm on your tariff. Out of the box it only watches and advises. Control is a separate, explicit opt-in described below.

## Getting started

1. Install the add-on and click **Start**.
2. On the add-on page, switch on **Show in sidebar**. Home Assistant leaves this off by default for every add-on, which is why PredHeat does not appear in the sidebar on its own. You can also reach it from **Open Web UI**.
3. Open **PredHeat** and look at the **System health** tab first. It shows which entities were found for each signal and what is missing.

The first start discovers your entities and backfills a week of history, which takes a minute or two. Readings are then collected every five minutes.

## The dashboard

**Overview.** Live temperatures and heat pump state, today's costs split into space heating and hot water, what PredHeat would do next, your house's heat loss figure, and a diagram of where today's energy went.

**Energy costs.** Costs by day for the last 7 to 90 days, priced at the tariff rate in force in each half hour, with COP and cost per kWh of heat. The flow diagram covers the chosen period.

**Forecast.** The next 24 hours of advice with a reason for each line, and the inputs behind it: prices by certainty (guaranteed off-peak, confirmed and planned Intelligent Octopus dispatches, peak), the weather forecast and the solar forecast.

**Tariffs.** Your own consumption replayed on Intelligent Octopus Go, Go, Cosy and Flexible with indicative published rates, ranked by cost, next to what you actually paid. Add or correct tariffs by placing a `tariffs.yaml` in the add-on's data directory (`/addon_configs/<slug>/` or the `/data` folder inside the add-on).

**System health.** Data providers, integrations discovered, every signal with its source entity and freshness, forecast accuracy of recorded plans, control status, and recent data quality events.

The API that feeds the dashboard is documented at `/docs` on the same page address.

## The heat loss figure

PredHeat fits a single-zone building model to the last 14 days of indoor temperature, outdoor temperature and heat delivered for space heating. It reports:

- **Heat loss** in watts per degree of indoor-to-outdoor difference, with a range. The "about x kW" line is that figure at 21 °C inside and -2 °C outside, the usual design condition for UK heat pump sizing, so you can compare it with your installer's survey.
- **How quickly it cools**, the time constant in hours, and how much heat the fabric stores per degree.
- **Based on**, how many half-hours of data were usable and any caveats, such as too little temperature variation to pin the number down.

It needs at least one full day of data before it reports, and the range narrows as days accumulate and weather varies. Mild weeks with little heating give wide ranges; cold snaps tighten them.

## What PredHeat would do

Until the building model is identified, the plan is rule-based: hot water at the end of the cheapest window, a pre-heat before expensive periods when the forecast says heating is needed, and a coast through them. Every line explains itself and says it is rule-based.

Once the heat loss figure reaches 30% confidence, the model-based optimiser takes over. For every half hour of the next 24 it chooses how hard to run the heat pump so that the electricity bill is lowest while the indoor temperature stays inside a comfort band of one degree either side of your setpoint and the house ends the day no colder than the setpoint. It uses the fitted heat loss and thermal capacity, the heat pump's COP at the forecast outdoor temperature, and the half-hourly prices. The Forecast tab shows the predicted indoor temperature for the plan and for a plain thermostat, and the saving between the two. Hot water is scheduled into the cheapest slots per unit of delivered heat before the morning.

In **shadow** mode each plan is saved. Once its horizon has passed, the model's prediction of indoor temperature is scored against what the sensor measured, and the result appears on the System health tab. The target is 0.5 °C RMSE.

## Letting PredHeat control the heating

Control is off by default and needs three things: **Allow control** and the **climate entity** for the heating zone on the add-on Configuration tab, and an operating mode of **Balanced** or **Max savings** on the dashboard's Settings tab. If any one is missing, nothing is ever written. The Configuration tab needs a Home Assistant admin and restarts the add-on, which is deliberate: it is the interlock. The mode is a runtime choice you can change at any time, and switching away from a controlling mode hands the setpoint back immediately.

When all three are set, on every collection cycle PredHeat reads the current target temperature of the climate entity and, if the plan says to pre-heat or coast right now, moves it up or down by the configured offset (balanced: the offset; max_savings: one and a half times it, capped at 2 °C). Otherwise it leaves your own setpoint in place. It hands the setpoint back and logs why when:

- the readings are stale (indoor temperature older than 30 minutes),
- the building model is below 50% confidence or the plan is not model-based,
- you change the thermostat yourself (it then waits four hours before acting again),
- the house is already below the comfort band,
- the add-on stops.

Every proposal, change, failure and release is written to the control log, and the System health tab shows what it is holding right now. Suggested sequence: run in shadow for a week, check the heat loss confidence and forecast accuracy, then switch to balanced.

## Settings: where each one lives

**Add-on Configuration tab** (install-time and safety; saving restarts the add-on):

| Option | Default | Meaning |
|---|---|---|
| Data source | `home_assistant` | Live data from this instance, or `simulated` for a synthetic house to explore the dashboard. |
| Collection interval | 300 s | How often readings, prices and forecasts are refreshed. |
| Log level | `info` | Use `debug` only while diagnosing a problem. |
| Allow control | off | The safety interlock. See "Letting PredHeat control the heating". |
| Climate entity to control | empty | The one entity PredHeat may write to. |

**Settings tab in the dashboard** (apply immediately, no restart, stored in the add-on's data directory):

| Setting | Default | Meaning |
|---|---|---|
| Operating mode | Monitor | Monitor, Shadow, Balanced or Max savings. The controlling modes are greyed out until Allow control and a climate entity are set in the Configuration tab. |
| Maximum setpoint offset | 1.0 °C | How far PredHeat may move your setpoint either way. |
| Gas unit price, boiler efficiency | 6.24p, 0.85 | For the "same heat on gas" comparison. |

The home location and time zone come from Home Assistant. Values you had set for the moved options in an earlier release are copied into the Settings tab once on the first start after upgrading.

## Safety

PredHeat reads Home Assistant through the Supervisor with the token Home Assistant issues to the add-on, so there is no long-lived access token to create or store. Exactly one module can write to Home Assistant, and it can do exactly one thing: set the target temperature of the single climate entity you name. It is only loaded when control is configured, every write goes through the guard chain above, and the API cannot enable control: the `/api/control` endpoints always answer 403. See `SAFETY.md` in the repository.

## Data

The database and discovery reports live in the add-on's `/data` directory. It survives restarts and updates and is included in Home Assistant backups.

## Troubleshooting

- **Not in the sidebar.** Switch on **Show in sidebar** on the add-on page.
- **"Learning" instead of a heat loss figure.** Fewer than 24 hours of usable indoor, outdoor and heat data so far, or a signal is missing. Check the System health tab.
- **Hot water or heating signals missing.** Discovery matches entity names from the pilot integrations (myVAILLANT, Octopus Energy, OpenWeatherMap, Solcast, Huawei Solar). The System health tab lists what was found; the mapping override screen is on the roadmap.
- **Something else.** Set the log level to `debug`, reproduce, and send the Log tab contents.

## Installing from a local folder (development)

Copy this `predheat` folder to `/addons/predheat` on the Home Assistant host, then open Settings, Add-ons, Add-on store, and refresh. PredHeat appears under Local add-ons. The image is built on the host on first install, which takes a few minutes.
