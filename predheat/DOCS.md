# Heat Pump Pilot

*Formerly called PredHeat. Renamed so it isn't confused with Predbat's own built-in Predheat feature; nothing about your install, settings or the `sensor.predheat_heat_pump_plan` entity changes.*

Heat Pump Pilot watches your heat pump, learns how your house holds heat, and works out the cheapest way to keep it warm on your tariff. Out of the box it only watches and advises. Control is a separate, explicit opt-in described below.

## Getting started

1. Install the add-on and click **Start**.
2. On the add-on page, switch on **Show in sidebar**. Home Assistant leaves this off by default for every add-on, which is why Heat Pump Pilot does not appear in the sidebar on its own. You can also reach it from **Open Web UI**.
3. Open **Heat Pump Pilot** and look at the **System health** tab first. It shows which entities were found for each signal and what is missing.

The first start discovers your entities and backfills a week of history, which takes a minute or two. Readings are then collected every five minutes.

## The dashboard

**Overview.** Live temperatures and heat pump state, today's costs split into space heating and hot water, what Heat Pump Pilot would do next, your house's heat loss figure, the heating curve check, and a diagram of where today's energy went.

**Energy costs.** Costs by day for the last 7 to 90 days, priced at the tariff rate in force in each half hour, with COP and cost per kWh of heat. The flow diagram covers the chosen period.

**Standing charge.** The electricity standing charge is a fixed amount per day, so Heat Pump Pilot never pro-rates it and never puts it in a heating or hot water figure: no heating choice can change it. Where a whole-house cost for a period is shown (whole home import on Energy costs), it appears once as "Standing charge (fixed, per day)" at the full daily rate from the Octopus integration, times the number of days. The "same heat on gas" comparison leaves it out of both sides, because you pay it either way. If you enter a gas standing charge on the Settings tab, it is added to the gas side for each day compared.

**Forecast.** The next 24 hours of advice with a reason for each line, and the inputs behind it: prices by certainty (guaranteed off-peak, confirmed and planned Intelligent Octopus dispatches, peak), the weather forecast and the solar forecast.

**Tariffs.** Your own consumption replayed on Intelligent Octopus Go, Go, Cosy, Flexible and, with published rates fetched, Agile, ranked by cost next to what you actually paid. Add an EV or a battery as a what-if, and see a typical year's cost from your own usage and your area's climate. Add or correct tariffs by placing a `tariffs.yaml` in the add-on's data directory (`/addon_configs/<slug>/` or the `/data` folder inside the add-on).

**System health.** Data providers, integrations discovered, every signal with its source entity and freshness, forecast accuracy of recorded plans, control status, and recent data quality events.

The API that feeds the dashboard is documented at `/docs` on the same page address.

## The heat loss figure

Heat Pump Pilot fits a single-zone building model to the last 14 days of indoor temperature, outdoor temperature and heat delivered for space heating. Until the fit reaches 30% confidence, the card says "Waiting for data" and tells you what it is waiting for, how many of the usual seven days it has, and when to expect a figure. Mild weeks with little heating are the usual reason for waiting; the first cold nights fix it.

Once learned, the headline is kilowatts at the design outdoor temperature for your region, the way an installer's survey quotes it, with 21 °C inside, plus the likely range. Enter your surveyed figure in Settings and the two are shown side by side; a difference over 25% is flagged, because it usually means either the survey or the sensor placement deserves a look. Enter the heat pump's rated output and the card says how much margin it has. "How fast it cools" is the indoor temperature drop per hour with the heating off at 5 °C outside. The watts-per-degree figure, time constant and thermal capacity are on the System health tab under Learned models.

Efficiency is the observed average COP over the period, the heat delivered divided by the electricity used from the controller's own counters, shown separately for space heating and hot water with the outdoor range it covers. It can differ from the Vaillant app because the period, the split by end use and the absence of normalisation differ.

## Predbat

If Predbat is installed, Heat Pump Pilot finds it on its own and reads its battery and solar plan each cycle. It changes nothing in Predbat.

The plan tells Heat Pump Pilot where one more kWh for the heat pump would come from in each half hour, and so what it would really cost:

- **Solar you would otherwise export:** only the export payment you give up.
- **The battery,** when Predbat's plan has it covering the house with room to spare: what Predbat values stored energy at, which is roughly what it cost to fill allowing for losses. Heat Pump Pilot never assumes the battery can discharge faster than Predbat itself plans, or go below the lowest level Predbat plans to reach.
- **The grid,** otherwise, at your tariff. The figure is never higher than the grid price.

Heat Pump Pilot plans the heating with these prices, so it no longer avoids heating at 5pm when the battery is covering the house cheaply. The Forecast tab shows the cost line over the price chart, and System health shows whether the plan is in use. The plan is used only while it is under 30 minutes old. Turn it off with "Use Predbat's battery plan" on the Settings tab.

**Sharing Heat Pump Pilot's plan with Predbat.** Predbat forecasts your usage from past days, so when Heat Pump Pilot moves heating it would not know in advance. Heat Pump Pilot publishes `sensor.predheat_heat_pump_plan` for Predbat to read. Add it to Predbat's `apps.yaml`, alongside any load forecast you already have, and restart Predbat:

```yaml
  load_forecast:
    - sensor.predheat_heat_pump_plan$extra
```

Predbat adds an outside forecast on top of its own, and its own already includes the heat pump from past days. So the `extra` attribute holds only the space heating Heat Pump Pilot plans above what the same half hour used on average over the last 7 days. Heating moved away from a time cannot be taken off Predbat's forecast (Predbat ignores negative values), so at those times Predbat may keep a little more in the battery than needed, which errs on the safe side. The `planned` attribute holds the full space heating plan, for setups whose history already excludes the heat pump.

The sensor carries extra load only while Heat Pump Pilot is controlling the heating (Balanced or Max savings with Allow control on), and not on trial days run by your ordinary thermostat; otherwise it is zero, because the heat pump will use what it usually does. System health shows what is being shared. Turn it off with "Share Heat Pump Pilot's plan with Predbat" on the Settings tab. Hot water is included when Heat Pump Pilot moves the cylinder reheat, which is when a hot water entity is chosen on the Settings tab; otherwise the cylinder heats on its own schedule, which Predbat already sees in its history.

## The heating curve check

The heating curve on your Vaillant controller decides how hot the radiator water is for each outdoor temperature. Water hotter than the house needs costs money: the heat pump is roughly 2 to 3% less efficient for every degree of extra flow temperature. The check, on the Overview under Your house, works out what your house actually needs from your own data:

1. From the hours when the heat pump was heating the house steadily, it measures how much heat your radiators give out at the water temperatures they ran at.
2. From your learned heat loss it knows how much heat the house needs at each outdoor temperature.
3. Together these give the water temperature the house needs, plus a 1 °C margin. The chart shows that against what your controller asks for now and what it would ask for with the suggested setting.
4. It cross-checks against comfort: a room above setpoint with the compressor starting more than twice an hour means the water is hotter than needed; a room below setpoint with the heat pump running flat out means it is not hot enough, and the check then suggests raising the curve, never lowering it.

It also looks at the minimum flow temperature (too high overrides the curve on mild days) and the outdoor cut-off (above which your house's own heat gains keep it warm).

**Changing a setting.** When Home Assistant offers the setting as an adjustable entity (myVAILLANT does on most systems), the table has a Change button. Heat Pump Pilot shows exactly which entity and values it will set and changes it once, only when you confirm. It never changes these settings by itself. Each request is limited to the entity's own range and to a sensible step (0.5 on the curve), is recorded in the control log, and can be undone from the same place. Without an adjustable entity, change it in the myVAILLANT app.

After a change the check looks only at heating since the change, so it judges the new setting, and "Since your last change" compares efficiency with what the week before predicted for the same weather, plus whether the rooms were warmer or cooler. Go one step at a time and give each step a few days, including a cold spell before going lower again.

## What Heat Pump Pilot would do

Until the building model is identified, the plan is rule-based: hot water at the end of the cheapest window, a pre-heat before expensive periods when the forecast says heating is needed, and a coast through them. Every line explains itself and says it is rule-based.

Once the heat loss figure reaches 30% confidence, the model-based optimiser takes over. For every half hour of the next 24 it chooses how hard to run the heat pump so that the electricity bill is lowest while the indoor temperature stays inside a comfort band of one degree either side of your setpoint and the house ends the day no colder than the setpoint. It uses the fitted heat loss and thermal capacity, the heat pump's COP at the forecast outdoor temperature, and the half-hourly prices. The Forecast tab shows the predicted indoor temperature for the plan and for a plain thermostat, and the saving between the two. Hot water is scheduled into the cheapest slots per unit of delivered heat before the morning.

In **shadow** mode each plan is saved. Once its horizon has passed, the model's prediction of indoor temperature is scored against what the sensor measured, and the cost it expected against what the heating actually cost at the prices it had then. Both appear on the System health tab. The target is a typical error under 0.5 °C.

The plan looks up to 48 hours ahead. Octopus publishes tomorrow's prices around 16:00; before that the second day uses the standard rate and is marked as assumed.

## Letting Heat Pump Pilot control the heating

Control is off by default and needs three things on the Settings tab: **Allow control** on, a **climate entity** chosen, and an operating mode of **Balanced** or **Max savings**. If any one is missing, nothing is ever written. Switching Allow control on shows the four preconditions (seven days in Shadow, heat loss confidence 50% or more, forecast accuracy under 0.5 °C, an entity chosen) and whether each is met; you can go ahead with one unmet by ticking that you understand Heat Pump Pilot may move the setpoint. Switching it off needs no confirmation and hands the setpoint back on the next cycle. Every change to the gate is written to the control log.

When all three are set, on every collection cycle Heat Pump Pilot reads the current target temperature of the climate entity and, if the plan says to pre-heat or coast right now, moves it up or down by the configured offset (balanced: the offset; max_savings: one and a half times it, capped at 2 °C). Otherwise it leaves your own setpoint in place. It hands the setpoint back and logs why when:

- the readings are stale (indoor temperature older than 30 minutes),
- the building model is below 50% confidence or the plan is not model-based,
- you change the thermostat yourself (it then waits four hours before acting again),
- the house is already below the comfort band,
- the add-on stops.

Every proposal, change, failure and release is written to the control log, and the System health tab shows what it is holding right now. Suggested sequence: run in shadow for a week, check the heat loss confidence and forecast accuracy, then switch to balanced.

## Settings: where each one lives

**Add-on Configuration tab** (saving restarts the add-on):

| Option | Default | Meaning |
|---|---|---|
| Collection interval | 300 s | How often readings, prices and forecasts are refreshed. |
| Log level | `info` | Use `debug` only while diagnosing a problem. |

**Settings tab in the dashboard** (saves as you change it, applies at once, stored in the add-on's data directory):

| Setting | Default | Meaning |
|---|---|---|
| Allow control | off | The interlock. A toggle with a confirmation that lists the preconditions. |
| Climate entity | the myVAILLANT zone when there is one | The heating zone Heat Pump Pilot may adjust, chosen from a list. |
| Operating mode | Monitor | Monitor, Shadow, Balanced or Max savings, each explained on its card. The controlling modes light up when Allow control is on. |
| Maximum setpoint offset | 1.0 °C | How far Heat Pump Pilot may move your setpoint either way. |
| Hot water entity | none | Optional water_heater entity. Heat Pump Pilot raises its target during a planned reheat and restores it afterwards. |
| Trial: alternate days | off | Alternates Heat Pump Pilot days and ordinary-thermostat days so the saving can be measured. Results on Energy costs. |
| Gas unit price, boiler efficiency | 6.24p, 0.85 | For the "same heat on gas" comparison. |
| Design outdoor temperature | from your region | The outdoor temperature heat loss is quoted at. |
| Surveyed heat loss and its outdoor temperature | empty | From your installer's survey; used as a starting point and shown beside the learned figure. |
| Heat pump rated output | empty | Nameplate kW, to show the margin over your heat loss. |

The home location and time zone come from Home Assistant. Values you had set for the moved options in an earlier release are copied into the Settings tab once on the first start after upgrading.

## Safety

Heat Pump Pilot reads Home Assistant through the Supervisor with the token Home Assistant issues to the add-on, so there is no long-lived access token to create or store. Exactly one module can write to Home Assistant, and it can do exactly one thing: set the target temperature of the single climate entity you name. It is only loaded when control is configured, every write goes through the guard chain above, and the API cannot enable control: the `/api/control` endpoints always answer 403. See `SAFETY.md` in the repository.

## Data

The database and discovery reports live in the add-on's `/data` directory. It survives restarts and updates and is included in Home Assistant backups.

## Troubleshooting

- **Not in the sidebar.** Switch on **Show in sidebar** on the add-on page.
- **"Learning" instead of a heat loss figure.** Fewer than 24 hours of usable indoor, outdoor and heat data so far, or a signal is missing. Check the System health tab.
- **A signal mapped to the wrong entity, or missing.** On System health, click Change next to the signal and pick from the candidates Heat Pump Pilot scored, or go back to automatic. Your choice is kept across restarts and updates.
- **Whole home import says "Data looks wrong".** The day's import is above a sanity bound. Check which entity feeds grid_import_kwh on System health; `/api/samples?signal=grid_import_kwh` shows the stored readings.
- **A reading shows "not updating".** Hover over it for the reason. Temperatures, pressure and power are flagged after 2 hours with nothing new from Home Assistant. Settings, modes, on/off flags, prices and energy counters can stay the same for days and are flagged only if the entity is unavailable or its integration has stopped sending altogether, which usually means the integration needs reloading in Home Assistant.
- **A new feature is missing after an update.** Home Assistant keeps the Heat Pump Pilot panel loaded, so the page can keep running the previous version. Reload the browser tab (Ctrl+F5 on a computer; close and reopen the Home Assistant app on a phone). From 0.6.2 the page tells you when this has happened and offers a reload.
- **No new version offered.** Settings, Add-ons, Add-on Store, the three-dot menu, Check for updates.
- **Predbat shows "Not installed" although Predbat is running.** Heat Pump Pilot looks for `predbat.grid_power_best`, `predbat.battery_power_best` and `predbat.soc_kw_best` (any prefix). Predbat's own Predheat switch (`switch.predbat_predheat_enable`) is a separate Predbat feature and makes no difference.
- **Something else.** Set the log level to `debug`, reproduce, and send the Log tab contents.

## Installing from a local folder (development)

Copy this `predheat` folder to `/addons/predheat` on the Home Assistant host, then open Settings, Add-ons, Add-on store, and refresh. Heat Pump Pilot appears under Local add-ons. The image is built on the host on first install, which takes a few minutes.
