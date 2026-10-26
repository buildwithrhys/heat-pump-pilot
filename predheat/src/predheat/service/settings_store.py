"""Runtime preferences, editable from the dashboard without a restart.

Stored as ``settings.json`` in the data directory. The Supervisor configuration
keeps only install-time and safety settings; these four are the ones a person
adjusts while using PredHeat. The operating mode can only be set to a
controlling mode when the Supervisor side has enabled control and named a
climate entity, so the dashboard cannot widen what the admin allowed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import OperatingMode, Settings

log = logging.getLogger("predheat.settings")

MODE_LINES = {
    "monitor": "Records and prices your energy use, learns your house, changes nothing.",
    "shadow": "As Monitor, plus saves every 24-hour plan so its advice can be scored against what happened.",
    "balanced": "Acts on the plan, moving your setpoint by up to the maximum offset.",
    "max_savings": "Acts on the plan, moving your setpoint by up to 1.5 times the offset, capped at 2 °C.",
}

FIELDS: dict[str, dict[str, Any]] = {
    "control_enabled": {"type": "bool", "default": False, "group": "control", "label": "Allow control",
                        "help": "Lets PredHeat move your room setpoint when the operating mode is Balanced or Max savings. Off takes effect on the next cycle."},
    "control_climate_entity": {"type": "entity", "default": None, "optional": True, "group": "control", "label": "Climate entity",
                               "help": "The heating zone PredHeat may adjust. The only entity it ever writes to."},
    "operating_mode": {"type": "enum", "default": "monitor", "choices": ["monitor", "shadow", "balanced", "max_savings"],
                       "labels": {"monitor": "Monitor", "shadow": "Shadow", "balanced": "Balanced", "max_savings": "Max savings"},
                       "lines": MODE_LINES, "group": "control", "label": "Operating mode",
                       "help": "Balanced and Max savings need Allow control on."},
    "gas_price_p_per_kwh": {"type": "number", "default": 6.24, "min": 0.0, "max": 50.0, "step": 0.01, "unit": "p/kWh", "group": "comparisons",
                            "label": "Gas unit price", "help": "Used only for the \"same heat on gas\" comparison. Your gas unit rate including VAT."},
    "boiler_efficiency": {"type": "number", "default": 0.85, "min": 0.5, "max": 1.0, "step": 0.01, "unit": "", "group": "comparisons",
                          "label": "Gas boiler efficiency", "help": "Seasonal efficiency the comparison assumes. 0.85 suits a modern condensing boiler; older ones are nearer 0.75."},
    "control_dhw_entity": {"type": "entity", "default": None, "optional": True, "group": "control", "label": "Hot water entity (optional)",
                           "help": "The water_heater entity for the cylinder. When set, PredHeat raises its target during a planned reheat and restores it afterwards."},
    "trial_mode": {"type": "bool", "default": False, "group": "control", "label": "Trial: alternate days",
                   "help": "Alternates whole days between PredHeat acting and your ordinary thermostat, so the saving can be measured fairly. Results appear on the Energy costs tab after a couple of weeks."},
    "control_max_offset_c": {"type": "number", "default": 1.0, "min": 0.0, "max": 2.0, "step": 0.5, "unit": "°C", "group": "control",
                             "label": "Maximum setpoint offset", "help": "How far PredHeat may move your setpoint either way when control is on. Max savings uses one and a half times this, capped at 2 °C."},
    "design_outdoor_c": {"type": "number", "default": None, "min": -10.0, "max": 5.0, "step": 0.5, "unit": "°C", "optional": True, "group": "advanced",
                         "label": "Design outdoor temperature", "help": "The outdoor temperature heat loss is quoted at, as in a survey. Leave empty to use the regional value PredHeat picks from your home location."},
    "survey_heat_loss_kw": {"type": "number", "default": None, "min": 0.5, "max": 40.0, "step": 0.1, "unit": "kW", "optional": True, "group": "comparisons",
                            "label": "Surveyed heat loss", "help": "From your installer's heat loss survey, if you have one. Used as a starting point for the learned figure and shown beside it."},
    "survey_outdoor_c": {"type": "number", "default": None, "min": -10.0, "max": 5.0, "step": 0.5, "unit": "°C", "optional": True, "group": "comparisons",
                         "label": "Survey outdoor temperature", "help": "The outdoor temperature the surveyed figure was quoted at. Leave empty to assume the design temperature above."},
    "heat_pump_rated_kw": {"type": "number", "default": None, "min": 1.0, "max": 50.0, "step": 0.5, "unit": "kW", "optional": True, "group": "comparisons",
                           "label": "Heat pump rated output", "help": "Nameplate heating output, for example 7 for an aroTHERM plus 7 kW. Used to show the margin over your heat loss."},
}
OPTION_KEYS = ("control_enabled", "control_climate_entity", "control_dhw_entity", "trial_mode", "operating_mode", "control_max_offset_c",
               "gas_price_p_per_kwh", "boiler_efficiency", "survey_heat_loss_kw", "survey_outdoor_c", "heat_pump_rated_kw",
               "design_outdoor_c")
# keys that older releases held in the Supervisor options file
MIGRATED_FROM_OPTIONS = ("operating_mode", "gas_price_p_per_kwh", "boiler_efficiency", "control_max_offset_c",
                         "control_enabled", "control_climate_entity")


class SettingsError(ValueError):
    pass


def validate(key: str, value: Any) -> Any:
    spec = FIELDS.get(key)
    if spec is None:
        raise SettingsError(f"unknown setting {key}")
    if spec["type"] == "enum":
        if value not in spec["choices"]:
            raise SettingsError(f"{key} must be one of {', '.join(spec['choices'])}")
        return value
    if spec["type"] == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "1", "yes", "on"):
            return True
        if str(value).lower() in ("false", "0", "no", "off", ""):
            return False
        raise SettingsError(f"{key} must be true or false")
    if spec["type"] == "entity":
        if value in (None, ""):
            return None
        v = str(value).strip()
        if "." not in v or not v.startswith(("climate.", "water_heater.")):
            raise SettingsError(f"{key} must be a climate or water_heater entity id such as climate.living_room")
        if key == "control_climate_entity" and not v.startswith("climate."):
            raise SettingsError("the climate entity must be a climate.* entity")
        if key == "control_dhw_entity" and not v.startswith("water_heater."):
            raise SettingsError("the hot water entity must be a water_heater.* entity")
        return v
    if value in (None, "") and spec.get("optional"):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{key} must be a number") from exc
    if not (spec["min"] <= v <= spec["max"]):
        raise SettingsError(f"{key} must be between {spec['min']} and {spec['max']}")
    return v


@dataclass
class SettingsStore:
    path: Path
    values: dict[str, Any]
    migrated_from_options: bool = False

    @classmethod
    def load(cls, data_dir: Path, options_file: Path | None = None) -> "SettingsStore":
        path = data_dir / "settings.json"
        values = {k: FIELDS[k]["default"] for k in OPTION_KEYS}
        migrated = False
        if path.is_file():
            try:
                doc = json.loads(path.read_text())
                for k in OPTION_KEYS:
                    if k in doc:
                        values[k] = validate(k, doc[k])
                migrated = bool(doc.get("migrated_from_options", False))
            except Exception as exc:  # noqa: BLE001
                log.warning("settings.json unreadable (%s); using defaults", exc)
        store = cls(path, values, migrated)
        if not migrated and options_file and options_file.is_file():
            store._migrate(options_file)
        return store

    def _migrate(self, options_file: Path) -> None:
        """Copy values the Supervisor config used to hold, once, then ignore them."""
        try:
            opts = json.loads(options_file.read_text()) or {}
        except Exception:  # noqa: BLE001
            opts = {}
        for k in MIGRATED_FROM_OPTIONS:
            if k in opts and opts[k] not in (None, ""):
                try:
                    self.values[k] = validate(k, opts[k])
                    log.info("migrated add-on option %s=%r into settings.json", k, opts[k])
                except SettingsError as exc:
                    log.warning("could not migrate add-on option %s=%r: %s", k, opts[k], exc)
        self.migrated_from_options = True
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({**self.values, "migrated_from_options": self.migrated_from_options}, indent=2))

    def apply(self, settings: Settings) -> None:
        """Push the stored values onto the live Settings object the platform uses."""
        settings.control_enabled = bool(self.values.get("control_enabled", False))
        settings.control_climate_entity = self.values.get("control_climate_entity")
        settings.control_dhw_entity = self.values.get("control_dhw_entity")
        settings.trial_mode = bool(self.values.get("trial_mode", False))
        settings.operating_mode = OperatingMode(self.values["operating_mode"])
        settings.gas_price_p_per_kwh = float(self.values["gas_price_p_per_kwh"])
        settings.boiler_efficiency = float(self.values["boiler_efficiency"])
        settings.control_max_offset_c = float(self.values["control_max_offset_c"])
        settings.design_outdoor_c = self.values.get("design_outdoor_c")
        settings.survey_heat_loss_kw = self.values.get("survey_heat_loss_kw")
        settings.survey_outdoor_c = self.values.get("survey_outdoor_c")
        settings.heat_pump_rated_kw = self.values.get("heat_pump_rated_kw")

    def update(self, changes: dict[str, Any], settings: Settings) -> dict[str, Any]:
        new = dict(self.values)
        for k, v in changes.items():
            new[k] = validate(k, v)
        possible = bool(new.get("control_enabled")) and bool(new.get("control_climate_entity"))
        if "operating_mode" in changes and new["operating_mode"] in ("balanced", "max_savings") and not possible:
            raise SettingsError("Switch Allow control on and choose a climate entity before a controlling mode")
        self.values = new
        self.save()
        self.apply(settings)
        return dict(self.values)

    def reset(self, key: str | None, settings: Settings) -> dict[str, Any]:
        keys = [key] if key else list(OPTION_KEYS)
        for k in keys:
            if k not in FIELDS:
                raise SettingsError(f"unknown setting {k}")
            self.values[k] = FIELDS[k]["default"]
        self.save()
        self.apply(settings)
        return dict(self.values)

    def describe(self, settings: Settings) -> dict[str, Any]:
        return {"values": dict(self.values), "fields": FIELDS, "gate": control_gate(settings),
                "effective": {"operating_mode": settings.operating_mode.value, "control_enabled": settings.control_enabled,
                              "climate_entity": settings.control_climate_entity}}


def control_gate(settings: Settings) -> dict[str, Any]:
    possible = bool(settings.control_enabled and settings.control_climate_entity)
    return {"allow_control": bool(settings.control_enabled), "climate_entity": settings.control_climate_entity, "control_possible": possible,
            "message": None if possible else ("Needs Allow control on" if not settings.control_enabled else "Choose a climate entity")}
