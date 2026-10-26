# PredHeat

Self-learning heat pump and home energy optimiser. This release is a monitor: it reads your heat pump, tariff, weather and solar data through Home Assistant, learns how the house behaves, prices every kWh at the rate in force when it was used, and shows the results in a dashboard. It does not control anything.

## What it reads

PredHeat discovers the entities it needs from the integrations already set up in Home Assistant. The pilot installation uses a Vaillant heat pump (myVAILLANT), Octopus Energy, OpenWeather, Solcast and Huawei Solar. The System health tab shows which signals were found and which are missing.

## Options

| Option | Default | Meaning |
|---|---|---|
| `data_mode` | `home_assistant` | `home_assistant` reads live data from this instance. `simulated` runs a synthetic house with known physics, useful for checking the dashboard without any hardware. |
| `operating_mode` | `monitor` | `monitor` records and prices. `shadow` also computes what an optimiser would have done, without acting. Active modes do not exist in this release. |
| `poll_seconds` | `300` | How often to collect readings. |
| `log_level` | `info` | Add-on log verbosity. |
| `latitude`, `longitude` | from Home Assistant | Override the location used for solar geometry. |
| `octopus_api_key`, `octopus_account` | empty | Optional. Direct Octopus access for future phases. The Home Assistant Octopus integration is used today. |
| `openweather_api_key`, `solcast_api_key` | empty | Optional. Direct provider access for future phases. |
| `sim_days`, `sim_seed` | `14`, `42` | Only used in `simulated` mode. |

## Safety

The add-on is read-only by design. There is no code path that writes to heating equipment, the `/api/control` endpoints always return 403, and the add-on forces `PREDHEAT_CONTROL_ENABLED=false` regardless of configuration. See `SAFETY.md` in the repository.

The Home Assistant API is reached through the Supervisor with the token Home Assistant issues to the add-on. No long-lived access token is needed and none is stored.

## Data

The database and discovery reports live in the add-on's `/data` directory, which survives restarts and updates and is included in Home Assistant backups.

## Dashboard

Open PredHeat from the sidebar. It has four tabs: Overview, Energy costs, Forecast and System health. The API documentation is at `/docs` on the same ingress URL.

## Installing from a local folder (development)

Copy this `predheat` folder to `/addons/predheat` on the Home Assistant host, then open Settings, Add-ons, Add-on store, and refresh. PredHeat appears under Local add-ons. The image is built on the host on first install, which takes a few minutes.
