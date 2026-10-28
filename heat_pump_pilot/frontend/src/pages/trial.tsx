import { useEffect, useState } from "react";
import { api, pounds, type TrialReport } from "../api";
import { Term } from "../glossary";

/** Alternating-day trial results: Heat Pump Pilot days against ordinary-thermostat days, per degree-day. */
export function TrialSection() {
  const [t, setT] = useState<TrialReport | null>(null);
  useEffect(() => { api.trial(28).then(setT).catch(() => setT(null)); }, []);
  if (!t || (!t.enabled && t.days.length === 0)) return null;
  if (!t.enabled) return null;
  const ready = t.saving_pct != null;
  return (
    <div className="section">
      <h2>Trial: Heat Pump Pilot days against ordinary days</h2>
      <div className="grid">
        <div className="card"><h3>Ordinary thermostat days</h3><div className="big">{t.baseline.n}</div><div className="sub">{t.baseline.mean != null ? `${t.baseline.mean.toFixed(1)}p of heating per degree-day` : "no days yet"}<Term text="Heating cost divided by how cold the day was (degree-days below 15.5 °C), so cold and mild days compare fairly." /></div></div>
        <div className="card"><h3>Heat Pump Pilot days</h3><div className="big">{t.optimised.n}</div><div className="sub">{t.optimised.mean != null ? `${t.optimised.mean.toFixed(1)}p of heating per degree-day` : "no days yet"}</div></div>
        <div className={`card ${ready && t.credible ? "info" : ""}`}>
          <h3>Saving so far</h3>
          <div className="big">{ready ? `${(t.saving_pct ?? 0) > 0 ? "" : "−"}${Math.abs(t.saving_pct ?? 0).toFixed(0)}%` : "–"}</div>
          <div className="sub">{ready ? `give or take ${t.saving_se_pct?.toFixed(0)} percentage points · ${t.credible ? "enough days to trust it" : "needs more days before it means much"}` : "needs at least two days in each group"}</div>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12 }}>{t.note} Days: {t.days.map((d) => `${d.date.slice(5)} ${d.group === "baseline" ? "ordinary" : "Heat Pump Pilot"} ${pounds(d.cost_pence)}`).join(" · ")}</p>
    </div>
  );
}
