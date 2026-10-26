import type { ShadowPlan } from "../api";

const fmt = (iso: string, withDay: boolean) =>
  new Date(iso).toLocaleString("en-GB", withDay ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" });

/** "Tue 04:30 to 05:30", or "04:30 to 05:30" when both ends fall on the same day as now. */
export function when(start: string, end: string): string {
  const s = new Date(start), e = new Date(end), today = new Date().toDateString();
  const sameDay = s.toDateString() === today && e.toDateString() === today;
  return `${fmt(start, !sameDay)} to ${fmt(end, false)}`;
}

export function PlanList({ plan, compact = false }: { plan: ShadowPlan; compact?: boolean }) {
  if (plan.status === "not_available") return <p className="muted">{plan.reason}</p>;
  if (plan.actions.length === 0) return <p className="muted">Nothing worth shifting in the next 24 hours. {plan.reason}</p>;
  return (
    <div className="tablewrap">
      {plan.cost && (
        <p className="muted" style={{ margin: "0 0 10px", fontSize: 13 }}>
          {plan.status === "model_based" ? "Model-based plan. " : ""}Space heating about £{(plan.cost.optimised_p / 100).toFixed(2)} optimised against £{(plan.cost.baseline_p / 100).toFixed(2)} for a plain thermostat, saving {plan.cost.saving_p.toFixed(0)}p over 24 hours.
        </p>
      )}
      <table>
        <thead>
          <tr><th>When</th><th>Action</th>{!compact && <th>Rate</th>}<th>Why</th></tr>
        </thead>
        <tbody>
          {plan.actions.map((a, i) => (
            <tr key={i}>
              <td style={{ whiteSpace: "nowrap" }}>{when(a.start, a.end)}</td>
              <td style={{ whiteSpace: "nowrap" }}><span className={`tag ${a.kind}`}>{a.label}</span></td>
              {!compact && <td>{a.rate_p == null ? "–" : `${a.rate_p.toFixed(1)}p`}{a.saving_p_per_kwh ? ` (saves ~${a.saving_p_per_kwh.toFixed(0)}p/kWh)` : ""}</td>}
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
