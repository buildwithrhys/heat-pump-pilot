import { useEffect, useRef, useState } from "react";
import { announceMode, api, MODE_LABEL, type ClimateEntity, type Preconditions, type SettingsDoc, type SettingsField } from "../api";

type Val = number | string | boolean | null;
const GROUPS: { key: "control" | "comparisons" | "advanced"; title: string; blurb: string }[] = [
  { key: "control", title: "Control", blurb: "Whether PredHeat may act, on what, and how far." },
  { key: "comparisons", title: "Comparisons", blurb: "Figures used for the gas comparison and the survey check on the Overview." },
  { key: "advanced", title: "Advanced", blurb: "Rarely needed; the defaults suit most homes." },
];
const ORDER: Record<string, string[]> = {
  control: ["control_enabled", "control_climate_entity", "control_dhw_entity", "operating_mode", "control_max_offset_c", "trial_mode"],
  comparisons: ["gas_price_p_per_kwh", "gas_standing_charge_p_per_day", "boiler_efficiency", "survey_heat_loss_kw", "survey_outdoor_c", "heat_pump_rated_kw"],
  advanced: ["use_predbat", "publish_for_predbat", "design_outdoor_c"],
};

export default function SettingsPage() {
  const [doc, setDoc] = useState<SettingsDoc | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [entities, setEntities] = useState<ClimateEntity[]>([]);
  const [heaters, setHeaters] = useState<ClimateEntity[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Preconditions | null>(null);
  const [ack, setAck] = useState(false);
  const timer = useRef<number | null>(null);

  const adopt = (d: SettingsDoc) => {
    setDoc(d);
    setDraft(Object.fromEntries(Object.entries(d.values).map(([k, v]) => [k, v == null ? "" : String(v)])));
    announceMode(d.effective.operating_mode, d.effective.control_enabled && !!d.effective.climate_entity);
  };
  useEffect(() => {
    api.settings().then(adopt).catch((e) => setErr(String(e)));
    api.climateEntities().then((r) => { setEntities(r.entities); setHeaters(r.water_heaters ?? []); }).catch(() => setEntities([]));
  }, []);
  if (err && !doc) return <p className="err">{err}</p>;
  if (!doc) return <p className="muted">Loading…</p>;

  const flash = (key: string) => { setSavedKey(key); if (timer.current) window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setSavedKey(null), 2500); };
  const commit = async (key: string, value: Val, extra: Record<string, Val> = {}) => {
    setErr(null);
    try { adopt(await api.saveSettings({ [key]: value, ...extra })); flash(key); }
    catch (e) { setErr(String((e as Error).message)); setDraft((x) => ({ ...x, [key]: doc.values[key] == null ? "" : String(doc.values[key]) })); }
  };
  const check = (f: SettingsField, raw: string): string | null => {
    if (raw.trim() === "" && f.optional) return null;
    const v = Number(raw);
    if (raw.trim() === "" || Number.isNaN(v)) return "Enter a number";
    if (f.min != null && v < f.min) return `Must be at least ${f.min}`;
    if (f.max != null && v > f.max) return `Must be at most ${f.max}`;
    return null;
  };
  const blurNumber = (key: string) => {
    const f = doc.fields[key];
    const raw = draft[key] ?? "";
    const current = doc.values[key] == null ? "" : String(doc.values[key]);
    if (raw === current) return;
    const problem = check(f, raw);
    if (problem) { setErr(`${f.label}: ${problem}`); return; }
    commit(key, raw.trim() === "" ? null : Number(raw));
  };
  const toggleGate = async (on: boolean) => {
    if (!on) { commit("control_enabled", false); return; }
    try { setDialog(await api.preconditions()); setAck(false); } catch (e) { setErr(String((e as Error).message)); }
  };
  const confirmGate = async () => {
    if (!dialog) return;
    await commit("control_enabled", true, dialog.all_met ? {} : { acknowledged: true });
    setDialog(null);
  };
  const resetAll = async () => {
    setErr(null);
    try { adopt(await api.resetSetting()); flash("__all"); } catch (e) { setErr(String((e as Error).message)); }
  };
  const gateOn = !!doc.values.control_enabled;
  const possible = doc.gate.control_possible;
  const entity = (doc.values.control_climate_entity as string | null) ?? null;
  const Saved = ({ k }: { k: string }) => (savedKey === k ? <span className="tag measured" style={{ marginLeft: 8 }}>Saved</span> : null);

  const field = (key: string) => {
    const f = doc.fields[key];
    if (!f) return null;
    if (f.type === "bool" && key === "control_enabled") {
      return (
        <div className="card" key={key}>
          <h3>{f.label}<Saved k={key} /></h3>
          <label style={{ display: "flex", alignItems: "center", gap: 12, cursor: "pointer" }}>
            <span className={`switch ${gateOn ? "on" : ""}`} role="switch" aria-checked={gateOn} tabIndex={0} onClick={() => toggleGate(!gateOn)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") toggleGate(!gateOn); }} />
            <span style={{ fontSize: 20, fontWeight: 300 }}>{gateOn ? "On" : "Off"}</span>
          </label>
          <div className="sub" style={{ marginTop: 8 }}>{f.help}</div>
        </div>
      );
    }
    if (f.type === "entity") {
      const isDhw = key === "control_dhw_entity";
      const list = isDhw ? heaters : entities;
      const value = (doc.values[key] as string | null) ?? "";
      return (
        <div className="card" key={key}>
          <h3>{f.label}<Saved k={key} /></h3>
          <select value={value} onChange={(e) => commit(key, e.target.value || null)} style={{ width: "100%" }}>
            <option value="">{list.length ? (isDhw ? "None (hot water not controlled)" : "Choose a heating zone") : (isDhw ? "No water heater entities found yet" : "No climate entities found yet")}</option>
            {list.map((e) => <option key={e.entity_id} value={e.entity_id}>{e.name}{e.platform === "mypyllant" ? " (myVAILLANT)" : ""} · {e.entity_id}</option>)}
          </select>
          <div className="sub" style={{ marginTop: 8 }}>{f.help}{list.length === 0 ? " PredHeat lists the entities it finds in Home Assistant after its first collection." : ""}</div>
        </div>
      );
    }
    if (f.type === "bool") {
      const on = !!doc.values[key];
      return (
        <div className="card" key={key}>
          <h3>{f.label}<Saved k={key} /></h3>
          <label style={{ display: "flex", alignItems: "center", gap: 12, cursor: "pointer" }}>
            <span className={`switch ${on ? "on" : ""}`} role="switch" aria-checked={on} tabIndex={0} onClick={() => commit(key, !on)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") commit(key, !on); }} />
            <span style={{ fontSize: 20, fontWeight: 300 }}>{on ? "On" : "Off"}</span>
          </label>
          <div className="sub" style={{ marginTop: 8 }}>{f.help}</div>
        </div>
      );
    }
    if (f.type === "enum") {
      return (
        <div key={key} style={{ gridColumn: "1 / -1" }}>
          <h3 style={{ fontSize: 12, fontWeight: 500, letterSpacing: 0.2, color: "var(--muted)", margin: "0 0 8px" }}>{f.label}<Saved k={key} /></h3>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {f.choices?.map((c) => {
              const controlling = c === "balanced" || c === "max_savings";
              const disabled = controlling && !possible;
              const active = doc.values.operating_mode === c;
              return (
                <button key={c} type="button" disabled={disabled} onClick={() => commit(key, c)} className={`card radio ${active ? "active" : ""}`} style={{ textAlign: "left", cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.55 : 1, font: "inherit" }} aria-pressed={active}>
                  <h3 style={{ color: active ? "var(--primary)" : undefined }}>{f.labels?.[c] ?? c}{active ? <span className="tag measured">current</span> : null}</h3>
                  <div style={{ fontSize: 13 }}>{f.lines?.[c]}</div>
                  {disabled && <div className="sub">{doc.gate.message ?? "Needs Allow control on"}</div>}
                </button>
              );
            })}
          </div>
        </div>
      );
    }
    return (
      <div className="card" key={key}>
        <h3>{f.label}{f.unit ? <span className="muted"> ({f.unit})</span> : null}<Saved k={key} /></h3>
        <input type="number" value={draft[key] ?? ""} min={f.min} max={f.max} step={f.step} placeholder={f.optional ? "not set" : undefined}
          onChange={(e) => setDraft((x) => ({ ...x, [key]: e.target.value }))} onBlur={() => blurNumber(key)} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          style={{ width: "100%", font: "inherit", fontSize: 20, fontWeight: 300, padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--background)", color: "var(--foreground)" }} />
        <div className="sub" style={{ marginTop: 8 }}>{f.help}</div>
      </div>
    );
  };

  return (
    <>
      <p style={{ margin: "0 0 14px", fontSize: 14 }}>
        <strong>{MODE_LABEL[doc.effective.operating_mode] ?? doc.effective.operating_mode}</strong> · control {possible ? "on" : "off"} · climate entity: {entity ?? "none"}
      </p>
      {err && <div className="note urgent">{err}</div>}
      {GROUPS.map((g) => (
        <div className="section" key={g.key}>
          <h2>{g.title}</h2>
          <p className="muted" style={{ fontSize: 13, margin: "0 0 10px" }}>{g.blurb}</p>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>{ORDER[g.key].map(field)}</div>
        </div>
      ))}
      <p className="muted" style={{ fontSize: 13, marginTop: 20 }}>
        Changes save as you make them and apply at once. <button type="button" className="linkish" onClick={resetAll}>Reset all to defaults</button>{savedKey === "__all" ? <span className="tag measured" style={{ marginLeft: 8 }}>Reset</span> : null}. The full explanation of each mode and of control is on the add-on's Documentation tab.
      </p>

      {dialog && (
        <div className="modal-backdrop" onClick={() => setDialog(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <h2 style={{ fontSize: 18, textTransform: "none", letterSpacing: 0 }}>Allow PredHeat to control the heating?</h2>
            <p style={{ fontSize: 14 }}>PredHeat will move your room setpoint by up to {dialog.max_offset_c} °C (1.5 times that in Max savings, capped at 2 °C) when the operating mode is Balanced or Max savings. These are the preconditions and whether each is met now:</p>
            <ul style={{ listStyle: "none", padding: 0, margin: "0 0 12px" }}>
              {dialog.checks.map((c) => (
                <li key={c.key} style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                  <span className={`tag ${c.met ? "measured" : "bad"}`} style={{ marginLeft: 0 }}>{c.met ? "met" : "not met"}</span>
                  <span style={{ flex: 1 }}>{c.label}<span className="muted"> · {c.detail}</span></span>
                </li>
              ))}
            </ul>
            {!dialog.all_met && (
              <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14, marginBottom: 12 }}>
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} style={{ marginTop: 3 }} />
                <span>I understand PredHeat may move my room setpoint by up to {dialog.max_offset_c} °C even though not every precondition is met.</span>
              </label>
            )}
            <div className="row" style={{ justifyContent: "flex-end", marginBottom: 0 }}>
              <button type="button" className="chip" onClick={() => setDialog(null)}>Cancel</button>
              <button type="button" className="chip primary" disabled={!dialog.all_met && !ack} onClick={confirmGate}>Turn on</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
