# PredHeat

PredHeat watches your heat pump, learns how your house holds heat, and works out the cheapest way to keep it warm on your tariff. In this release it only watches and advises: it never changes a setting on your heating.

## Getting started

1. Install the add-on and click **Start**.
2. On the add-on page, switch on **Show in sidebar**. Home Assistant leaves this off by default for every add-on, which is why PredHeat does not appear in the sidebar on its own. You can also reach it from **Open Web UI**.
3. Open **PredHeat** and look at the **System health** tab first. It shows which entities were found for each signal and what is missing.

The first start discovers your entities and backfills a week of history, which takes a minute or two. Readings are then collected every five minutes.

## The dashboard

**Overview.** Live temperatures and heat pump state, today's costs split into space heating and hot water, what PredHeat would do next, your house's heat loss figure, and a diagram of where today's energy went.

**Energy costs.** Costs by day for the last 7 to 90 days, priced at the tariff rate in force in each half hour, with COP and cost per kWh of heat. The flow diagram covers the chosen period.

**Forecast.** The next 24 hours of advice with a reason for each line, and the inputs behind it: prices by certainty (guaranteed off-peak, confirmed and planned Intelligent Octopus dispatches, peak), the weather forecast and the solar forecast.

**System health.** Data providers, integrations discovered, every signal with its source entity and freshness, and recent data quality events.

The API that feeds the dashboard is documented at `/docs` on the same page address.

## The heat loss figure

PredHeat fits a single-zone building model to the last 14 days of indoor temperature, outdoor temperature and heat delivered for space heating. It reports:

- **Heat loss** in watts per degree of indoor-to-outdoor difference, with a range. The "about x kW" line is that figure at 21 °C inside and -2 °C outside, the usual design condition for UK heat pump sizing, so you can compare it with your installer's survey.
- **How quickly it cools**, the time constant in hours, and how much heat the fabric stores per degree.
- **Based on**, how many half-hours of data were usable and any caveats, such as too little temperature variation to pin the number down.

It needs at least one full day of data before it reports, and the range narrows as days accumulate and weather varies. Mild weeks with little heating give wide ranges; cold snaps tighten them.

## What PredHeat would do

The plan is rule-based in this release and every line explains itself. It looks for the cheapest window in the next 24 hours and the expensive periods, then proposes to:

- **Heat hot water** in the last hour of the cheapest window, so the cylinder is hot for the morning with the least standing loss.
- **Pre-heat the house** by about 1 °C in the cheapest 90 minutes before an expensive period, when the forecast says heating will be needed.
- **Let the house coast** for up to three hours into the expensive period on the stored heat, then resume.

If prices are flat, it says so and recommends running normally. In **shadow** mode each plan is saved, so once enough history exists the advice can be scored against what the heating actually did.

## Options

Every option has a description on the Configuration page. In brief:

| Option | Default | Meaning |
|---|---|---|
| Data source | `home_assistant` | Live data from this instance, or `simulated` for a synthetic house to explore the dashboard. |
| Operating mode | `monitor` | `monitor` records, prices and advises. `shadow` also saves each plan for later comparison. Neither controls anything. |
| Collection interval | 300 s | How often readings, prices and forecasts are refreshed. |
| Log level | `info` | Use `debug` only while diagnosing a problem. |
| Latitude, longitude | from Home Assistant | Override the location used for solar geometry. |
| Octopus, OpenWeather and Solcast keys | empty | Not needed today. Reserved for direct access in later releases. |
| Simulated history, seed | 14 days, 42 | Only used with the simulated data source. |

## Safety

PredHeat is read-only by design. There is no code in it that writes to heating equipment, the `/api/control` endpoints always answer 403, and the add-on forces control off regardless of configuration. It reads Home Assistant through the Supervisor with the token Home Assistant issues to the add-on, so there is no long-lived access token to create or store.

## Data

The database and discovery reports live in the add-on's `/data` directory. It survives restarts and updates and is included in Home Assistant backups.

## Troubleshooting

- **Not in the sidebar.** Switch on **Show in sidebar** on the add-on page.
- **"Learning" instead of a heat loss figure.** Fewer than 24 hours of usable indoor, outdoor and heat data so far, or a signal is missing. Check the System health tab.
- **Hot water or heating signals missing.** Discovery matches entity names from the pilot integrations (myVAILLANT, Octopus Energy, OpenWeatherMap, Solcast, Huawei Solar). The System health tab lists what was found; the mapping override screen is on the roadmap.
- **Something else.** Set the log level to `debug`, reproduce, and send the Log tab contents.

## Installing from a local folder (development)

Copy this `predheat` folder to `/addons/predheat` on the Home Assistant host, then open Settings, Add-ons, Add-on store, and refresh. PredHeat appears under Local add-ons. The image is built on the host on first install, which takes a few minutes.
