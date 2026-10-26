import { useEffect, useState } from "react";
import { api, num, pounds, type TariffComparison } from "../api";
import { Term } from "../glossary";

export default function TariffsPage() {
  const [days, setDays] = useState(30);
  const [t, setT] = useState<TariffComparison | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setT(null); api.tariffs(days).then(setT).catch((e) => setErr(String(e))); }, [days]);
  if (err) return <p className="err">{err}</p>;
  if (!t) return <p className="muted">Loading…</p>;
  if (t.status === "implausible") return <div className="note urgent">{t.reason}</div>;
  if (t.status !== "ok") return <p className="muted">{t.reason}</p>;
  const best = t.results.find((r) => r.replayable);
  return (
    <>
      <div className="row">
        <label>Replay <select value={days} onChange={(e) => setDays(+e.target.value)}>
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option><option value={365}>Last year</option>
        </select></label>
        <span className="muted">{num(t.kwh, 0, " kWh")} of {t.source === "whole_home_import" ? "whole-home import" : "heat pump electricity only"} · rates {t.rates_as_of}{t.region ? ` · your region ${t.region}${t.region_name ? ` (${t.region_name})` : ""}` : ""}</span>
      </div>
      {t.actual && !t.actual.prices_complete && (
        <div className="note warn">Some half-hourly prices for this period are missing, so "What you paid" is understated and the comparison against it is unreliable. The replays on the right are unaffected.</div>
      )}
      <div className="grid">
        {t.actual && (
          <div className="card">
            <h3>What you paid<Term text="Your actual half-hourly consumption priced at the rates you were on at the time, plus standing charges." /></h3>
            <div className="big">{pounds(t.actual.total_pence)}</div>
            <div className="sub">{t.actual.code} · {t.actual.effective_p_per_kwh.toFixed(1)}p per kWh effective{t.actual.prices_complete ? "" : " · some prices missing"}</div>
          </div>
        )}
        {best && (
          <div className={`card ${t.actual && t.actual.difference_vs_best_pence != null && t.actual.difference_vs_best_pence > 50 ? "info" : ""}`}>
            <h3>Cheapest for your usage<Term k="replay" /></h3>
            <div className="big">{pounds(best.total_pence ?? 0)}</div>
            <div className="sub">{best.name}{t.actual?.on_cheapest ? " · you are on this tariff" : t.actual?.difference_vs_best_pence != null && t.actual.prices_complete ? (t.actual.difference_vs_best_pence > 0 ? ` · ${pounds(t.actual.difference_vs_best_pence)} less than you paid` : " · what you paid was lower") : ""}</div>
          </div>
        )}
        {best && (
          <div className="card">
            <h3>A year at this rate<Term k="annualised" /></h3>
            <div className="big">{pounds((best.annualised_pounds ?? 0) * 100).replace(/\.00$/, "")}</div>
            <div className="sub">{best.name}, scaled from {t.days} days · indicative</div>
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
                  <td><strong>{r.name}</strong><div className="muted" style={{ fontSize: 12 }}>{r.description}</div></td>
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
                  <td className="muted" style={{ fontSize: 12 }}>{r.requires && r.requires.length > 0 ? `Needs ${r.requires.join(", ").replace("_", " ")}. ` : ""}{r.note}</td>
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
