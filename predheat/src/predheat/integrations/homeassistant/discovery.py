"""Automated capability discovery from a Home Assistant state dump.

Pure functions: give them a list of ``HAState`` and they return proposals.
This is what makes the capability report factual rather than assumed, and it
is the basis of the entity-mapping UI (the user confirms or overrides).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from ...domain import (
    CapabilityReport,
    DiscoveredCapability,
    Provenance,
    Quality,
    SIGNAL_CATALOGUE,
    Signal,
    SignalSpec,
)
from ...domain.signals import SignalKind
from .client import HAState

# Platforms we know how to interpret, mapped to provenance labels
KNOWN_PLATFORMS: dict[str, Provenance] = {
    "mypyllant": Provenance.VAILLANT_MYPYLLANT,
    "octopus_energy": Provenance.OCTOPUS_HA,
    "openweathermap": Provenance.OPENWEATHER_HA,
    "solcast_solar": Provenance.SOLCAST_HA,
    "huawei_solar": Provenance.HUAWEI_SOLAR_HA,
}

# Entities that look like a match but must never be used for a signal
_EXCLUDE_SUBSTRINGS = ("forecast_", "model_y_", "tesla_", "inverter_internal", "bms_", "pack_", "device_temperature")
# Home Assistant appends _2, _3 ... to duplicate entity ids; those are stale copies.
_DUPLICATE_SUFFIX = re.compile(r"_\d$")


def _score(state: HAState, spec: SignalSpec) -> tuple[float, str]:
    """Return (confidence 0..1, note) for mapping *state* to *spec*."""
    eid = state.entity_id.lower()
    if any(x in eid for x in _EXCLUDE_SUBSTRINGS) or _DUPLICATE_SUFFIX.search(eid):
        return 0.0, "excluded"
    if state.disabled:
        return 0.0, "disabled"
    if state.domain not in spec.domains:
        return 0.0, f"domain {state.domain} cannot carry this signal"
    if spec.require and not all(r in eid for r in spec.require):
        return 0.0, "missing required anchor"
    if any(x in eid for x in spec.exclude):
        return 0.0, "excluded by signal rule"
    is_energy_counter = state.device_class == "energy" or (state.unit or "") in ("Wh", "kWh", "MWh")
    if spec.kind != SignalKind.CUMULATIVE and is_energy_counter:
        return 0.0, "energy counter cannot carry an instantaneous or boolean signal"
    if spec.kind == SignalKind.CUMULATIVE and state.domain == "sensor" and not is_energy_counter and spec.unit == "kWh":
        return 0.0, "not an energy counter"
    score = 0.0
    note = ""
    for rank, hint in enumerate(spec.hints):
        if hint in eid:
            score = 1.0 - rank * 0.12  # earlier (more specific) hints score higher
            note = f"matched hint '{hint}'"
            break
    if score == 0.0:
        return 0.0, ""
    if spec.device_class and state.device_class and state.device_class != spec.device_class:
        score -= 0.4
        note += f"; device_class {state.device_class} != {spec.device_class}"
    unit = state.unit
    if spec.unit_scale and unit and unit not in spec.unit_scale:
        score -= 0.4
        note += f"; unit {unit} not convertible"
    if spec.accept_units and unit and unit not in spec.accept_units:
        score -= 0.3
    if spec.unit_scale and not unit:
        score -= 0.5
        note += "; no unit"
    if not state.is_live:
        score -= 0.5
        note += f"; state is {state.state}"
    return max(score, 0.0), note


def propose_mappings(states: Iterable[HAState]) -> dict[Signal, list[tuple[HAState, float, str]]]:
    """For each signal, ranked candidate entities (best first)."""
    states = list(states)
    out: dict[Signal, list[tuple[HAState, float, str]]] = {}
    for signal, spec in SIGNAL_CATALOGUE.items():
        cands = []
        for s in states:
            sc, note = _score(s, spec)
            if sc > 0:
                cands.append((s, sc, note))
        cands.sort(key=lambda c: c[1], reverse=True)
        out[signal] = cands
    return out


def _quality_for(signal: Signal, state: HAState) -> Quality:
    spec = SIGNAL_CATALOGUE[signal]
    plat = state.platform or ""
    if plat == "mypyllant" and spec.unit in ("kWh", "kW", "count", "h"):
        return Quality.MANUFACTURER_ESTIMATED
    if plat in ("octopus_energy", "huawei_solar") and spec.unit in ("kWh", "kW"):
        return Quality.METERED
    return spec.default_quality if spec.default_quality != Quality.UNAVAILABLE else Quality.SENSOR


def discover_capabilities(
    states: Iterable[HAState],
    config_entries: list[dict] | None = None,
    now: datetime | None = None,
) -> CapabilityReport:
    states = list(states)
    now = now or datetime.now(tz=timezone.utc)
    proposals = propose_mappings(states)
    caps: list[DiscoveredCapability] = []
    warnings: list[str] = []

    for signal, spec in SIGNAL_CATALOGUE.items():
        cands = proposals.get(signal, [])
        if not cands:
            caps.append(
                DiscoveredCapability(
                    signal=signal, status="unavailable", quality=Quality.UNAVAILABLE,
                    confidence=0.0, notes="no matching entity",
                )
            )
            continue
        best, conf, note = cands[0]
        status = "available" if (best.is_live and conf >= 0.5) else "degraded"
        caps.append(
            DiscoveredCapability(
                signal=signal,
                status=status,
                quality=_quality_for(signal, best),
                source_ref=best.entity_id,
                unit=best.unit,
                confidence=round(conf, 2),
                notes=note,
                alternatives=[c[0].entity_id for c in cands[1:4]],
            )
        )

    # Writable controls we can *see* (not use). Only Vaillant entities count.
    writable = []
    for s in states:
        if (s.platform or "") != "mypyllant":
            continue
        if s.domain in ("number", "switch", "climate", "water_heater", "select"):
            writable.append(
                {
                    "entity_id": s.entity_id,
                    "domain": s.domain,
                    "state": s.state,
                    "min": s.attributes.get("min") or s.attributes.get("min_temp"),
                    "max": s.attributes.get("max") or s.attributes.get("max_temp"),
                    "step": s.attributes.get("step") or s.attributes.get("target_temp_step"),
                    "modes": s.attributes.get("hvac_modes") or s.attributes.get("operation_list"),
                    "presets": s.attributes.get("preset_modes"),
                }
            )

    integrations = []
    platforms_present = {s.platform for s in states if s.platform}
    for plat, prov in KNOWN_PLATFORMS.items():
        n = sum(1 for s in states if s.platform == plat)
        live = sum(1 for s in states if s.platform == plat and s.is_live)
        entry = next((c for c in (config_entries or []) if c.get("domain") == plat), None)
        integrations.append(
            {
                "platform": plat,
                "provenance": prov.value,
                "present": plat in platforms_present,
                "entities": n,
                "live_entities": live,
                "config_state": entry.get("state") if entry else None,
            }
        )

    # Warnings that matter for the thermal model and for safety
    indoor = [c for c in caps if c.signal == Signal.INDOOR_TEMPERATURE]
    if indoor and indoor[0].status == "available":
        alts = [a for a in indoor[0].alternatives]
        if not alts:
            warnings.append("Only one indoor temperature source found; thermal model will be single-zone.")
    for sig in (Signal.HP_COMPRESSOR_ON, Signal.HP_DEFROST_ACTIVE, Signal.RETURN_TEMPERATURE):
        c = next(c for c in caps if c.signal == sig)
        if c.status == "unavailable":
            warnings.append(f"{sig.value} is not exposed by the installation; related analytics will be estimated or omitted.")
    ems = next((s for s in states if s.entity_id.endswith("energy_manager_state")), None)
    if ems:
        warnings.append(
            f"Vaillant energy manager state is '{ems.state}'. Treat EnergyPLUS/EEBUS as a potential competing controller; Heat Pump Pilot stays read-only."
        )

    return CapabilityReport(
        generated_at=now,
        provenance=Provenance.HOME_ASSISTANT,
        capabilities=caps,
        writable_controls=writable,
        integrations=integrations,
        warnings=warnings,
    )
