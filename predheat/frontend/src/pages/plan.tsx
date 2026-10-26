import type { ShadowPlan } from "../api";
import { Term, tip } from "../glossary";

const fmt = (iso: string, withDay: boolean) =>
  new Date(iso).toLocaleString("en-GB", withDay ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });

/** "Tue 04:30 to 05:30", or "04:30 to 05:30" when both ends fall on the same day as now. */
export function when(start: string, end: string): string {
  const s = new Date(start), e = new Date(end), today = new Date().toDateString();
  const sameDay = s.toDateString() === today && e.toDateString() === today;
  return `${fmt(start, !sameDay)} to ${fmt(end, false)}`;
}

export function PlanList({ plan, compact = false, skipKey }: { plan: ShadowPlan; compact?: boolean; skipKey?: string }) {
  if (plan.status === "not_available") return <p className="muted">{plan.reason}</p>;
  const actions = skipKey ? plan.actions.filter((a) => `${a.kind}|${a.start}` !== skipKey) : plan.actions;
  if (plan.actions.length === 0) return <p className="muted">Nothing worth shifting in the next 24 hours. {plan.reason}</p>;
  if (actions.length === 0) return <p className="muted" style={{ fontSize: 12 }}>That is the only action in the next 24 hours.</p>;
  return (
    <div className="tablewrap">
      {plan.cost && (
        <p className="muted" style={{ margin: "0 0 10px", fontSize: 13 }}>
          {plan.status === "model_based" ? "Model-based plan. " : ""}Space heating about £{(plan.cost.optimised_p / 100).toFixed(2)} optimised against £{(plan.cost.baseline_p / 100).toFixed(2)} for a plain thermostat, saving {plan.cost.saving_p.toFixed(0)}p over 24 hours.
        </p>
      )}
      <table>
        <thead>
          <tr><th>When</th><th>Action</th>{!compact && <th>Price then<Term k="tariff_now" /></th>}<th>Why</th></tr>
        </thead>
        <tbody>
          {actions.map((a, i) => (
            <tr key={i}>
              <td style={{ whiteSpace: "nowrap" }}>{when(a.start, a.end)}</td>
              <td style={{ whiteSpace: "nowrap" }}><span className={`tag ${a.kind}`}>{a.label}</span></td>
              {!compact && <td>{a.rate_p == null ? "–" : `${a.rate_p.toFixed(1)}p per kWh`}{a.kind === "coast_space" ? <span className="muted" title={tip("coast_rate")}> (dear rate, avoided by the earlier pre-heat)</span> : a.saving_p_per_kwh && a.saving_basis === "vs_dear_period" ? <span title={tip("saving_vs")}> (about {a.saving_p_per_kwh.toFixed(0)}p per kWh cheaper than the dear period it prepares for)</span> : a.saving_p_per_kwh && a.saving_basis === "vs_average" ? <span> (about {a.saving_p_per_kwh.toFixed(0)}p per kWh below your average price over the plan)</span> : ""}</td>}
              <td className="muted">{a.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!compact && plan.assumptions && (
        <ul className="muted" style={{ fontSize: "0.85rem", marginTop: 8 }}>
          {plan.assumptions.map((s, i) => <li key={i}>{s}</li>)}
        </ul>
      )}
    </div>
  );
}
