import { useEffect, useState } from "react";
import { api, MODE_LABEL, type SettingsDoc } from "../api";

const ORDER = ["operating_mode", "control_max_offset_c", "gas_price_p_per_kwh", "boiler_efficiency"];

export default function SettingsPage() {
  const [doc, setDoc] = useState<SettingsDoc | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => {
    api.settings().then((d) => { setDoc(d); setDraft(Object.fromEntries(Object.entries(d.values).map(([k, v]) => [k, String(v)]))); }).catch((e) => setErr(String(e)));
  }, []);
  if (err && !doc) return <p className="err">{err}</p>;
  if (!doc) return <p className="muted">Loading…</p>;

  const clientCheck = (key: string, raw: string): string | null => {
    const f = doc.fields[key];
    if (f.type === "enum") return f.choices?.includes(raw) ? null : "Choose one of the options";
    const v = Number(raw);
    if (raw.trim() === "" || Number.isNaN(v)) return "Enter a number";
    if (f.min != null && v < f.min) return `Must be at least ${f.min}`;
    if (f.max != null && v > f.max) return `Must be at most ${f.max}`;
    return null;
  };
  const save = async (key: string) => {
    setErr(null); setSaved(null);
    const problem = clientCheck(key, draft[key]);
    if (problem) { setErr(`${doc.fields[key].label}: ${problem}`); return; }
    try {
      const d = await api.saveSettings({ [key]: doc.fields[key].type === "enum" ? draft[key] : Number(draft[key]) });
      setDoc(d); setSaved(`${doc.fields[key].label} saved and applied`);
    } catch (e) { setErr(String((e as Error).message)); }
  };
  const reset = async (key: string) => {
    setErr(null); setSaved(null);
    try { const d = await api.resetSetting(key); setDoc(d); setDraft((x) => ({ ...x, [key]: String(d.values[key]) })); setSaved(`${doc.fields[key].label} reset to default`); }
    catch (e) { setErr(String((e as Error).message)); }
  };
  const gateOff = !doc.gate.control_possible;

  return (
    <>
      <div className="grid">
        <div className="card"><h3>Operating mode</h3><div className="big" style={{ fontSize: "1.2rem" }}>{MODE_LABEL[doc.effective.operating_mode] ?? doc.effective.operating_mode}</div><div className="sub">in effect now</div></div>
        <div className={`card ${doc.gate.control_possible ? "info" : ""}`}><h3>Control gate</h3><div className="big" style={{ fontSize: "1.2rem" }}>{doc.gate.control_possible ? "On" : "Off"}</div><div className="sub">{doc.gate.allow_control ? "Allow control is on" : "Allow control is off"} · set in the add-on configuration</div></div>
        <div className="card"><h3>Climate entity</h3><div className="big" style={{ fontSize: "1rem", wordBreak: "break-all" }}>{doc.gate.climate_entity ?? "none"}</div><div className="sub">the only entity PredHeat may write to</div></div>
        <div className="card"><h3>Data source</h3><div className="big" style={{ fontSize: "1.2rem" }}>{doc.effective.data_mode === "simulated" ? "Simulated" : "Home Assistant"}</div><div className="sub">set in the add-on configuration</div></div>
      </div>
      {err && <div className="note urgent">{err}</div>}
      {saved && <div className="note">{saved}</div>}
      <div className="section">
        <h2>Preferences</h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Changes apply immediately, no restart. The full explanation of each mode and of control is on the add-on's Documentation tab.</p>
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          {ORDER.map((key) => {
            const f = doc.fields[key];
            const dirty = draft[key] !== String(doc.values[key]);
            return (
              <div className="card" key={key}>
                <h3>{f.label}{f.unit ? <span className="muted"> ({f.unit})</span> : null}</h3>
                {f.type === "enum" ? (
                  <select value={draft[key]} onChange={(e) => setDraft((x) => ({ ...x, [key]: e.target.value }))} style={{ width: "100%" }}>
                    {f.choices?.map((c) => {
                      const controlling = c === "balanced" || c === "max_savings";
                      return <option key={c} value={c} disabled={controlling && gateOff}>{(f.labels?.[c] ?? c) + (controlling && gateOff ? " (enable control in the add-on configuration)" : "")}</option>;
                    })}
                  </select>
                ) : (
                  <input type="number" value={draft[key]} min={f.min} max={f.max} step={f.step} onChange={(e) => setDraft((x) => ({ ...x, [key]: e.target.value }))} style={{ width: "100%", font: "inherit", fontSize: 20, fontWeight: 300, padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--background)", color: "var(--foreground)" }} />
                )}
                <div className="sub" style={{ marginTop: 8 }}>{f.help}{key === "operating_mode" && gateOff ? ` ${doc.gate.message}.` : ""}</div>
                <div className="row" style={{ marginTop: 10, marginBottom: 0 }}>
                  <button className="chip" onClick={() => save(key)} disabled={!dirty} style={{ background: dirty ? "var(--primary-fill)" : undefined, color: dirty ? "var(--primary-deep)" : undefined, borderColor: dirty ? "var(--primary-fill)" : undefined, cursor: dirty ? "pointer" : "default" }}>Save</button>
                  <button className="chip" onClick={() => reset(key)} disabled={String(doc.values[key]) === String(f.default)} style={{ cursor: "pointer" }}>Reset to default ({f.type === "enum" ? (f.labels?.[String(f.default)] ?? f.default) : f.default}{f.unit ? ` ${f.unit}` : ""})</button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
