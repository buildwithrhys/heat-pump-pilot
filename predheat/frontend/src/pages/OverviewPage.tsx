import { useEffect, useState } from "react";
import { api, num, pounds, type Overview, type Reading } from "../api";

function Tile({ title, reading, unit = "", dp = 1, sub }: { title: string; reading: Reading | null | undefined; unit?: string; dp?: number; sub?: string }) {
  const v = reading?.value;
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="big">{reading?.text && v == null ? reading.text : num(v ?? null, dp, unit)}</div>
      <div className="sub">
        {sub ?? (reading ? new Date(reading.ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "no data")}
        {reading?.stale && <span className="tag bad">stale</span>}
      </div>
    </div>
  );
}

function CostTile({ title, pence, kwh, cop, quality, complete }: { title: string; pence: number; kwh: number; cop?: number | null; quality: string; complete?: boolean }) {
  return (
    <div className="card">
      <h3>{title}<span className={`tag ${quality}`}>{quality}</span></h3>
      <div className="big">{pounds(pence)}</div>
      <div className="sub">
        {num(kwh, 1, " kWh")}{cop ? ` · COP ${cop.toFixed(1)}` : ""}{complete === false ? " · prices incomplete" : ""}
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const [o, setO] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api.overview().then(setO).catch((e) => setErr(String(e)));
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);
  if (err) return <p className="err">Could not reach the PredHeat API: {err}</p>;
  if (!o) return <p className="muted">Loading…</p>;
  const t = o.today;
  return (
    <>
      <div className="grid">
        <Tile title="Indoor" reading={o.indoor_temperature} unit=" °C" sub={o.indoor_setpoint?.value != null ? `setpoint ${o.indoor_setpoint.value.toFixed(1)} °C` : undefined} />
        <Tile title="Outdoor" reading={o.outdoor_temperature} unit=" °C" />
        <Tile title="Heat pump" reading={o.hp_operating_state} sub={o.hp_electrical_power?.value != null ? `${(o.hp_electrical_power.value * 1000).toFixed(0)} W electrical` : undefined} />
        <Tile title="Flow" reading={o.flow_temperature} unit=" °C" />
        <Tile title="Hot water cylinder" reading={o.dhw_tank_temperature} unit=" °C" />
        <div className="card">
          <h3>Tariff now</h3>
          <div className="big">{o.import_rate_now_p != null ? `${o.import_rate_now_p.toFixed(1)}p` : "–"}</div>
          <div className="sub">{o.tariff_code ?? "unknown tariff"}</div>
        </div>
      </div>

      <div className="section">
        <h2>Today so far</h2>
        <div className="grid">
          <CostTile title="Space heating" pence={t.space_heating.cost_pence} kwh={t.space_heating.kwh_electric} cop={t.space_heating.cop} quality={t.space_heating.quality} complete={t.space_heating.prices_complete} />
          <CostTile title="Hot water" pence={t.hot_water.cost_pence} kwh={t.hot_water.kwh_electric} cop={t.hot_water.cop} quality={t.hot_water.quality} complete={t.hot_water.prices_complete} />
          <CostTile title="Heat pump total" pence={t.heat_pump_total.cost_pence} kwh={t.heat_pump_total.kwh_electric} quality={t.space_heating.quality} />
          <CostTile title="Whole home import" pence={t.whole_home_import.cost_pence} kwh={t.whole_home_import.kwh} quality={t.whole_home_import.quality} complete={t.whole_home_import.prices_complete} />
          <div className="card">
            <h3>Useful heat cost</h3>
            <div className="big">{t.space_heating.pence_per_kwh_heat != null ? `${t.space_heating.pence_per_kwh_heat.toFixed(1)}p` : "–"}</div>
            <div className="sub">per kWh of heat delivered (space heating)</div>
          </div>
          <div className="card">
            <h3>Standing charge</h3>
            <div className="big">{pounds(t.standing_charge_pence)}</div>
            <div className="sub">whole property, shown separately</div>
          </div>
        </div>
        <p className="muted" style={{ fontSize: "0.82rem" }}>
          Heat pump energy figures come from the Vaillant controller and are manufacturer estimates, not independent meter readings. Hourly data arrives one to two hours late, so today's totals are incomplete until tomorrow.
          {!t.reconciliation.reconciled && <strong className="err"> Category totals do not reconcile (worst {t.reconciliation.worst_difference_kwh} kWh).</strong>}
        </p>
      </div>

      <div className="section">
        <h2>Optimiser</h2>
        <div className="card">
          <h3>Next planned action</h3>
          <div className="big" style={{ fontSize: "1.1rem" }}>{o.next_action.kind === "none" ? "None" : o.next_action.kind}</div>
          <div className="sub">{o.next_action.reason}</div>
        </div>
      </div>
    </>
  );
}
