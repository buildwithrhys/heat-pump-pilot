import { useEffect, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Timeline } from "../api";
import { PlanList } from "./plan";

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const dayhh = (iso: string) => new Date(iso).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });

const CLASS_LABEL: Record<string, string> = {
  guaranteed_off_peak: "Guaranteed off-peak",
  confirmed_dispatch: "Confirmed dispatch",
  planned_dispatch: "Planned dispatch (assumed)",
  peak: "Peak",
  variable: "Variable",
  unknown: "Unknown",
};

export default function ForecastPage() {
  const [t, setT] = useState<Timeline | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.timeline(48).then(setT).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="err">{err}</p>;
  if (!t) return <div className="grid">{[0, 1, 2].map((i) => <div key={i} className="card" style={{ minHeight: 200, opacity: 0.5 }}><h3>Loading forecast…</h3></div>)}</div>;
  const colour: Record<string, string> = { guaranteed_off_peak: css("--s3"), confirmed_dispatch: css("--s1"), planned_dispatch: css("--s4"), peak: css("--peak"), variable: css("--s1"), unknown: css("--grid") };
  const grid = css("--grid"), ink2 = css("--ink-2");
  // one time origin for every chart: the first priced slot; earlier forecast points are dropped
  const origin = t.slots.length ? new Date(t.slots[0].start).getTime() : 0;
  const horizon = t.slots.length ? new Date(t.slots[t.slots.length - 1].end).getTime() : Infinity;
  const inRange = (iso: string) => { const ms = new Date(iso).getTime(); return ms >= origin && ms < horizon; };
  const prices = t.slots.map((s) => ({ x: s.start, label: dayhh(s.start), rate: s.rate_p ?? 0, cls: s.price_class, assumed: s.assumed }));
  const wx = t.weather.filter((w) => inRange(w.valid_at)).map((w) => ({ x: w.valid_at, label: dayhh(w.valid_at), temp: w.temperature_c, cloud: w.cloud_cover_pct }));
  const pv = t.solar.filter((p) => inRange(p.period_start)).map((p) => ({ x: p.period_start, label: dayhh(p.period_start), pv: p.pv_kw, lo: p.pv10_kw ?? p.pv_kw, hi: p.pv90_kw ?? p.pv_kw }));
  // ticks on the hour, every six hours, whatever the series resolution
  const sixHourly = (iso: string) => { const d = new Date(iso); return d.getMinutes() === 0 && d.getHours() % 6 === 0; };
  const tickIf = (rows: { x: string }[]) => (i: number) => (rows[i] && sixHourly(rows[i].x) ? undefined : "");
  return (
    <>
      <div className="section">
        <h2>What PredHeat would do in the next 24 hours</h2>
        <PlanList plan={t.optimisation} />
      </div>
      {t.optimisation.predicted && t.optimisation.predicted.length > 0 && (
        <div className="section chart">
          <h4>Predicted indoor temperature, next 24 hours (°C)</h4>
          <div className="legend">
            <span style={{ "--c": css("--primary") } as React.CSSProperties}>PredHeat plan</span>
            <span style={{ "--c": css("--axis") } as React.CSSProperties}>Plain thermostat</span>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={t.optimisation.predicted.map((p) => ({ x: p.start, label: dayhh(p.start), plan: p.t_in, base: p.t_in_baseline, heat: p.heat_kw }))} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke={grid} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v, i) => tickIf(t.optimisation.predicted!.map((p) => ({ x: p.start })))(i) ?? v} interval={0} axisLine={{ stroke: grid }} tickLine={false} />
              <YAxis tick={{ fontSize: 12, fill: ink2 }} axisLine={false} tickLine={false} width={34} domain={["auto", "auto"]} />
              <Tooltip formatter={(v, n) => [`${Number(v ?? 0).toFixed(1)} ${String(n) === "heat" ? "kW" : "°C"}`, String(n) === "plan" ? "PredHeat plan" : String(n) === "base" ? "Plain thermostat" : "Heat output"]} labelFormatter={(_l, p) => (p?.[0] ? dayhh((p[0].payload as { x: string }).x) : "")} contentStyle={{ borderRadius: 8, fontSize: 13 }} />
              <Line type="monotone" dataKey="base" stroke={css("--axis")} strokeWidth={2} strokeDasharray="4 3" dot={false} />
              <Line type="monotone" dataKey="plan" stroke={css("--primary")} strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="note">
        The charts below are the inputs behind the plan: prices by certainty, weather and solar. Planned Intelligent Octopus dispatches are shown but never assumed to qualify until they start.
      </div>
      <div className="chart">
        <h4>Import price, next 48 hours ({t.tariff_code})</h4>
        <div className="legend">
          {Object.keys(CLASS_LABEL).filter((k) => (t.slot_summary[k] ?? 0) > 0).map((k) => (
            <span key={k} style={{ "--c": colour[k] } as React.CSSProperties}>{CLASS_LABEL[k]} ({t.slot_summary[k]})</span>
          ))}
        </div>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={prices} margin={{ top: 4, right: 12, left: 0, bottom: 0 }} barCategoryGap={1}>
            <CartesianGrid vertical={false} stroke={grid} />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v, i) => tickIf(prices)(i) ?? v} interval={0} axisLine={{ stroke: grid }} tickLine={false} />
            <YAxis tickFormatter={(v) => `${v}p`} tick={{ fontSize: 12, fill: ink2 }} axisLine={false} tickLine={false} width={40} />
            <Tooltip formatter={(v, _n, p) => [`${Number(v ?? 0).toFixed(2)} p/kWh`, CLASS_LABEL[(p.payload as { cls: string }).cls]]} labelFormatter={(_l, p) => (p?.[0] ? dayhh((p[0].payload as { x: string }).x) : "")} contentStyle={{ borderRadius: 8, fontSize: 13 }} cursor={{ fill: grid, opacity: 0.5 }} />
            <Bar dataKey="rate" radius={[2, 2, 0, 0]}>
              {prices.map((p, i) => <Cell key={i} fill={colour[p.cls]} fillOpacity={p.assumed ? 0.45 : 1} stroke={p.assumed ? colour[p.cls] : undefined} strokeDasharray={p.assumed ? "3 2" : undefined} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="section chart">
        <h4>Outdoor temperature forecast (°C)</h4>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={wx} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={grid} />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v, i) => tickIf(wx)(i) ?? v} interval={0} axisLine={{ stroke: grid }} tickLine={false} />
            <YAxis tick={{ fontSize: 12, fill: ink2 }} axisLine={false} tickLine={false} width={34} domain={["auto", "auto"]} />
            <Tooltip formatter={(v) => [`${Number(v ?? 0).toFixed(1)} °C`, "Forecast"]} labelFormatter={(_l, p) => (p?.[0] ? dayhh((p[0].payload as { x: string }).x) : "")} contentStyle={{ borderRadius: 8, fontSize: 13 }} />
            <Line type="monotone" dataKey="temp" stroke={css("--s1")} strokeWidth={2} dot={false} activeDot={{ r: 5 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="section chart">
        <h4>Solar PV forecast (kW, p10 to p90 band)</h4>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={pv} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={grid} />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v, i) => tickIf(pv)(i) ?? v} interval={0} axisLine={{ stroke: grid }} tickLine={false} />
            <YAxis tick={{ fontSize: 12, fill: ink2 }} axisLine={false} tickLine={false} width={34} />
            <Tooltip formatter={(v, n) => [`${Number(v ?? 0).toFixed(2)} kW`, String(n) === "pv" ? "Expected" : String(n) === "hi" ? "p90" : "p10"]} labelFormatter={(_l, p) => (p?.[0] ? hhmm((p[0].payload as { x: string }).x) : "")} contentStyle={{ borderRadius: 8, fontSize: 13 }} />
            <Area type="monotone" dataKey="hi" stroke="none" fill={css("--s7")} fillOpacity={0.15} />
            <Area type="monotone" dataKey="lo" stroke="none" fill={css("--surface")} fillOpacity={1} />
            <Area type="monotone" dataKey="pv" stroke={css("--s7")} strokeWidth={2} fill="none" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}
