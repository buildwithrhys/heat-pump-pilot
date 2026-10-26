import { num, type HeatLoss, type HeatPump, type EndUseEfficiency } from "../api";

/** Small "i" with a browser tooltip, for units and terms. */
const Info = ({ text }: { text: string }) => <span title={text} aria-label={text} style={{ cursor: "help", marginLeft: 6, color: "var(--tertiary)", fontSize: 11, border: "1px solid var(--border)", borderRadius: "50%", padding: "0 5px" }}>i</span>;

function Waiting({ title, reason, tip }: { title: string; reason?: string; tip: string }) {
  return (
    <div className="card">
      <h3>{title}<Info text={tip} /></h3>
      <div className="big" style={{ fontSize: "1.1rem" }}>Waiting for data</div>
      <div className="sub">{reason}</div>
    </div>
  );
}

const signed = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(0)}%`;

export function YourHouse({ hl, hp }: { hl: HeatLoss; hp: HeatPump }) {
  const ok = hl.status === "ok" && hl.design_kw != null;
  return (
    <div className="grid">
      {!ok ? (
        <Waiting title="Heat loss" reason={hl.reason} tip="Heat loss is how much heating power the house needs to hold 21 °C inside at the design outdoor temperature, the way a heat loss survey quotes it." />
      ) : (
        <div className={`card ${hl.survey_disagrees ? "warn" : ""}`}>
          <h3>Heat loss<Info text={`${hl.heat_loss_w_per_k} watts per °C of inside/outside difference (range ${hl.ci_low_w_per_k} to ${hl.ci_high_w_per_k}). Design temperature ${hl.design_outdoor_c} °C for ${hl.design_region}; change it in Settings.`} /></h3>
          <div className="big">{num(hl.design_kw, 1, " kW")}</div>
          <div className="sub">
            at {hl.design_outdoor_c} °C outside, {hl.design_indoor_c} °C inside · likely {num(hl.design_kw_low, 1)} to {num(hl.design_kw_high, 1)} kW
            {hl.survey ? ` · survey said ${num(hl.survey.kw_at_design, 2, " kW")}${hl.survey_difference_pct != null ? ` (${signed(hl.survey_difference_pct)})` : ""}${hl.survey_disagrees ? ", worth a second look" : ""}` : ""}
            {hl.rated_kw != null && hl.rated_margin_kw != null ? ` · your ${num(hl.rated_kw, 0, " kW")} heat pump ${hl.rated_margin_kw >= 0 ? `covers this with ${num(hl.rated_margin_kw, 1, " kW")} to spare` : `falls ${num(-hl.rated_margin_kw, 1, " kW")} short`}` : ""}
          </div>
        </div>
      )}
      {ok && hl.cooling_rate_c_per_h_at_5c != null ? (
        <div className="card">
          <h3>How fast it cools<Info text={`Time constant ${hl.time_constant_h} hours; the fabric stores about ${hl.capacity_kwh_per_k} kWh per °C${hl.internal_gain_kw != null ? `; internal gains about ${hl.internal_gain_kw} kW` : ""}.`} /></h3>
          <div className="big">{num(hl.cooling_rate_c_per_h_at_5c, 1, " °C/h")}</div>
          <div className="sub">with the heating off at 5 °C outside, starting from 21 °C</div>
        </div>
      ) : ok ? (
        <Waiting title="How fast it cools" reason="The heat loss is known but the house's thermal mass is not pinned down yet. It needs a few periods with the heating off." tip="How quickly the indoor temperature falls with the heating off, which depends on heat loss and the thermal mass of the fabric." />
      ) : null}
      <Efficiency e={hp.heating} lookback={hp.lookback_days} why={hp.why_differs} />
      <Efficiency e={hp.hot_water} lookback={hp.lookback_days} why={hp.why_differs} />
      {ok && (
        <div className="card">
          <h3>Based on<Info text={`Half-hour readings where indoor temperature, outdoor temperature and heat delivered were all present. Heating ran ${num(hl.heating_hours, 0)} hours in the window.`} /></h3>
          <div className="big" style={{ fontSize: "1.1rem" }}>{hl.samples.toLocaleString("en-GB")} half-hour readings</div>
          <div className="sub">over {hl.days_with_data} {hl.days_with_data === 1 ? "day" : "days"} (looks back {hl.lookback_days} days){hl.outdoor_range_c ? `, ${hl.outdoor_range_c[0]} to ${hl.outdoor_range_c[1]} °C outside` : ""}</div>
        </div>
      )}
    </div>
  );
}

function Efficiency({ e, lookback, why }: { e: EndUseEfficiency; lookback: number; why: string }) {
  const tip = `COP (coefficient of performance): kWh of heat delivered per kWh of electricity used, from the controller's own energy counters. ${e.running_hours} running hours over ${e.days_with_data} days; looks back ${lookback} days.`;
  if (e.status !== "ok" || e.observed_cop == null) {
    return <Waiting title={`${e.label} efficiency`} reason={e.reason} tip={tip} />;
  }
  return (
    <div className="card">
      <h3>{e.label} efficiency<Info text={tip} /></h3>
      <div className="big">{num(e.observed_cop, 1)}</div>
      <div className="sub">
        average COP, {e.label.toLowerCase()}, last {e.days_with_data} {e.days_with_data === 1 ? "day" : "days"}{e.outdoor_range_c ? `, ${e.outdoor_range_c[0]} to ${e.outdoor_range_c[1]} °C outside` : ""}
        {e.cop_at_7 != null ? <><br />Estimated {num(e.cop_at_7, 1)} at 7 °C outside (the standard comparison point)</> : null}
        {e.cop_at_minus_2 != null ? <><br />Estimated {num(e.cop_at_minus_2, 1)} at −2 °C outside</> : null}
      </div>
      <div className="sub" style={{ marginTop: 6 }}>{why}</div>
    </div>
  );
}
