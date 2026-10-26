import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, goToTab, num, pounds, type Costs, type DailyRow } from "../api";
import { Term, tip } from "../glossary";
import { EnergyFlow } from "./flow";

/** A day with no readings at all shows dashes rather than zeros. */
const hasData = (d: DailyRow) => d.space_heating.kwh_electric > 0 || d.hot_water.kwh_electric > 0 || (d.space_heating.kwh_heat ?? 0) > 0 || d.whole_home_import.kwh > 0;
const dash = (v: number | null | undefined, fmt: (v: number) => string, ok: boolean) => (ok && v != null ? fmt(v) : "–");

/** "Wed 8 Oct" from an ISO date, UK style. */
const ukDate = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

type Preset = "7" | "14" | "30" | "90" | "this_month" | "last_month" | "this_year" | "custom";
const iso = (d: Date) => d.toISOString().slice(0, 10);
function presetRange(p: Preset): { start?: string; end?: string; days?: number } {
  const now = new Date();
  if (p === "this_month") return { start: iso(new Date(now.getFullYear(), now.getMonth(), 1)), end: iso(now) };
  if (p === "last_month") return { start: iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), end: iso(new Date(now.getFullYear(), now.getMonth(), 0)) };
  if (p === "this_year") return { start: iso(new Date(now.getFullYear(), 0, 1)), end: iso(now) };
  if (p === "custom") return {};
  return { days: Number(p) };
}
const spanDays = (q: { start?: string; end?: string; days?: number }) => q.days ?? (q.start && q.end ? Math.round((new Date(q.end).getTime() - new Date(q.start).getTime()) / 864e5) + 1 : 7);

export default function CostsPage() {
  const [preset, setPreset] = useState<Preset>("7");
  const [custom, setCustom] = useState<{ start: string; end: string }>({ start: iso(new Date(Date.now() - 29 * 864e5)), end: iso(new Date()) });
  const [group, setGroup] = useState<"auto" | "day" | "week" | "month">("auto");
  const q = preset === "custom" ? { start: custom.start, end: custom.end } : presetRange(preset);
  const days = spanDays(q);
  const effectiveGroup = group !== "auto" ? group : days <= 31 ? "day" : days <= 183 ? "week" : "month";
  const [data, setData] = useState<{ summary: Costs; daily: DailyRow[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (preset === "custom" && (!custom.start || !custom.end || custom.end < custom.start)) return;
    setData(null);
    api.costs({ ...q, group: effectiveGroup }).then(setData).catch((e) => setErr(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, custom.start, custom.end, effectiveGroup]);
  if (err) return <p className="err">{err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const s = data.summary;
  const rows = data.daily.map((d) => ({
    date: ukDate(d.date),
    heating: +(d.space_heating.cost_pence / 100).toFixed(2),
    hotwater: +(d.hot_water.cost_pence / 100).toFixed(2),
    heatkwh: d.space_heating.kwh_heat ?? 0,
    cop: d.space_heating.cop,
    label: d.label && d.days && d.days > 1 ? d.label : ukDate(d.date),
  }));
  const c1 = css("--s1"), c2 = css("--s2"), grid = css("--grid"), ink2 = css("--ink-2");
  return (
    <>
      <div className="row">
        <label>Period <select value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
          <option value="7">Last 7 days</option><option value="14">Last 14 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option>
          <option value="this_month">This month</option><option value="last_month">Last month</option><option value="this_year">This year</option><option value="custom">Custom…</option>
        </select></label>
        {preset === "custom" && (
          <>
            <label>From <input type="date" value={custom.start} max={custom.end} onChange={(e) => setCustom((c) => ({ ...c, start: e.target.value }))} /></label>
            <label>To <input type="date" value={custom.end} min={custom.start} onChange={(e) => setCustom((c) => ({ ...c, end: e.target.value }))} /></label>
          </>
        )}
        <label>Rows <select value={group} onChange={(e) => setGroup(e.target.value as "auto" | "day" | "week" | "month")}>
          <option value="auto">By {effectiveGroup} (auto)</option><option value="day">By day</option><option value="week">By week</option><option value="month">By month</option>
        </select></label>
        <a className="chip" href={api.costsCsvUrl({ ...q, group: effectiveGroup })} download style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Download CSV</a>
        <span className="muted">Tariff {s.tariff_code ?? "unknown"}</span>
      </div>
      <p className="sources-line">Heat pump energy figures are Vaillant's own estimates, not metered readings, and arrive one to two hours late<Term k="vaillant_estimate" />. Whole home import is {s.whole_home_import.source === "smart_meter_half_hourly" ? "half-hourly smart meter data" : "from the Octopus meter reading"}<Term k="metered" />. <button type="button" className="linkish" onClick={() => goToTab("System health")}>Data quality by signal</button></p>
      <div className="grid">
        <div className="card"><h3>Space heating</h3><div className="big">{pounds(s.space_heating.cost_pence)}</div><div className="sub">{num(s.space_heating.kwh_electric, 1, " kWh")} of electricity became {num(s.space_heating.kwh_heat, 1, " kWh")} of heat · COP {num(s.space_heating.cop, 2)}<Term k="cop" /></div></div>
        <div className="card"><h3>Hot water</h3><div className="big">{pounds(s.hot_water.cost_pence)}</div><div className="sub">{num(s.hot_water.kwh_electric, 1, " kWh")} of electricity became {num(s.hot_water.kwh_heat, 1, " kWh")} of heat · COP {num(s.hot_water.cop, 2)}<Term k="cop" /></div></div>
        <div className="card"><h3>Heat pump total</h3><div className="big">{pounds(s.heat_pump_total.cost_pence)}</div><div className="sub">{num(s.heat_pump_total.kwh_electric, 1, " kWh")} including backup heater {num(s.auxiliary.kwh_electric, 2, " kWh")} and standby {num(s.standby.kwh_electric, 2, " kWh")}<span className="tag" title={tip("modelled")}>standby modelled</span></div></div>
        <div className="card"><h3>Cost of heat<Term k="useful_heat_cost" /></h3><div className="big">{s.space_heating.pence_per_kwh_heat != null ? `${s.space_heating.pence_per_kwh_heat.toFixed(1)}p` : "–"}</div><div className="sub">per kWh of heat into the house, space heating; hot water {s.hot_water.pence_per_kwh_heat != null ? `${s.hot_water.pence_per_kwh_heat.toFixed(1)}p` : "–"}</div></div>
        {s.whole_home_import.plausible === false ? (
          <div className="card urgent"><h3>Whole home import</h3><div className="big" style={{ fontSize: "1.1rem" }}>Data looks wrong</div><div className="sub">{num(s.whole_home_import.kwh, 0, " kWh")} is above the {num(s.whole_home_import.limit_kwh, 0, " kWh")} bound for this period. <button type="button" className="linkish" onClick={() => goToTab("System health")}>See System health</button></div></div>
        ) : (
          <div className="card"><h3>Whole home import</h3><div className="big">{pounds(s.whole_home_import.cost_pence)}</div><div className="sub">{num(s.whole_home_import.kwh, 1, " kWh")} metered + standing {pounds(s.standing_charge_pence)}</div></div>
        )}
        <div className={`card ${s.gas_equivalent.saving_pence >= 0 ? "info" : "warn"}`}><h3>Same heat on gas<Term k="gas_comparison" /></h3><div className="big">{pounds(s.gas_equivalent.cost_pence)}</div><div className="sub">{s.gas_equivalent.saving_pence >= 0 ? `heat pump ${pounds(s.gas_equivalent.saving_pence)} cheaper` : `heat pump ${pounds(-s.gas_equivalent.saving_pence)} dearer`} · {num(s.gas_equivalent.kwh_gas, 0, " kWh")} of gas at {s.gas_equivalent.price_p_per_kwh}p, {Math.round(s.gas_equivalent.boiler_efficiency * 100)}% boiler</div></div>
      </div>

      <div className="section">
        <EnergyFlow costs={s} title={`Where the heat pump's energy came from and went, last ${days} days`} days={days} />
      </div>

      <div className="section chart">
        <h4>Daily heat pump cost</h4>
        <div className="legend"><span style={{ "--c": c1 } as React.CSSProperties}>Space heating</span><span style={{ "--c": c2 } as React.CSSProperties}>Hot water</span></div>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={rows} margin={{ top: 4, right: 12, left: 0, bottom: 0 }} barCategoryGap="30%">
            <CartesianGrid vertical={false} stroke={grid} />
            <XAxis dataKey="date" tick={{ fontSize: 12, fill: ink2 }} axisLine={{ stroke: grid }} tickLine={false} />
            <YAxis tickFormatter={(v) => `£${v}`} tick={{ fontSize: 12, fill: ink2 }} axisLine={false} tickLine={false} width={44} />
            <Tooltip formatter={(v, n) => [`£${Number(v ?? 0).toFixed(2)}`, String(n) === "heating" ? "Space heating" : "Hot water"]} contentStyle={{ borderRadius: 8, fontSize: 13 }} cursor={{ fill: grid, opacity: 0.5 }} />
            <Bar dataKey="heating" stackId="a" fill={c1} stroke={css("--surface")} strokeWidth={2} />
            <Bar dataKey="hotwater" stackId="a" fill={c2} stroke={css("--surface")} strokeWidth={2} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="section tablewrap">
        <table>
          <thead><tr><th>Period</th><th className="num">Electricity used (kWh)<Term k="kwh" /></th><th className="num">Heat delivered (kWh)</th><th className="num">COP<Term k="cop" /></th><th className="num">Heating £</th><th className="num">Hot water £</th><th className="num">Heat pump total £</th><th className="num">Home import £<Term k="metered" /></th></tr></thead>
          <tbody>
            {data.daily.map((d) => (
              <tr key={d.date}>
                <td style={{ whiteSpace: "nowrap" }}>{d.label && d.days && d.days > 1 ? `${d.label} (${d.days} days)` : ukDate(d.date)}{d.space_heating.prices_complete === false && (d.space_heating.kwh_electric > 0) ? <span className="tag bad">prices missing</span> : null}</td>
                <td className="num">{dash(d.space_heating.kwh_electric, (v) => num(v, 1), hasData(d))}</td>
                <td className="num">{dash(d.space_heating.kwh_heat, (v) => num(v, 1), hasData(d))}</td>
                <td className="num">{num(d.space_heating.cop, 2)}</td>
                <td className="num">{dash(d.space_heating.cost_pence, pounds, hasData(d) && d.space_heating.prices_complete !== false)}</td>
                <td className="num">{dash(d.hot_water.cost_pence, pounds, hasData(d) && d.hot_water.prices_complete !== false)}</td>
                <td className="num">{dash(d.heat_pump_total.cost_pence, pounds, hasData(d) && d.space_heating.prices_complete !== false)}</td>
                <td className="num">{d.whole_home_import.plausible === false ? <span className="err" title="Data looks wrong, see System health">wrong</span> : dash(d.whole_home_import.cost_pence, pounds, hasData(d) && d.whole_home_import.kwh > 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: "0.82rem" }}>Electricity is priced at the half-hourly rate in force when it was used, including confirmed Intelligent Octopus dispatch periods. Appliance costs exclude the standing charge, which is shown for the whole property.</p>
    </>
  );
}
