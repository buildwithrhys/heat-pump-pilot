import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, num, pounds, type Costs, type DailyRow } from "../api";
import { EnergyFlow } from "./flow";

/** "Wed 8 Oct" from an ISO date, UK style. */
const ukDate = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

export default function CostsPage() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<{ summary: Costs; daily: DailyRow[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setData(null); api.costs(days).then(setData).catch((e) => setErr(String(e))); }, [days]);
  if (err) return <p className="err">{err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const s = data.summary;
  const rows = data.daily.map((d) => ({
    date: d.date.slice(5),
    heating: +(d.space_heating.cost_pence / 100).toFixed(2),
    hotwater: +(d.hot_water.cost_pence / 100).toFixed(2),
    heatkwh: d.space_heating.kwh_heat ?? 0,
    cop: d.space_heating.cop,
    label: ukDate(d.date),
  }));
  const c1 = css("--s1"), c2 = css("--s2"), grid = css("--grid"), ink2 = css("--ink-2");
  return (
    <>
      <div className="row">
        <label>Period <select value={days} onChange={(e) => setDays(+e.target.value)}>
          <option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </select></label>
        <span className="muted">{s.tariff_code}</span>
      </div>
      <div className="grid">
        <div className="card"><h3>Space heating<span className={`tag ${s.space_heating.quality}`}>{s.space_heating.quality}</span></h3><div className="big">{pounds(s.space_heating.cost_pence)}</div><div className="sub">{num(s.space_heating.kwh_electric, 1, " kWh elec")} → {num(s.space_heating.kwh_heat, 1, " kWh heat")} · COP {num(s.space_heating.cop, 2)}</div></div>
        <div className="card"><h3>Hot water<span className={`tag ${s.hot_water.quality}`}>{s.hot_water.quality}</span></h3><div className="big">{pounds(s.hot_water.cost_pence)}</div><div className="sub">{num(s.hot_water.kwh_electric, 1, " kWh elec")} → {num(s.hot_water.kwh_heat, 1, " kWh heat")} · COP {num(s.hot_water.cop, 2)}</div></div>
        <div className="card"><h3>Heat pump total</h3><div className="big">{pounds(s.heat_pump_total.cost_pence)}</div><div className="sub">{num(s.heat_pump_total.kwh_electric, 1, " kWh")} incl. aux {num(s.auxiliary.kwh_electric, 2)} and standby {num(s.standby.kwh_electric, 2)} (estimated)</div></div>
        <div className="card"><h3>Cost per kWh of heat</h3><div className="big">{s.space_heating.pence_per_kwh_heat != null ? `${s.space_heating.pence_per_kwh_heat.toFixed(1)}p` : "–"}</div><div className="sub">space heating; hot water {s.hot_water.pence_per_kwh_heat != null ? `${s.hot_water.pence_per_kwh_heat.toFixed(1)}p` : "–"}</div></div>
        <div className="card"><h3>Whole home import<span className={`tag ${s.whole_home_import.quality}`}>{s.whole_home_import.quality}</span></h3><div className="big">{pounds(s.whole_home_import.cost_pence)}</div><div className="sub">{num(s.whole_home_import.kwh, 1, " kWh")} + standing {pounds(s.standing_charge_pence)}</div></div>
        <div className={`card ${s.gas_equivalent.saving_pence >= 0 ? "info" : "warn"}`}><h3>Same heat on gas</h3><div className="big">{pounds(s.gas_equivalent.cost_pence)}</div><div className="sub">{s.gas_equivalent.saving_pence >= 0 ? `heat pump ${pounds(s.gas_equivalent.saving_pence)} cheaper` : `heat pump ${pounds(-s.gas_equivalent.saving_pence)} dearer`} · {num(s.gas_equivalent.kwh_gas, 0, " kWh")} of gas at {s.gas_equivalent.price_p_per_kwh}p, {Math.round(s.gas_equivalent.boiler_efficiency * 100)}% boiler</div></div>
      </div>

      <div className="section">
        <EnergyFlow costs={s} title={`Electricity in, heat out, last ${days} days`} />
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
          <thead><tr><th>Day</th><th className="num">Heating kWh</th><th className="num">Heat kWh</th><th className="num">COP</th><th className="num">Heating £</th><th className="num">Hot water £</th><th className="num">HP total £</th><th className="num">Home import £</th></tr></thead>
          <tbody>
            {data.daily.map((d) => (
              <tr key={d.date}>
                <td style={{ whiteSpace: "nowrap" }}>{ukDate(d.date)}</td>
                <td className="num">{num(d.space_heating.kwh_electric, 1)}</td>
                <td className="num">{num(d.space_heating.kwh_heat, 1)}</td>
                <td className="num">{num(d.space_heating.cop, 2)}</td>
                <td className="num">{pounds(d.space_heating.cost_pence)}</td>
                <td className="num">{pounds(d.hot_water.cost_pence)}</td>
                <td className="num">{pounds(d.heat_pump_total.cost_pence)}</td>
                <td className="num">{pounds(d.whole_home_import.cost_pence)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: "0.82rem" }}>Electricity is priced at the half-hourly rate in force when it was used, including confirmed Intelligent Octopus dispatch periods. Appliance costs exclude the standing charge, which is shown for the whole property.</p>
    </>
  );
}
