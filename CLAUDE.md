# Heat Pump Pilot: rules for anyone (human or model) changing this repository

## The "no unanswered questions" rule for the dashboard

Every number, badge, label or status in the UI must be understandable by a homeowner with no heating or energy background, without leaving the page and without asking anyone. The test for any element: if a user saw only this, what would they ask? If there is a question, the element answers it in place or it does not ship.

- A number always carries its unit and its period in plain words ("10.5 kWh today").
- Units a homeowner does not use day to day (W/K, time constants, normalised COP, RMSE) are translated into something they do (kW at −3 °C, degrees lost per hour, average COP over the last 7 days, typical error) and the technical form goes in a tooltip.
- A badge is shown only when it changes how the reader should treat the number, and says why in the same breath ("Vaillant estimate", "stale: last read 2 h ago"). If the reason is the same for a group of cards, say it once under the group heading.
- Unknown states say what is being waited for, how far along it is and when to expect a result. Never an internal condition ("confidence is 0%", "range includes zero", "Phase 4").
- Every term with technical content gets a tooltip, and tooltips draw from `predheat/frontend/src/glossary.tsx` so wording is consistent across tabs. Add to the glossary rather than writing a one-off.
- Each card has one headline figure, one plain-English label that includes the unit and the period, and at most one supporting line. Anything else goes in a tooltip.

Before shipping UI changes, do the pass: list each element that would make a user ask a question and the proposed wording (see `docs/ui-audit-2026-10-26.md` for the format), then apply.

## Other standing rules

- Cumulative meter readings are never summed as intervals. Statistics rows and live rows are separate sources; each counter has a `CounterProfile` in `analytics/energy_accounting.py` (maximum rate, how late its data arrives, daily reset, longest gap it may span), and every discarded rise is logged as a data quality event. See `CHANGELOG.md` 0.2.1, 0.2.3 and 0.3.1 for the incidents.
- The only module that writes to Home Assistant is `predheat/src/predheat/integrations/homeassistant/control.py`. Control cannot be enabled through the API; the gate lives in the Settings tab store and is re-checked every cycle (`SAFETY.md`). The one exception to "no writes through the API" is the heating curve check: a person can apply or undo one of three Vaillant settings after a confirmation dialog, bounded and logged (`SAFETY.md`, "Heating curve settings"). Nothing automatic may call those paths. Besides these, Heat Pump Pilot writes only its own `sensor.predheat_*` entities (the plan it shares with Predbat), never anyone else's.
- Runtime preferences live in `settings.json` via `service/settings_store.py`, not in the Supervisor options. The add-on Configuration tab holds only collection interval and log level, both optional, because existing installs never receive new defaults.
- Tests must pass (`cd predheat && pytest`) and the dashboard must build (`cd predheat/frontend && npm run build`) before a version bump in `config.yaml`, `pyproject.toml` and `__init__.py`, with a `CHANGELOG.md` entry written for users.
