import { useEffect, useState } from "react";
import { CartesianGrid, ComposedChart, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { api, num, type HeatingCurve, type CurveSetting } from "../api";
import { Term, tip } from "../glossary";

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const day = (iso: string) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" });
const fmt = (key: string, v: number | null | undefined) => (v == null ? "–" : key === "heating_curve" ? v.toFixed(2) : `${v.toFixed(0)} °C`);
const termFor: Record<string, string> = { heating_curve: "heating_curve", min_flow: "min_flow", cutoff: "outdoor_cutoff" };

type Pending = { setting: CurveSetting; value: number; undo?: boolean };

/** The heating curve check: what the house needs against what the controller asks for, with a confirmed Change and an Undo. */
export function HeatingCurveCheck() {
  const [c, setC] = useState<HeatingCurve | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.heatingCurve().then(setC).catch((e) => setErr(String(e.message ?? e))); }, []);
  if (err) return <p className="err">Could not load the heating curve check: {err}</p>;
  if (!c) return <p className="muted">Loading…</p>;

  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      setC(pending.undo ? await api.undoCurve(pending.setting.key) : await api.applyCurve(pending.setting.key, pending.value));
      setPending(null);
      setErr(null);
    } catch (e) {
      setErr(String((e as Error).message));
      setPending(null);
    } finally {
      setBusy(false);
    }
  };

  const ba = c.before_after;
  const beforeAfter = ba ? (
    <div className={`card ${ba.efficiency_change_pct != null && ba.efficiency_change_pct > 0 ? "info" : ""}`}>
      <h3>Since your last change<Term k="curve_before_after" /></h3>
      <div className="big" style={ba.status !== "ok" ? { fontSize: "1.1rem" } : undefined}>
        {ba.status === "ok" && ba.efficiency_change_pct != null ? `${ba.efficiency_change_pct > 0 ? "+" : ""}${ba.efficiency_change_pct.toFixed(0)}% efficiency` : "Measuring"}
      </div>
      <div className="sub" title={ba.before && ba.after ? `Average COP ${num(ba.before.cop, 2)} the week before; ${num(ba.after.cop, 2)} since, against ${num(ba.expected_cop_after ?? null, 2)} expected for the weather.` : undefined}>
        {ba.change.label} {fmt(ba.change.setting, ba.change.from)} to {fmt(ba.change.setting, ba.change.to)} on {day(ba.change.at)}
        {ba.status !== "ok" ? `. ${ba.reason}` : ba.comfort_change_c != null ? `, room ${Math.abs(ba.comfort_change_c) < 0.15 ? "as warm as before" : `${Math.abs(ba.comfort_change_c).toFixed(1)} °C ${ba.comfort_change_c > 0 ? "warmer" : "cooler"} than before`}` : ""}
      </div>
    </div>
  ) : null;

  const undoable = Object.entries(c.last_change).filter(([k, v]) => v?.can_undo && c.writable[k]);
  const undoLinks = undoable.map(([k, v]) => {
    const s = c.settings?.find((x) => x.key === k) ?? { key: k as CurveSetting["key"], label: c.writable[k].label, now: v!.to != null ? Number(v!.to) : null, suggested: null, why: "" };
    return (
      <button key={k} type="button" className="linkish" style={{ marginLeft: 8 }} title={`You changed this on ${day(v!.at)}.`}
              onClick={() => setPending({ setting: s, value: Number(v!.from), undo: true })}>Undo {s.label.toLowerCase()} (back to {fmt(k, Number(v!.from))})</button>
    );
  });
  const dialog = pending && (
    <div className="modal-backdrop" onClick={() => !busy && setPending(null)}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <h3 style={{ marginTop: 0 }}>{pending.undo ? "Put back" : "Change"} {pending.setting.label.toLowerCase()} to {fmt(pending.setting.key, pending.value)}?</h3>
        <p style={{ fontSize: 14 }}>
          PredHeat will set <code>{c.writable[pending.setting.key]?.entity_id}</code> from {fmt(pending.setting.key, pending.setting.now)} to {fmt(pending.setting.key, pending.value)} on your Vaillant controller, once.
          {pending.undo ? "" : " It will not change it again by itself. You can undo it here at any time."}
        </p>
        {!pending.undo && pending.setting.key === "heating_curve" && c.direction === "lower" && (
          <p style={{ fontSize: 14 }}>Rooms will warm up a little more slowly after a setback. If the house does not reach your setpoint in the next cold spell, raise it again.</p>
        )}
        {c.writable[pending.setting.key]?.ambiguous && <p className="note warn">More than one matching setting was found in Home Assistant; PredHeat picked the main heating circuit. Check the entity name above.</p>}
        <div className="row" style={{ justifyContent: "flex-end", marginBottom: 0 }}>
          <button type="button" className="chip" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
          <button type="button" className="chip primary" disabled={busy} onClick={confirm}>{busy ? "Saving…" : pending.undo ? "Put back" : "Change"}</button>
        </div>
      </div>
    </div>
  );

  if (c.status !== "ok") {
    return (
      <>
        <div className="grid">
          <div className="card">
            <h3>Heating curve<Term k="heating_curve" /></h3>
            <div className="big" style={{ fontSize: "1.1rem" }}>{c.since_change ? "Checking the new setting" : "Waiting for data"}</div>
            <div className="sub">{c.reason}</div>
          </div>
          {beforeAfter}
        </div>
        {undoLinks.length > 0 && <p className="sources-line" style={{ marginTop: 8 }}>Changed your mind?{undoLinks}</p>}
        {dialog}
      </>
    );
  }

  const curve = c.settings?.find((s) => s.key === "heating_curve");
  const headline = c.direction === "keep" ? "About right" : c.suggested_curve != null ? `${c.direction === "lower" ? "Lower" : "Raise"} to ${c.suggested_curve.toFixed(2)}` : c.direction === "lower" ? "Could be lower" : "Could be higher";
  const conf = c.confidence ?? 0;
  const confWord = conf >= 0.7 ? "high" : conf >= 0.4 ? "moderate" : "low";
  const anyWritable = Object.keys(c.writable).length > 0;
  const grid = css("--grid"), ink2 = css("--ink-2");

  return (
    <>
      <div className="grid">
        <div className={`card ${c.direction === "lower" ? "info" : c.direction === "raise" ? "warn" : ""}`}>
          <h3>Heating curve<Term k="heating_curve" /></h3>
          <div className="big">{headline}</div>
          <div className="sub" title={`${tip("curve_confidence")} Confidence: ${confWord} (${Math.round(conf * 100)}%).`}>
            now {curve?.now != null ? curve.now.toFixed(2) : "not reported"} · {c.direction === "lower" ? `about ${num(c.saving_pct ?? 0, 0)}% cheaper space heating, last ${c.lookback_days} days' weather` : c.direction === "raise" ? "to hold your setpoint in cold weather" : `already close to what the house needs, last ${c.lookback_days} days`}
          </div>
        </div>
        <div className={`card ${c.coverage && !c.coverage.cold_end_checked ? "warn" : ""}`}>
          <h3>Weather checked<Term text="The outdoor temperatures in the hours this check used, when the heat pump was heating the house steadily. The suggestion only holds for weather it has seen." /></h3>
          <div className="big">{num(c.coverage?.t_min, 0)} to {num(c.coverage?.t_max, 0, " °C")}</div>
          <div className="sub">outside, over {c.coverage?.running_hours} hours of heating in the last {c.lookback_days} days{c.coverage && !c.coverage.cold_end_checked ? ". Not yet seen below 3 °C: check again after a cold spell" : ""}</div>
        </div>
        <div className="card">
          <h3>Your radiators<Term k="radiator_fit" /></h3>
          <div className="big">{num(c.emitter?.output_at_50c_flow_kw, 1, " kW")}</div>
          <div className="sub" title={`Fitted emitter constant ${c.emitter?.k_kw_per_k13} kW/K^1.3 (spread ${c.emitter?.spread}) from ${c.emitter?.hours} hours.${c.emitter?.return_reported ? "" : ` Return temperature not reported, so a ${c.emitter?.assumed_flow_return_difference_c} °C flow-to-return difference is assumed.`}`}>
            of heat with 50 °C water, measured from your own data
          </div>
        </div>
        {beforeAfter}
      </div>

      {c.chart && c.chart.length > 0 && (
        <div className="chart" style={{ marginTop: 12 }}>
          <h4>Water temperature for each outdoor temperature, °C</h4>
          <div className="legend">
            <span style={{ ["--c" as string]: css("--s2") }}>What your controller asks for now</span>
            {c.direction !== "keep" && <span style={{ ["--c" as string]: css("--s1") }}>With the suggested setting</span>}
            <span style={{ ["--c" as string]: css("--axis") }} title={tip("needed_flow")}>What your house needs (including a 1 °C margin)</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={c.chart} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={grid} vertical={false} />
              <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 11, fill: ink2 }} tickFormatter={(v) => `${v} °C`} axisLine={{ stroke: grid }} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: ink2 }} width={36} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip formatter={(v, n) => [`${Number(v).toFixed(1)} °C`, n === "now" ? "Asked for now" : n === "suggested" ? "With the suggestion" : "House needs"]} labelFormatter={(v) => `${v} °C outside`} />
              <Line type="monotone" dataKey="now" stroke={css("--s2")} strokeWidth={2} dot={false} />
              {c.direction !== "keep" && <Line type="monotone" dataKey="suggested" stroke={css("--s1")} strokeWidth={2} dot={false} />}
              <Scatter dataKey="needed" fill={css("--axis")} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {(c.notes ?? []).map((n, i) => <p key={i} className="note warn">{n}</p>)}

      <div className="tablewrap" style={{ marginTop: 12 }}>
        <table>
          <thead><tr><th>Setting</th><th>Now</th><th>Suggested</th><th>Why</th><th></th></tr></thead>
          <tbody>
            {(c.settings ?? []).map((s) => {
              const w = c.writable[s.key];
              const last = c.last_change[s.key];
              return (
                <tr key={s.key}>
                  <td>{s.label}<Term k={termFor[s.key]} /></td>
                  <td>{fmt(s.key, s.now)}</td>
                  <td>{s.suggested != null ? fmt(s.key, s.suggested) : "no change"}</td>
                  <td style={{ maxWidth: 360 }}>{s.why}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {w && s.suggested != null && <button type="button" className="chip primary" onClick={() => setPending({ setting: s, value: s.suggested! })}>Change to {fmt(s.key, s.suggested)}</button>}
                    {w && last?.can_undo && (
                      <button type="button" className="linkish" style={{ marginLeft: 8 }} title={`You changed this on ${day(last.at)}.`}
                              onClick={() => setPending({ setting: s, value: Number(last.from), undo: true })}>Undo (back to {fmt(s.key, Number(last.from))})</button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="sources-line" style={{ marginTop: 8 }}>
        {anyWritable
          ? "Change sets the value on your Vaillant controller once, through Home Assistant, and only when you confirm. PredHeat never changes these settings by itself."
          : "Home Assistant does not offer these settings as adjustable entities on your system, so change them in the myVAILLANT app."}
        {" "}Lower the curve one step at a time and give it a few days; if a room feels cold, put it back.<Term k="curve_margin" />
      </p>

      {dialog}
    </>
  );
}
