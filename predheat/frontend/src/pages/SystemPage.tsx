import { useEffect, useState } from "react";
import { api, num, pounds, type HeatLoss, type HeatPump, type MappingSignal, type MappingView, type SystemHealth } from "../api";
import { GLOSSARY, Term } from "../glossary";

const QUALITY_LABEL: Record<string, [string, string]> = {
  sensor: ["measured", GLOSSARY.quality_sensor], metered: ["metered", GLOSSARY.quality_metered], manufacturer_estimated: ["appliance estimate", GLOSSARY.quality_manufacturer_estimated],
  model_derived: ["PredHeat estimate", GLOSSARY.quality_model_derived], forecast: ["forecast", GLOSSARY.quality_forecast], simulated: ["simulated", GLOSSARY.quality_simulated], unavailable: ["none", GLOSSARY.status_unavailable],
};
const STATUS_LABEL: Record<string, [string, string]> = { available: ["live", GLOSSARY.status_live], degraded: ["found, not updating", GLOSSARY.status_degraded], unavailable: ["not found", GLOSSARY.status_unavailable] };

function LearnedModels() {
  const [hl, setHl] = useState<HeatLoss | null>(null);
  const [hp, setHp] = useState<HeatPump | null>(null);
  useEffect(() => { api.heatLoss().then(setHl).catch(() => null); api.heatPump().then(setHp).catch(() => null); }, []);
  if (!hl || !hp) return <p className="muted">Loading…</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead><tr><th>Model</th><th>Status</th><th>Value</th><th>Detail (technical)</th></tr></thead>
        <tbody>
          <tr>
            <td>House model<Term k="heat_loss" /></td>
            <td><span className={`tag ${hl.status === "ok" ? "measured" : ""}`}>{hl.status === "ok" ? "learned" : "waiting"}</span></td>
            <td className="num">{hl.status === "ok" ? <>{num(hl.heat_loss_w_per_k, 0)} W per °C<Term k="w_per_k" /></> : "–"}</td>
            <td className="muted">{hl.status === "ok" ? <>likely range {num(hl.ci_low_w_per_k, 0)} to {num(hl.ci_high_w_per_k, 0)} W per °C · confidence {Math.round((hl.confidence ?? 0) * 100)}% · heat stored per °C {hl.capacity_kwh_per_k != null ? `${hl.capacity_kwh_per_k} kWh` : "not learned"}<Term k="thermal_capacity" /> · time constant {hl.time_constant_h != null ? `${hl.time_constant_h} h` : "not learned"}<Term k="time_constant" /> · design temperature {hl.design_outdoor_c} °C ({hl.design_region})<Term k="design_temperature" />{hl.notes && hl.notes.length ? ` · ${hl.notes.join("; ")}` : ""}</> : hl.reason}</td>
          </tr>
          <tr>
            <td>Heat pump efficiency, space heating<Term k="cop" /></td>
            <td><span className={`tag ${hp.heating.status === "ok" ? "measured" : ""}`}>{hp.heating.status === "ok" ? "learned" : "waiting"}</span></td>
            <td className="num">{hp.heating.observed_cop != null ? `COP ${num(hp.heating.observed_cop, 2)}` : "–"}</td>
            <td className="muted">{hp.heating.status === "ok" ? <>{hp.heating.running_hours} running hours over {hp.heating.days_with_data} days · efficiency changes by {hp.heating.slope_per_degc} per °C outside<Term k="slope" /> · model confidence {Math.round(hp.heating.model_confidence * 100)}% · most heat seen {hp.capacity_kw} kW ({hp.capacity_source})<Term k="capacity_source" /></> : hp.heating.reason}</td>
          </tr>
          <tr>
            <td>Heat pump efficiency, hot water<Term k="cop" /></td>
            <td><span className={`tag ${hp.hot_water.status === "ok" ? "measured" : ""}`}>{hp.hot_water.status === "ok" ? "learned" : "waiting"}</span></td>
            <td className="num">{hp.hot_water.observed_cop != null ? `COP ${num(hp.hot_water.observed_cop, 2)}` : "–"}</td>
            <td className="muted">{hp.hot_water.status === "ok" ? `${hp.hot_water.running_hours} running hours over ${hp.hot_water.days_with_data} days` : hp.hot_water.reason}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function MappingRow({ x, map, editable, onChange }: { x: SystemHealth["signals"][number]; map?: MappingSignal; editable: boolean; onChange: (m: MappingView) => void }) {
  const [editing, setEditing] = useState(false);
  const [choice, setChoice] = useState<string>(x.source ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const unmapped = x.status === "unavailable";
  const save = async (entity: string | null) => {
    setBusy(true); setProblem(null);
    try { onChange(await api.setMapping(x.signal, entity)); setEditing(false); }
    catch (e) { setProblem(String((e as Error).message)); }
    finally { setBusy(false); }
  };
  return (
    <tr>
      <td title={map?.description ?? ""}>{x.signal}</td>
      <td><span className={`tag ${x.status === "available" ? (x.stale ? "bad" : "measured") : x.status === "degraded" ? "estimated" : ""}`} title={x.stale ? GLOSSARY.stale : STATUS_LABEL[x.status]?.[1]}>{x.stale ? `not updating: ${x.age_minutes} min` : (STATUS_LABEL[x.status]?.[0] ?? x.status)}</span>{map?.overridden ? <span className="tag" title="You chose this entity on this page">chosen by you</span> : null}</td>
      <td className="muted" title={QUALITY_LABEL[x.quality]?.[1]}>{QUALITY_LABEL[x.quality]?.[0] ?? x.quality}</td>
      <td className="mono muted" title={x.source ?? ""}>
        {editing ? (
          <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <select value={choice} onChange={(e) => setChoice(e.target.value)} style={{ height: 32, fontSize: 12 }}>
              <option value="">(none)</option>
              {(map?.candidates ?? []).map(([eid, score, note]) => <option key={eid} value={eid}>{eid} · {Math.round(score * 100)}% {note}</option>)}
              {x.source && !(map?.candidates ?? []).some(([eid]) => eid === x.source) ? <option value={x.source}>{x.source}</option> : null}
            </select>
            <button type="button" className="chip primary" style={{ height: 32 }} disabled={busy} onClick={() => save(choice || null)}>Use</button>
            {map?.overridden && <button type="button" className="chip" style={{ height: 32 }} disabled={busy} onClick={() => save(null)}>Back to automatic</button>}
            <button type="button" className="chip" style={{ height: 32 }} onClick={() => setEditing(false)}>Cancel</button>
            {problem && <span className="err" style={{ fontSize: 12 }}>{problem}</span>}
          </span>
        ) : (x.source ? x.source.replace(/_/g, "_\u200b") : <span className="muted">none</span>)}
      </td>
      <td className="num">{unmapped ? "" : (x.last_text ?? (x.last_value != null ? x.last_value.toFixed(2) : ""))}</td>
      <td className="num">{unmapped ? "" : (x.age_minutes ?? "")}</td>
      <td className="muted">{x.notes}</td>
      <td>{editable && !editing && <button type="button" className="linkish" style={{ fontSize: 12 }} onClick={() => { setChoice(x.source ?? ""); setEditing(true); }}>Change</button>}</td>
    </tr>
  );
}

export default function SystemPage() {
  const [s, setS] = useState<SystemHealth | null>(null);
  const [mapping, setMapping] = useState<MappingView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.system().then(setS).catch((e) => setErr(String(e))); api.mapping().then(setMapping).catch(() => setMapping(null)); }, []);
  if (err) return <p className="err">{err}</p>;
  if (!s) return <p className="muted">Loading…</p>;
  const avail = s.signals.filter((x) => x.status === "available");
  return (
    <>
      <div className="grid">
        <div className="card"><h3>Operating mode</h3><div className="big" style={{ fontSize: "1.2rem" }}>{s.operating_mode}</div><div className="sub">change it on the Settings tab</div></div>
        <div className={`card ${s.control.holding ? "info" : ""}`}><h3>Control</h3><div className="big" style={{ fontSize: "1.2rem" }}>{!s.control.configured ? "Off" : s.control.holding ? `Holding ${s.control.commanded_c} °C` : "Armed"}</div><div className="sub">{s.control.configured ? `${s.control.entity} · ${s.control.last?.reason ?? "waiting for first cycle"}` : s.control.reason} · {s.record_counts.control_actions} log entries</div></div>
        <div className="card"><h3>Readings found<Term k="signals" /></h3><div className="big">{avail.length}<span className="muted" style={{ fontSize: "1rem" }}> of {s.signals.length}</span></div><div className="sub">{avail.filter((x) => x.stale).length} not updating</div></div>
        <div className="card"><h3>Readings stored</h3><div className="big">{s.record_counts.measurements.toLocaleString("en-GB")}</div><div className="sub">plus {s.record_counts.rate_intervals.toLocaleString("en-GB")} half-hour prices and {s.record_counts.dispatches} EV charging slots</div></div>
      </div>

      <div className="section">
        <h2>Learned models</h2>
        <LearnedModels />
      </div>

      <div className="section">
        <h2>Forecast accuracy</h2>
        {s.forecast_accuracy.status !== "ok" ? (
          <p className="muted">{s.forecast_accuracy.reason}</p>
        ) : (
          <div className="grid">
            <div className={`card ${s.forecast_accuracy.meets_target ? "info" : "warn"}`}>
              <h3>Typical indoor temperature error<Term k="forecast_error" /></h3>
              <div className="big">{s.forecast_accuracy.rmse_baseline_c?.toFixed(2)} °C</div>
              <div className="sub">over {s.forecast_accuracy.points} half-hours in {s.forecast_accuracy.runs} plans · target under {s.forecast_accuracy.target_rmse_c} °C before control is trusted</div>
            </div>
            <div className="card">
              <h3>Runs warm or cold<Term k="bias" /></h3>
              <div className="big">{(s.forecast_accuracy.bias_baseline_c ?? 0) > 0 ? "+" : ""}{s.forecast_accuracy.bias_baseline_c?.toFixed(2)} °C</div>
              <div className="sub">positive means the model runs warm · mean absolute error {s.forecast_accuracy.mae_baseline_c?.toFixed(2)} °C</div>
            </div>
          </div>
        )}
        {s.forecast_accuracy.cost_backtest?.status === "ok" && (
          <p style={{ fontSize: 13 }}>Cost check: the model expected the heating to cost {pounds(s.forecast_accuracy.cost_backtest.predicted_pence ?? 0)} over {s.forecast_accuracy.cost_backtest.slots} scored half-hours; it actually cost {pounds(s.forecast_accuracy.cost_backtest.actual_pence ?? 0)}{s.forecast_accuracy.cost_backtest.error_pct != null ? ` (${s.forecast_accuracy.cost_backtest.error_pct > 0 ? "+" : ""}${s.forecast_accuracy.cost_backtest.error_pct}%)` : ""}, using the prices PredHeat had at the time.<Term text="Scores the plan on what it knew when it was made: predicted thermostat cost per half hour against the heating electricity actually used, at the stored prices." /></p>
        )}
        {s.forecast_accuracy.note && <p className="muted" style={{ fontSize: 12 }}>{s.forecast_accuracy.note}</p>}
      </div>

      {s.missing_for_thermal_model.length > 0 && (
        <div className="note urgent">Thermal modelling cannot start: missing {s.missing_for_thermal_model.join(", ")}.</div>
      )}
      {s.warnings.map((w, i) => <div className={`note ${/competing|EnergyPLUS|EEBUS|not exposed/i.test(w) ? "warn" : ""}`} key={i}>{w}</div>)}

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
          <thead><tr><th>Integration</th><th>Present</th><th className="num">Entities</th><th className="num">Live</th><th>Set-up state</th></tr></thead>
          <tbody>{s.integrations.map((i) => (
            <tr key={i.platform}><td>{i.platform}</td><td>{i.present ? "yes" : "no"}</td><td className="num">{i.entities}</td><td className="num">{i.live_entities}</td><td className="muted">{i.config_state ?? ""}</td></tr>
          ))}</tbody>
        </table></div>
      </div>

      <div className="section">
        <h2>Signal mapping and freshness</h2>
        <div className="tablewrap"><table>
          <thead><tr><th>Signal<Term k="signals" /></th><th>Status</th><th>Data quality</th><th>Home Assistant entity</th><th className="num">Last value</th><th className="num">Age (minutes)</th><th>Notes</th><th></th></tr></thead>
          <tbody>{s.signals.map((x) => (
            <MappingRow key={x.signal} x={x} map={mapping?.signals.find((m) => m.signal === x.signal)} editable={!!mapping?.editable} onChange={setMapping} />
          ))}</tbody>
        </table></div>
      </div>

      <div className="section">
        <h2>Recent data quality events<Term k="events" /></h2>
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
