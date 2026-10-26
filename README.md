# PredHeat

**PredHeat watches your heat pump, learns how your house holds heat, and works out the cheapest way to keep it warm.**

It runs as a Home Assistant add-on. Give it a few days and it will tell you your home's heat loss in plain numbers, show you exactly where every kilowatt-hour went and what it cost, and lay out what it would do differently on your tariff: when to heat the hot water, when to warm the house ahead of expensive electricity, and when to let it coast.

In this release PredHeat only watches and advises. It never changes a setting on your heating.

![Overview](docs/screenshots/desktop-Overview.png)

## What you get

**A heat loss figure for your house.** From indoor and outdoor temperatures and the heat your pump delivers, PredHeat fits a simple physical model and reports the heat loss in watts per degree, the equivalent kilowatts on a cold day, how many hours the house takes to cool, and how confident it is. The number improves as more days are collected.

**Where the energy went.** A flow diagram shows electricity coming in from the grid, solar or battery, the free heat the pump pulled from the outside air, and the heat that went into space heating and hot water. Costs are priced slot by slot at the tariff rate in force at the time, including Intelligent Octopus dispatch windows.

**What PredHeat would do.** A 24-hour plan on your real prices and weather: reheat the cylinder at the end of the cheap window, pre-heat before the expensive period, coast through it. Every line says why. In shadow mode each plan is saved so the advice can be scored against what actually happened.

**System health.** Which Home Assistant entities were discovered for each signal, how fresh they are, and what is missing.

![Forecast](docs/screenshots/desktop-Forecast.png)

## Install

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbuildwithrhys%2Fpredheat)

1. Click the badge above, or in Home Assistant go to **Settings → Add-ons → Add-on store → ⋮ → Repositories** and add `https://github.com/buildwithrhys/predheat`.
2. Find **PredHeat** in the store and click **Install**. The image is built on your machine and takes a few minutes.
3. On the add-on page, switch on **Show in sidebar**, then click **Start**.
4. Open **PredHeat** from the sidebar.

No access token is needed. The add-on reads Home Assistant through the Supervisor.

The badge needs this repository to be public. While it is private, install from a folder instead: copy `predheat/` to `/addons/predheat` on the Home Assistant host, then refresh the add-on store and look under **Local add-ons**.

## First run

PredHeat discovers your entities on first start and backfills a week of history. Open the **System health** tab first. It lists every signal it needs, which entity it mapped it to, and anything missing. The four signals the heat loss model cannot do without are indoor temperature, outdoor temperature, electricity used for heating and heat delivered for heating.

The heat loss figure appears once a full day of usable data exists and tightens over the following days. Until then the Overview says "Learning".

## What PredHeat works with

Any heat pump, tariff, weather and solar source that is already in Home Assistant can be mapped. The pilot installation is a Vaillant aroTHERM via myVAILLANT, Octopus Intelligent Go via the Octopus Energy integration, OpenWeatherMap, Solcast and Huawei Solar for PV and battery. Entity names from other integrations are matched by discovery hints, and the mapping is shown on the System health tab.

## Settings

The add-on's configuration page explains every option. The two that matter:

- **Data source**: `home_assistant` for live data, `simulated` to explore the dashboard with a synthetic house.
- **Operating mode**: `monitor` records, prices and advises. `shadow` also saves every plan it produces for later comparison. Neither controls anything.

Full details are in [predheat/DOCS.md](predheat/DOCS.md).

## Safety

There is no code path that writes to heating equipment. The control endpoints always return 403 and the add-on forces control off whatever the configuration says. See [SAFETY.md](SAFETY.md).

## For developers

The repository is a Home Assistant add-on repository. `repository.yaml` marks it as such and `predheat/` is the add-on, which is also the Docker build context, so the Python package, tests and the React dashboard live inside it. The design documents are at the top level: [ARCHITECTURE.md](ARCHITECTURE.md), [DATA_MODEL.md](DATA_MODEL.md), [INTEGRATIONS.md](INTEGRATIONS.md), [VALIDATION.md](VALIDATION.md), [ROADMAP.md](ROADMAP.md).

Run it outside Home Assistant with simulated data:

```bash
cd predheat
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
predheat bootstrap          # creates data/predheat.sqlite with 14 simulated days
predheat serve              # API on http://127.0.0.1:8750, docs at /docs
cd frontend && npm install && npm run dev   # dashboard on http://localhost:5173
```

Or from the repository root, `docker compose up --build` builds the same image as the add-on and serves the dashboard on port 8750. Copy `.env.example` to `.env` to point it at a Home Assistant instance with a long-lived token.
