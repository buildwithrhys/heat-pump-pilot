import { useEffect, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Timeline } from "../api";

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
  if (!t) return <p className="muted">Loading…</p>;
  const colour: Record<string, string> = { guaranteed_off_peak: css("--s3"), confirmed_dispatch: css("--s1"), planned_dispatch: css("--s4"), peak: css("--peak"), variable: css("--s1"), unknown: css("--grid") };
  const grid = css("--grid"), ink2 = css("--ink-2");
  const prices = t.slots.map((s) => ({ x: s.start, label: dayhh(s.start), rate: s.rate_p ?? 0, cls: s.price_class, assumed: s.assumed }));
  const wx = t.weather.map((w) => ({ x: w.valid_at, label: dayhh(w.valid_at), temp: w.temperature_c, cloud: w.cloud_cover_pct }));
  const pv = t.solar.map((p) => ({ x: p.period_start, label: dayhh(p.period_start), pv: p.pv_kw, lo: p.pv10_kw ?? p.pv_kw, hi: p.pv90_kw ?? p.pv_kw }));
  const tickEvery = (n: number) => (i: number) => (i % n === 0 ? undefined : "");
  return (
    <>
      <div className="note">
        {t.optimisation.reason} This page shows the inputs the optimiser will use: prices by certainty, weather and solar. Planned Intelligent Octopus dispatches are shown but never assumed to qualify until they start.
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
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v, i) => tickEvery(12)(i) ?? v} interval={0} axisLine={{ stroke: grid }} tickLine={false} />
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
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v, i) => tickEvery(6)(i) ?? v} interval={0} axisLine={{ stroke: grid }} tickLine={false} />
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
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v, i) => tickEvery(12)(i) ?? v} interval={0} axisLine={{ stroke: grid }} tickLine={false} />
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
