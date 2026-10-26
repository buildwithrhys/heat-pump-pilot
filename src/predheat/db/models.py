"""SQLAlchemy ORM models. See DATA_MODEL.md for units and provenance rules.

Conventions
- All ``*_at`` / ``ts`` / ``start`` / ``end`` columns hold UTC datetimes.
- Energy in kWh, power in kW, temperatures in degC, prices in pence.
- Every stored number has a ``quality`` and a ``provenance``.
- Forecast rows keep ``issued_at`` so that backtests use what was known at the time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TZDateTime(DateTime):
    """DateTime that always carries timezone info (SQLite stores naive, we re-attach UTC)."""

    def __init__(self) -> None:
        super().__init__(timezone=True)


class Property(Base):
    __tablename__ = "property"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    postcode_area: Mapped[str | None] = mapped_column(String(8))
    preferred_temp_c: Mapped[float] = mapped_column(Float, default=21.0)
    comfort_tolerance_c: Mapped[float] = mapped_column(Float, default=1.0)
    survey_heat_loss_kw: Mapped[float | None] = mapped_column(Float)
    survey_design_indoor_c: Mapped[float | None] = mapped_column(Float)
    survey_design_outdoor_c: Mapped[float | None] = mapped_column(Float)
    floor_area_m2: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime())

    equipment: Mapped[list["Equipment"]] = relationship(back_populates="property")


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    kind: Mapped[str] = mapped_column(String(40))  # heat_pump, cylinder, inverter, battery, ev_charger, meter
    manufacturer: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(80))
    rated_kw: Mapped[float | None] = mapped_column(Float)
    external_id: Mapped[str | None] = mapped_column(String(120))
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    property: Mapped[Property] = relationship(back_populates="equipment")


class IntegrationConnection(Base):
    __tablename__ = "integration_connection"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    provider: Mapped[str] = mapped_column(String(60))  # Provenance value
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # ok, degraded, failed
    last_success_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # never secrets


class SensorMapping(Base):
    """Which native entity feeds which canonical signal."""

    __tablename__ = "sensor_mapping"
    __table_args__ = (UniqueConstraint("property_id", "signal", name="uq_mapping_signal"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    signal: Mapped[str] = mapped_column(String(60))
    source_ref: Mapped[str] = mapped_column(String(200))
    provenance: Mapped[str] = mapped_column(String(60))
    quality: Mapped[str] = mapped_column(String(40))
    unit_scale: Mapped[float] = mapped_column(Float, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class SensorMeasurement(Base):
    __tablename__ = "sensor_measurement"
    __table_args__ = (
        Index("ix_measurement_signal_ts", "property_id", "signal", "ts"),
        UniqueConstraint("property_id", "signal", "ts", "source_ref", name="uq_measurement"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    signal: Mapped[str] = mapped_column(String(60))
    ts: Mapped[datetime] = mapped_column(TZDateTime())
    value: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str | None] = mapped_column(String(80))
    quality: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[str] = mapped_column(String(60))
    source_ref: Mapped[str | None] = mapped_column(String(200))
    ingested_at: Mapped[datetime] = mapped_column(TZDateTime())


class WeatherForecast(Base):
    __tablename__ = "weather_forecast"
    __table_args__ = (
        UniqueConstraint("property_id", "provenance", "issued_at", "valid_at", name="uq_wx"),
        Index("ix_wx_valid", "property_id", "valid_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    provenance: Mapped[str] = mapped_column(String(60))
    issued_at: Mapped[datetime] = mapped_column(TZDateTime())
    valid_at: Mapped[datetime] = mapped_column(TZDateTime())
    temperature_c: Mapped[float] = mapped_column(Float)
    wind_speed_ms: Mapped[float | None] = mapped_column(Float)
    humidity_pct: Mapped[float | None] = mapped_column(Float)
    cloud_cover_pct: Mapped[float | None] = mapped_column(Float)
    condition: Mapped[str | None] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(40), default="forecast")


class SolarForecast(Base):
    __tablename__ = "solar_forecast"
    __table_args__ = (
        UniqueConstraint("property_id", "provenance", "issued_at", "period_start", name="uq_solar"),
        Index("ix_solar_period", "property_id", "period_start"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    provenance: Mapped[str] = mapped_column(String(60))
    issued_at: Mapped[datetime] = mapped_column(TZDateTime())
    period_start: Mapped[datetime] = mapped_column(TZDateTime())
    period_minutes: Mapped[int] = mapped_column(Integer, default=30)
    pv_estimate_kw: Mapped[float] = mapped_column(Float)
    pv_estimate10_kw: Mapped[float | None] = mapped_column(Float)
    pv_estimate90_kw: Mapped[float | None] = mapped_column(Float)
    ghi_w_m2: Mapped[float | None] = mapped_column(Float)


class ElectricityTariff(Base):
    __tablename__ = "electricity_tariff"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    tariff_code: Mapped[str] = mapped_column(String(60), index=True)
    product_code: Mapped[str | None] = mapped_column(String(60))
    display_name: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(4))
    is_export: Mapped[bool] = mapped_column(Boolean, default=False)
    family: Mapped[str | None] = mapped_column(String(40))  # intelligent_go, go, cosy, agile, fixed, flexible
    valid_from: Mapped[datetime | None] = mapped_column(TZDateTime())
    valid_to: Mapped[datetime | None] = mapped_column(TZDateTime())
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class TariffRateInterval(Base):
    __tablename__ = "tariff_rate_interval"
    __table_args__ = (
        UniqueConstraint("tariff_code", "start", "is_intelligent_adjusted", name="uq_rate"),
        Index("ix_rate_lookup", "tariff_code", "start", "end"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tariff_code: Mapped[str] = mapped_column(String(60))
    start: Mapped[datetime] = mapped_column(TZDateTime())
    end: Mapped[datetime] = mapped_column(TZDateTime())
    rate_p_per_kwh: Mapped[float] = mapped_column(Float)
    is_intelligent_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_capped: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[str] = mapped_column(String(60))
    observed_at: Mapped[datetime] = mapped_column(TZDateTime())


class TariffStandingCharge(Base):
    __tablename__ = "tariff_standing_charge"
    id: Mapped[int] = mapped_column(primary_key=True)
    tariff_code: Mapped[str] = mapped_column(String(60), index=True)
    start: Mapped[datetime] = mapped_column(TZDateTime())
    end: Mapped[datetime | None] = mapped_column(TZDateTime())
    pence_per_day: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(TZDateTime())


class SmartChargingDispatch(Base):
    __tablename__ = "smart_charging_dispatch"
    __table_args__ = (Index("ix_dispatch_window", "property_id", "start", "end"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    kind: Mapped[str] = mapped_column(String(12))  # planned, started, completed
    start: Mapped[datetime] = mapped_column(TZDateTime())
    end: Mapped[datetime] = mapped_column(TZDateTime())
    charge_kwh: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(20))
    location: Mapped[str | None] = mapped_column(String(20))
    observed_at: Mapped[datetime] = mapped_column(TZDateTime())


class EnergyConsumptionInterval(Base):
    """Half-hourly energy per category, derived from counters. kWh."""

    __tablename__ = "energy_consumption_interval"
    __table_args__ = (
        UniqueConstraint("property_id", "category", "start", name="uq_energy_interval"),
        Index("ix_energy_interval", "property_id", "start"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    category: Mapped[str] = mapped_column(String(40))
    # hp_elec_heating, hp_elec_dhw, aux_elec_heating, aux_elec_dhw, hp_standby,
    # hp_unclassified, hp_total, grid_import, grid_export, pv, ev, household_other
    start: Mapped[datetime] = mapped_column(TZDateTime())
    end: Mapped[datetime] = mapped_column(TZDateTime())
    kwh: Mapped[float] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[str] = mapped_column(String(60))
    allocation_method: Mapped[str | None] = mapped_column(String(60))


class ThermalEnergyInterval(Base):
    """Half-hourly useful heat per end use. kWh thermal."""

    __tablename__ = "thermal_energy_interval"
    __table_args__ = (UniqueConstraint("property_id", "end_use", "start", name="uq_thermal_interval"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    end_use: Mapped[str] = mapped_column(String(20))  # heating, dhw
    start: Mapped[datetime] = mapped_column(TZDateTime())
    end: Mapped[datetime] = mapped_column(TZDateTime())
    kwh_thermal: Mapped[float] = mapped_column(Float)
    quality: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[str] = mapped_column(String(60))


class CostCalculation(Base):
    """Reproducible cost of one energy interval under one tariff version."""

    __tablename__ = "cost_calculation"
    __table_args__ = (
        UniqueConstraint("property_id", "category", "start", "tariff_code", "scenario", name="uq_cost"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    category: Mapped[str] = mapped_column(String(40))
    start: Mapped[datetime] = mapped_column(TZDateTime())
    end: Mapped[datetime] = mapped_column(TZDateTime())
    tariff_code: Mapped[str] = mapped_column(String(60))
    scenario: Mapped[str] = mapped_column(String(40), default="actual")  # actual, replay:<code>, optimised:<code>
    kwh: Mapped[float] = mapped_column(Float)
    rate_p_per_kwh: Mapped[float] = mapped_column(Float)
    cost_pence: Mapped[float] = mapped_column(Float)
    rate_observed_at: Mapped[datetime] = mapped_column(TZDateTime())
    quality: Mapped[str] = mapped_column(String(40))
    calculated_at: Mapped[datetime] = mapped_column(TZDateTime())


class BuildingModelVersion(Base):
    __tablename__ = "building_model_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    created_at: Mapped[datetime] = mapped_column(TZDateTime())
    model_type: Mapped[str] = mapped_column(String(40), default="1R1C")
    heat_loss_w_per_k: Mapped[float | None] = mapped_column(Float)
    heat_loss_ci_low: Mapped[float | None] = mapped_column(Float)
    heat_loss_ci_high: Mapped[float | None] = mapped_column(Float)
    thermal_capacity_kwh_per_k: Mapped[float | None] = mapped_column(Float)
    solar_gain_factor: Mapped[float | None] = mapped_column(Float)
    internal_gain_kw: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    training_window_start: Mapped[datetime | None] = mapped_column(TZDateTime())
    training_window_end: Mapped[datetime | None] = mapped_column(TZDateTime())
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    is_survey_prior: Mapped[bool] = mapped_column(Boolean, default=False)


class HeatPumpModelVersion(Base):
    __tablename__ = "heat_pump_model_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    created_at: Mapped[datetime] = mapped_column(TZDateTime())
    model_type: Mapped[str] = mapped_column(String(40), default="cop_linear_dT")
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class OptimisationRun(Base):
    __tablename__ = "optimisation_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    issued_at: Mapped[datetime] = mapped_column(TZDateTime())
    horizon_start: Mapped[datetime] = mapped_column(TZDateTime())
    horizon_end: Mapped[datetime] = mapped_column(TZDateTime())
    mode: Mapped[str] = mapped_column(String(20))
    building_model_id: Mapped[int | None] = mapped_column(ForeignKey("building_model_version.id"))
    heat_pump_model_id: Mapped[int | None] = mapped_column(ForeignKey("heat_pump_model_version.id"))
    tariff_code: Mapped[str | None] = mapped_column(String(60))
    predicted_cost_pence: Mapped[float | None] = mapped_column(Float)
    baseline_cost_pence: Mapped[float | None] = mapped_column(Float)
    slots: Mapped[list] = mapped_column(JSON, default=list)  # per-slot plan with explanations
    status: Mapped[str] = mapped_column(String(20), default="shadow")


class ControlExecutionLog(Base):
    """Audit log of every proposed and executed control action. Append only."""

    __tablename__ = "control_execution_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    ts: Mapped[datetime] = mapped_column(TZDateTime())
    run_id: Mapped[int | None] = mapped_column(ForeignKey("optimisation_run.id"))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(200))
    requested_value: Mapped[str | None] = mapped_column(String(80))
    previous_value: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))  # proposed, blocked, executed, failed, reverted
    reason: Mapped[str | None] = mapped_column(Text)


class DataQualityEvent(Base):
    __tablename__ = "data_quality_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    ts: Mapped[datetime] = mapped_column(TZDateTime())
    severity: Mapped[str] = mapped_column(String(10))  # info, warning, error
    source: Mapped[str] = mapped_column(String(60))
    signal: Mapped[str | None] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
