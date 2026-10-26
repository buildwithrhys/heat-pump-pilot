import { useEffect, useState } from "react";
import { api, goToTab, num, pounds, type Overview, type Reading } from "../api";
import { PlanList, when } from "./plan";
import { EnergyFlow } from "./flow";
import { Term, tip } from "../glossary";
import { YourHouse } from "./house";
import { HeatingCurveCheck } from "./curve";

const ago = (min: number | undefined) => (min == null ? "" : min < 1 ? "just now" : min < 90 ? `${Math.round(min)} min ago` : `${(min / 60).toFixed(1)} h ago`);

function Tile({ title, reading, unit = "", dp = 1, sub, term }: { title: string; reading: Reading | null | undefined; unit?: string; dp?: number; sub?: string; term?: string }) {
  const v = reading?.value;
  const tipText = reading ? `Last read ${ago(reading.age_minutes)}; value last changed ${new Date(reading.ts).toLocaleString("en-GB", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" })}. Flagged stale after ${reading.stale_after_minutes ?? 15} minutes without a successful read.` : "No reading stored";
  return (
    <div className="card" title={tipText}>
      <h3>{title}{term ? <Term k={term} /> : null}</h3>
      <div className="big">{reading?.text && v == null ? reading.text : num(v ?? null, dp, unit)}</div>
      <div className="sub">
        {sub ?? (reading ? `read ${ago(reading.age_minutes)}` : "no data")}
        {reading?.stale && <span className="tag bad" title={tipText}>stale: last read {ago(reading.age_minutes)}</span>}
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
        {num(kwh, 1, " kWh")} of electricity today{cop ? <> · COP {cop.toFixed(1)}<Term k="cop" /></> : null}{complete === false ? <span className="tag" title={tip("prices_incomplete")}>some prices missing</span> : null}{quality === "modelled" ? <span className="tag" title={tip("modelled")}>modelled</span> : null}
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
        <Tile title="Heat pump" term="hp_state" reading={o.hp_operating_state} sub={o.hp_electrical_power?.value != null ? `drawing ${(o.hp_electrical_power.value * 1000).toFixed(0)} W now` : undefined} />
        <Tile title="Flow temperature" term="flow_temperature" reading={o.flow_temperature} unit=" °C" />
        <Tile title="Hot water cylinder" reading={o.dhw_tank_temperature} unit=" °C" />
        <div className="card">
          <h3>Electricity price now<Term k="tariff_now" /></h3>
          <div className="big">{o.import_rate_now_p != null ? `${o.import_rate_now_p.toFixed(1)}p` : "–"}</div>
          <div className="sub" title={o.tariff_code ?? ""}>per kWh, import rate right now</div>
        </div>
      </div>

      <div className="section">
        <h2>Today so far</h2>
        <p className="sources-line">Heat pump energy figures are Vaillant's own estimates, not metered readings, and arrive one to two hours late<Term k="vaillant_estimate" />. Whole home import is metered<Term k="metered" />. <button type="button" className="linkish" onClick={() => goToTab("System health")}>Data quality by signal</button></p>
        <div className="grid">
          <CostTile title="Space heating" pence={t.space_heating.cost_pence} kwh={t.space_heating.kwh_electric} cop={t.space_heating.cop} quality={t.space_heating.quality} complete={t.space_heating.prices_complete} />
          <CostTile title="Hot water" pence={t.hot_water.cost_pence} kwh={t.hot_water.kwh_electric} cop={t.hot_water.cop} quality={t.hot_water.quality} complete={t.hot_water.prices_complete} />
          <CostTile title="Heat pump total" pence={t.heat_pump_total.cost_pence} kwh={t.heat_pump_total.kwh_electric} quality={t.space_heating.quality} />
          {t.whole_home_import.plausible === false ? (
            <div className="card urgent"><h3>Whole home import</h3><div className="big" style={{ fontSize: "1.1rem" }}>Data looks wrong</div><div className="sub">{num(t.whole_home_import.kwh, 0, " kWh")} today is above the {num(t.whole_home_import.limit_kwh, 0, " kWh")} bound. <button type="button" className="linkish" onClick={() => goToTab("System health")}>See System health</button></div></div>
          ) : (
            <div className="card"><h3>Whole home import</h3><div className="big">{pounds(t.whole_home_import.cost_pence)}</div><div className="sub">{num(t.whole_home_import.kwh, 1, " kWh")} metered{t.whole_home_import.prices_complete === false ? " · prices incomplete" : ""}</div></div>
          )}
          <div className="card">
            <h3>Cost of heat<Term k="useful_heat_cost" /></h3>
            <div className="big">{t.space_heating.pence_per_kwh_heat != null ? `${t.space_heating.pence_per_kwh_heat.toFixed(1)}p` : "–"}</div>
            <div className="sub">per kWh of heat into the house today, space heating</div>
          </div>
          <div className="card">
            <h3>Standing charge<Term k="standing_charge" /></h3>
            <div className="big">{pounds(t.standing_charge_pence)}</div>
            <div className="sub">today's fixed daily charge, whole property</div>
          </div>
          <div className={`card ${t.gas_equivalent.saving_pence >= 0 ? "info" : "warn"}`}>
            <h3>Same heat on gas<Term k="gas_comparison" /></h3>
            <div className="big">{pounds(t.gas_equivalent.cost_pence)}</div>
            <div className="sub">{t.gas_equivalent.saving_pence >= 0 ? `heat pump ${pounds(t.gas_equivalent.saving_pence)} cheaper` : `heat pump ${pounds(-t.gas_equivalent.saving_pence)} dearer`} · modelled at {t.gas_equivalent.price_p_per_kwh}p, {Math.round(t.gas_equivalent.boiler_efficiency * 100)}% boiler</div>
          </div>
        </div>
        {!t.reconciliation.reconciled && <p className="err" style={{ fontSize: 12 }}>Category totals do not reconcile (worst {t.reconciliation.worst_difference_kwh} kWh).</p>}
      </div>

      <div className="section">
        <h2>What PredHeat would do</h2>
        <div className="card">
          <h3>{o.next_action.state === "now" ? "Now" : "Next"}{o.operating_mode === "monitor" ? <span className="tag" title={tip("monitor_plan")}>Monitor mode: shown only, nothing acts</span> : o.operating_mode === "shadow" ? <span className="tag" title={tip("shadow_plan")}>Shadow mode: this plan is saved and scored</span> : <span className="tag measured">acting on this plan</span>}</h3>
          <div className="big" style={{ fontSize: "1.1rem" }}>{o.next_action.kind === "none" ? "Nothing to shift" : o.next_action.label ?? o.next_action.kind}</div>
          <div className="sub">{o.next_action.start && o.next_action.end ? `${when(o.next_action.start, o.next_action.end)} · ` : ""}{o.next_action.reason}</div>
        </div>
        <PlanList plan={o.plan} compact skipKey={o.next_action.start ? `${o.next_action.kind}|${o.next_action.start}` : undefined} />
      </div>
      <div className="section">
        <h2>Your house</h2>
        <YourHouse hl={o.heat_loss} hp={o.heat_pump} />
      </div>
      <div className="section">
        <h2>Heating curve check</h2>
        <p className="sources-line">Is your heat pump making the water hotter than your house needs? Checked from the last 14 days of your own data.</p>
        <HeatingCurveCheck />
      </div>
      <div className="section">
        <h2>Heat pump energy today</h2>
        <EnergyFlow costs={o.today} title="Where the heat pump's energy came from and went today" days={1} />
      </div>
    </>
  );
}
