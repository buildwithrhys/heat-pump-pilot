import { useEffect, useState } from "react";
import { api, num, pounds, type Overview, type Reading } from "../api";
import { PlanList, when } from "./plan";
import { EnergyFlow } from "./flow";

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
      <h3>{title}</h3>
      <div className="big">{pounds(pence)}</div>
      <div className="sub">
        {num(kwh, 1, " kWh")}{cop ? ` · COP ${cop.toFixed(1)}` : ""}{complete === false ? " · prices incomplete" : ""}<span className={`tag ${quality}`}>{quality}</span>
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
          <div className={`card ${t.gas_equivalent.saving_pence >= 0 ? "info" : "warn"}`}>
            <h3>Same heat on gas</h3>
            <div className="big">{pounds(t.gas_equivalent.cost_pence)}</div>
            <div className="sub">{t.gas_equivalent.saving_pence >= 0 ? `heat pump ${pounds(t.gas_equivalent.saving_pence)} cheaper` : `heat pump ${pounds(-t.gas_equivalent.saving_pence)} dearer`} · modelled at {t.gas_equivalent.price_p_per_kwh}p, {Math.round(t.gas_equivalent.boiler_efficiency * 100)}% boiler</div>
          </div>
        </div>
        <p className="muted" style={{ fontSize: "0.82rem" }}>
          Heat pump energy figures come from the Vaillant controller and are manufacturer estimates, not independent meter readings. Hourly data arrives one to two hours late, so today's totals are incomplete until tomorrow.
          {!t.reconciliation.reconciled && <strong className="err"> Category totals do not reconcile (worst {t.reconciliation.worst_difference_kwh} kWh).</strong>}
        </p>
      </div>

      <div className="section">
        <h2>What PredHeat would do</h2>
        <div className="card">
          <h3>{o.next_action.state === "now" ? "Now" : "Next"}{o.operating_mode === "shadow" ? <span className="tag">shadow, recorded</span> : <span className="tag">monitor, display only</span>}</h3>
          <div className="big" style={{ fontSize: "1.1rem" }}>{o.next_action.kind === "none" ? "Nothing to shift" : o.next_action.label ?? o.next_action.kind}</div>
          <div className="sub">{o.next_action.start && o.next_action.end ? `${when(o.next_action.start, o.next_action.end)} · ` : ""}{o.next_action.reason}</div>
        </div>
        <PlanList plan={o.plan} compact skipKey={o.next_action.start ? `${o.next_action.kind}|${o.next_action.start}` : undefined} />
      </div>
      <div className="section">
        <h2>Your house</h2>
        <div className="grid">
          <div className="card">
            <h3>Heat loss{o.heat_loss.status === "ok" && <span className="tag">{Math.round((o.heat_loss.confidence ?? 0) * 100)}% confidence</span>}</h3>
            {o.heat_loss.status === "ok" ? (
              <>
                <div className="big">{num(o.heat_loss.heat_loss_w_per_k, 0, " W/K")}</div>
                <div className="sub">about {num(o.heat_loss.design_kw_at_minus_2, 1, " kW")} at −2 °C outside, 21 °C inside{o.heat_loss.ci_low_w_per_k != null && o.heat_loss.ci_high_w_per_k != null ? ` · range ${Math.round(o.heat_loss.ci_low_w_per_k)} to ${Math.round(o.heat_loss.ci_high_w_per_k)} W/K` : ""}</div>
              </>
            ) : (
              <>
                <div className="big" style={{ fontSize: "1.1rem" }}>Still learning</div>
                <div className="sub">{o.heat_loss.reason}</div>
              </>
            )}
          </div>
          {o.heat_loss.status === "ok" && (
            <div className="card">
              <h3>How quickly it cools</h3>
              <div className="big">{num(o.heat_loss.time_constant_h, 0, " h")}</div>
              <div className="sub">time constant · stores {num(o.heat_loss.capacity_kwh_per_k, 1, " kWh")} per °C{o.heat_loss.internal_gain_kw != null ? ` · ${num(o.heat_loss.internal_gain_kw, 2, " kW")} internal gains` : ""}</div>
            </div>
          )}
          <div className="card">
            <h3>Heat pump efficiency{o.heat_pump.status === "ok" && <span className="tag">{Math.round(o.heat_pump.confidence * 100)}% confidence</span>}</h3>
            <div className="big">{num(o.heat_pump.cop_at_7, 1)}</div>
            <div className="sub">COP at 7 °C · {num(o.heat_pump.cop_at_minus_2, 1)} at −2 °C · {o.heat_pump.status === "prior" ? "typical values, no data yet" : `${o.heat_pump.samples} hours of data`}{o.heat_pump.observed_mean_cop ? ` · observed ${num(o.heat_pump.observed_mean_cop, 1)}` : ""}</div>
          </div>
          {o.heat_loss.status === "ok" && (
            <div className="card">
              <h3>Based on</h3>
              <div className="big">{o.heat_loss.samples}</div>
              <div className="sub">half-hours of data over {Math.round((new Date(o.heat_loss.window_end).getTime() - new Date(o.heat_loss.window_start).getTime()) / 864e5)} days{o.heat_loss.notes && o.heat_loss.notes.length > 0 ? ` · ${o.heat_loss.notes.join("; ")}` : ""}</div>
            </div>
          )}
        </div>
      </div>
      <div className="section">
        <h2>Where today's energy went</h2>
        <EnergyFlow costs={o.today} title="Electricity in, heat out, today" />
      </div>
    </>
  );
}
