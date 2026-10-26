import { useEffect, useState } from "react";
import { api, type SystemHealth } from "../api";

export default function SystemPage() {
  const [s, setS] = useState<SystemHealth | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.system().then(setS).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="err">{err}</p>;
  if (!s) return <p className="muted">Loading…</p>;
  const avail = s.signals.filter((x) => x.status === "available");
  return (
    <>
      <div className="grid">
        <div className="card"><h3>Data source</h3><div className="big" style={{ fontSize: "1.2rem" }}>{s.data_mode}</div><div className="sub">operating mode {s.operating_mode}</div></div>
        <div className={`card ${s.control.holding ? "info" : ""}`}><h3>Control</h3><div className="big" style={{ fontSize: "1.2rem" }}>{!s.control.configured ? "Off" : s.control.holding ? `Holding ${s.control.commanded_c} °C` : "Armed"}</div><div className="sub">{s.control.configured ? `${s.control.entity} · ${s.control.last?.reason ?? "waiting for first cycle"}` : s.control.reason} · {s.record_counts.control_actions} log entries</div></div>
        <div className="card"><h3>Signals</h3><div className="big">{avail.length}<span className="muted" style={{ fontSize: "1rem" }}> / {s.signals.length}</span></div><div className="sub">{avail.filter((x) => x.stale).length} stale</div></div>
        <div className="card"><h3>Stored</h3><div className="big">{s.record_counts.measurements.toLocaleString()}</div><div className="sub">measurements · {s.record_counts.rate_intervals} rate slots · {s.record_counts.dispatches} dispatches</div></div>
      </div>

      <div className="section">
        <h2>Forecast accuracy</h2>
        {s.forecast_accuracy.status !== "ok" ? (
          <p className="muted">{s.forecast_accuracy.reason}</p>
        ) : (
          <div className="grid">
            <div className={`card ${s.forecast_accuracy.meets_target ? "info" : "warn"}`}>
              <h3>Indoor temperature error</h3>
              <div className="big">{s.forecast_accuracy.rmse_baseline_c?.toFixed(2)} °C</div>
              <div className="sub">RMSE over {s.forecast_accuracy.points} half-hours in {s.forecast_accuracy.runs} plans · target {s.forecast_accuracy.target_rmse_c} °C</div>
            </div>
            <div className="card">
              <h3>Bias</h3>
              <div className="big">{(s.forecast_accuracy.bias_baseline_c ?? 0) > 0 ? "+" : ""}{s.forecast_accuracy.bias_baseline_c?.toFixed(2)} °C</div>
              <div className="sub">positive means the model runs warm · mean absolute error {s.forecast_accuracy.mae_baseline_c?.toFixed(2)} °C</div>
            </div>
          </div>
        )}
        {s.forecast_accuracy.note && <p className="muted" style={{ fontSize: 12 }}>{s.forecast_accuracy.note}</p>}
      </div>

      {s.missing_for_thermal_model.length > 0 && (
        <div className="note">Thermal modelling cannot start: missing {s.missing_for_thermal_model.join(", ")}.</div>
      )}
      {s.warnings.map((w, i) => <div className="note" key={i}>{w}</div>)}

      <div className="section">
        <h2>Providers</h2>
        <div className="tablewrap"><table>
          <thead><tr><th>Provider</th><th>Status</th><th>Last success</th><th>Error</th></tr></thead>
          <tbody>{s.providers.map((p) => (
            <tr key={p.name}><td>{p.name}</td><td><span className={`tag ${p.ok ? "measured" : "bad"}`}>{p.ok ? "ok" : "failed"}</span></td><td>{p.last_success_at ? new Date(p.last_success_at).toLocaleString("en-GB") : "–"}</td><td className="muted">{p.last_error ?? ""}</td></tr>
          ))}</tbody>
        </table></div>
      </div>

      <div className="section">
        <h2>Integrations discovered</h2>
        <div className="tablewrap"><table>
          <thead><tr><th>Platform</th><th>Present</th><th className="num">Entities</th><th className="num">Live</th><th>Config</th></tr></thead>
          <tbody>{s.integrations.map((i) => (
            <tr key={i.platform}><td>{i.platform}</td><td>{i.present ? "yes" : "no"}</td><td className="num">{i.entities}</td><td className="num">{i.live_entities}</td><td className="muted">{i.config_state ?? ""}</td></tr>
          ))}</tbody>
        </table></div>
      </div>

      <div className="section">
        <h2>Signal mapping and freshness</h2>
        <div className="tablewrap"><table>
          <thead><tr><th>Signal</th><th>Status</th><th>Quality</th><th>Source</th><th className="num">Last value</th><th className="num">Age (min)</th><th>Notes</th></tr></thead>
          <tbody>{s.signals.map((x) => (
            <tr key={x.signal}>
              <td>{x.signal}</td>
              <td><span className={`tag ${x.status === "available" ? (x.stale ? "bad" : "measured") : x.status === "degraded" ? "estimated" : ""}`}>{x.stale ? "stale" : x.status}</span></td>
              <td className="muted">{x.quality}</td>
              <td className="muted" style={{ fontSize: "0.78rem", wordBreak: "break-all" }}>{x.source ?? ""}</td>
              <td className="num">{x.last_text ?? (x.last_value != null ? x.last_value.toFixed(2) : "")}</td>
              <td className="num">{x.age_minutes ?? ""}</td>
              <td className="muted">{x.notes}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </div>

      <div className="section">
        <h2>Recent data quality events</h2>
        {s.recent_events.length === 0 ? <p className="muted">None.</p> : (
          <div className="tablewrap"><table>
            <thead><tr><th>Time</th><th>Severity</th><th>Source</th><th>Message</th></tr></thead>
            <tbody>{s.recent_events.map((e, i) => (
              <tr key={i}><td>{new Date(e.ts).toLocaleString("en-GB")}</td><td><span className={`tag ${e.severity === "error" ? "bad" : "estimated"}`}>{e.severity}</span></td><td>{e.source}</td><td>{e.message}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </>
  );
}
