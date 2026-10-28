import { useEffect, useState } from "react";
import { api, num, pounds, type TariffComparison } from "../api";
import { Term } from "../glossary";

export default function TariffsPage() {
  const [days, setDays] = useState(30);
  const [ev, setEv] = useState(0);
  const [battery, setBattery] = useState(0);
  const [live, setLive] = useState(false);
  const [t, setT] = useState<TariffComparison | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setT(null); api.tariffs(days, ev, battery, live).then(setT).catch((e) => setErr(String(e))); }, [days, ev, battery, live]);
  if (err) return <p className="err">{err}</p>;
  if (!t) return <p className="muted">Loading…</p>;
  if (t.status === "implausible") return <div className="note urgent">{t.reason}</div>;
  if (t.status !== "ok") return <p className="muted">{t.reason}</p>;
  const ranked = t.results.filter((r) => r.replayable);
  const best = ranked[0];
  return (
    <>
      <div className="row">
        <label>Replay <select value={days} onChange={(e) => setDays(+e.target.value)}>
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option><option value={365}>Last year</option>
        </select></label>
        <label>EV charging <input type="number" min={0} max={60} step={1} value={ev} onChange={(e) => setEv(Number(e.target.value) || 0)} style={{ width: 70, height: 38, borderRadius: 8, border: "1px solid var(--border)", padding: "0 8px", font: "inherit" }} /> kWh a day<Term text="What-if: adds this much charging in each day's cheapest slots on every tariff, where an EV would be scheduled to charge." /></label>
        <label>Battery <input type="number" min={0} max={40} step={1} value={battery} onChange={(e) => setBattery(Number(e.target.value) || 0)} style={{ width: 70, height: 38, borderRadius: 8, border: "1px solid var(--border)", padding: "0 8px", font: "inherit" }} /> kWh a day shifted<Term text="What-if: moves this much of each day's use from the dearest slots to the cheapest ones, as a home battery would." /></label>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Use published rates for my region<Term text="Fetches each tariff's current unit rates for your region from Octopus (no account needed) and makes Agile replayable. Takes a few seconds the first time." /></label>
        <span className="muted">{num(t.kwh, 0, " kWh")} of {t.source === "whole_home_import" ? "whole-home import" : "heat pump electricity only"} · rates {Object.keys(t.live_rates ?? {}).length ? "published for your region" : t.rates_as_of}{t.region ? ` · your region ${t.region}${t.region_name ? ` (${t.region_name})` : ""}` : ""}</span>
      </div>
      {t.annual?.status === "ok" ? (
        <p className="sources-line">A typical year for {t.annual.climate} ({t.annual.climate_source}): about {num(t.annual.annual_kwh, 0, " kWh")} of electricity{t.annual.upper_estimate ? ", an upper estimate because solar is not modelled and summer import will be lower" : ""}. The "year at this rate" figures use it.<Term text={t.annual.note ?? ""} /></p>
      ) : t.annual?.reason ? (
        <p className="sources-line">No yearly estimate yet. {t.annual.reason}</p>
      ) : null}
      {t.actual && (
        <p className="sources-line">Compared on {num(t.actual.coverage_pct, 0, "%")} of your electricity ({num(t.actual.kwh, 0, " kWh")} of {num(t.actual.kwh_total, 0, " kWh")}, {t.actual.days_priced} {t.actual.days_priced === 1 ? "day" : "days"}): the half-hours for which Heat Pump Pilot has your actual prices. The rest is left out of every figure below.<Term k="price_coverage" /></p>
      )}
      {t.reconciliation && t.reconciliation.difference_pct != null && Math.abs(t.reconciliation.difference_pct) > 5 && (
        <div className="note warn">Your tariff's standard rates, replayed on the same half-hours, come to {pounds(t.reconciliation.template_unit_pence)} against the {pounds(t.reconciliation.actual_unit_pence)} you actually paid ({t.reconciliation.difference_pct > 0 ? "+" : ""}{t.reconciliation.difference_pct.toFixed(0)}%). The difference is usually extra cheap Intelligent Octopus slots the standard rates cannot model, so the other tariffs below may look {t.reconciliation.difference_pct > 0 ? "dearer" : "cheaper"} than they would really be for you.<Term k="reconciliation" /></div>
      )}
      <div className="grid">
        {t.actual && (
          <div className="card">
            <h3>What you paid<Term text="Your actual half-hourly consumption priced at the rates you were on at the time, including any Intelligent Octopus slots, plus standing charges, on the half-hours that have a stored price." /></h3>
            <div className="big">{pounds(t.actual.total_pence)}</div>
            <div className="sub">{t.actual.effective_p_per_kwh != null ? `${t.actual.effective_p_per_kwh.toFixed(1)}p per kWh on average` : ""} over {t.actual.days_priced} {t.actual.days_priced === 1 ? "day" : "days"} with prices · {t.actual.code}</div>
          </div>
        )}
        {best && (
          t.actual?.on_cheapest ? (
            <div className="card info">
              <h3>Your tariff is the cheapest<Term k="replay" /></h3>
              <div className="big">{pounds(t.actual.total_pence)}</div>
              <div className="sub">every alternative would have cost more on the same half-hours; the nearest is {ranked[1] ? `${ranked[1].name} at ${pounds(ranked[1].total_pence ?? 0)}` : "none"}</div>
            </div>
          ) : (
            <div className={`card ${t.actual && t.actual.difference_vs_best_pence != null && t.actual.difference_vs_best_pence > 50 ? "info" : ""}`}>
              <h3>{t.actual ? "Cheaper than what you paid" : "Cheapest of the tariffs compared"}<Term k="replay" /></h3>
              <div className="big">{pounds(best.total_pence ?? 0)}</div>
              <div className="sub">{best.name}{t.actual?.difference_vs_best_pence != null ? ` · ${pounds(t.actual.difference_vs_best_pence)} less than you paid on the same half-hours` : " · no actual prices yet to compare against"}</div>
            </div>
          )
        )}
        {best && (
          <div className="card">
            <h3>A year at this rate<Term k="annualised" /></h3>
            <div className="big">{pounds((best.annualised_pounds ?? 0) * 100).replace(/\.00$/, "")}</div>
            <div className="sub">{best.name}, {t.annual?.status === "ok" ? `on a typical year's ${num(t.annual.annual_kwh, 0, " kWh")}${t.annual.upper_estimate ? " (upper estimate)" : ""}` : `scaled from ${best.days ?? t.days} days, rough until 14 days of clean data`}</div>
          </div>
        )}
      </div>
      <div className="section">
        <h2>Ranked by total cost for the period</h2>
        <div className="tablewrap">
          <table>
            <thead><tr><th>Tariff</th><th className="num">Unit charges</th><th className="num">Standing charges<Term k="standing_charge" /></th><th className="num">Total</th><th className="num">Average p/kWh</th><th className="num">Used in cheap hours<Term k="cheap_share" /></th><th>Notes</th></tr></thead>
            <tbody>
              {t.results.map((r) => (
                <tr key={r.code}>
                  <td><strong>{r.name}</strong>{r.current ? <span className="tag measured">your tariff, actual prices</span> : null}<div className="muted" style={{ fontSize: 12 }}>{r.description}</div></td>
                  {r.replayable ? (
                    <>
                      <td className="num">{pounds(r.unit_pence ?? 0)}</td>
                      <td className="num">{pounds(r.standing_pence ?? 0)}</td>
                      <td className="num"><strong>{pounds(r.total_pence ?? 0)}</strong></td>
                      <td className="num">{r.effective_p_per_kwh?.toFixed(1)}</td>
                      <td className="num">{r.cheap_share != null ? `${Math.round(r.cheap_share * 100)}%` : "–"}</td>
                    </>
                  ) : (
                    <td colSpan={5} className="muted">Needs Octopus API data, coming in a later release</td>
                  )}
                  <td className="muted" style={{ fontSize: 12 }}>{r.requires && r.requires.length > 0 ? `Needs ${r.requires.join(", ").replace("_", " ")}. ` : ""}{r.note}{r.rates_source === "octopus_api" ? <span className="tag measured">published rates</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          {t.caveats?.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </div>
    </>
  );
}
