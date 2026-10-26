import { ResponsiveContainer, Sankey, Tooltip } from "recharts";
import type { Costs } from "../api";
import { GLOSSARY } from "../glossary";

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const kwh = (v: number, dp = 1) => v.toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });

/**
 * Where the heat pump's energy came from and went. Scoped to the heat pump: the
 * inputs (electricity by origin, free heat from the outside air) balance the
 * outputs (space heating, hot water, standby and backup) by construction, and any
 * residual is drawn as "unaccounted" rather than hidden.
 */
export function EnergyFlow({ costs, title, days = 1 }: { costs: Costs; title: string; days?: number }) {
  const shElec = costs.space_heating.kwh_electric;
  const dhwElec = costs.hot_water.kwh_electric;
  const hpElec = costs.heat_pump_total.kwh_electric;
  const other = Math.max(hpElec - shElec - dhwElec, 0); // standby + backup heater
  const shHeat = costs.space_heating.kwh_heat ?? 0;
  const dhwHeat = costs.hot_water.kwh_heat ?? 0;
  const envSh = shHeat - shElec;
  const envDhw = dhwHeat - dhwElec;
  const imp = costs.whole_home_import.plausible === false ? 0 : costs.whole_home_import.kwh;
  const gridToHp = imp > 0 ? Math.min(hpElec, imp) : hpElec;
  const onsite = imp > 0 ? Math.max(hpElec - imp, 0) : 0;
  const freeAir = envSh + envDhw;

  if (hpElec < 0.05) return <p className="muted">No heat pump energy recorded for this period yet.</p>;
  const limit = 200 * Math.max(days, 1);
  const checks: [string, number][] = [["Electricity to the heat pump", hpElec], ["Free heat from outside air", freeAir], ["Space heating", shHeat], ["Hot water", dhwHeat], ["Standby and backup", other]];
  const bad = checks.find(([, v]) => v > limit || v < -0.05);
  if (bad) {
    return <p className="note urgent">These figures look wrong: {bad[0]} is {kwh(bad[1])} kWh for {days === 1 ? "one day" : `${days} days`}. Check the Signal mapping table on System health.</p>;
  }

  const inputs = gridToHp + onsite + freeAir;
  const outputs = shHeat + dhwHeat + other;
  const residual = inputs - outputs;
  const ink = css("--ink"), ink2 = css("--ink-2");
  const C = { grid: css("--flow-grid"), onsite: css("--flow-onsite"), air: css("--flow-air"), hp: css("--flow-hp"), gap: css("--axis") };
  type N = { name: string; colour: string; side: "in" | "out" };
  const nodes: N[] = [];
  const links: { source: number; target: number; value: number; colour: string }[] = [];
  const add = (n: N) => { nodes.push(n); return nodes.length - 1; };
  const hub = add({ name: "Heat pump", colour: C.hp, side: "in" });
  const inp = (name: string, value: number, colour: string) => { if (value > 0.05) links.push({ source: add({ name, colour, side: "in" }), target: hub, value, colour }); };
  const out = (name: string, value: number, colour: string) => { if (value > 0.05) links.push({ source: hub, target: add({ name, colour, side: "out" }), value, colour }); };
  inp("Grid electricity", gridToHp, C.grid);
  inp("Solar / battery electricity", onsite, C.onsite);
  inp("Free heat from outside air", Math.max(freeAir, 0), C.air);
  out("Space heating", shHeat, C.hp);
  out("Hot water", dhwHeat, C.hp);
  out("Standby and backup heater", other, C.hp);
  if (residual > 0.05) out("Unaccounted", residual, C.gap);
  else if (residual < -0.05) inp("Unaccounted", -residual, C.gap);
  const data = { nodes: nodes.map((n) => ({ name: n.name, colour: n.colour })), links };
  const flowOf = (i: number) => links.filter((l) => l.target === i).reduce((a, l) => a + l.value, 0) || links.filter((l) => l.source === i).reduce((a, l) => a + l.value, 0);
  const sources = [["From the grid", C.grid, gridToHp], ["From solar / battery", C.onsite, onsite], ["From outside air", C.air, freeAir], ["Heat pump output", C.hp, outputs]].filter(([, , v]) => (v as number) > 0.05) as [string, string, number][];

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Node = (props: any) => {
    const { x, y, width, height, index, payload } = props as { x: number; y: number; width: number; height: number; index: number; payload: { name: string; colour: string } };
    const n = nodes[index];
    const nodeTip = payload.name === "Unaccounted" ? GLOSSARY.unaccounted : payload.name === "Free heat from outside air" ? "Heat delivered minus electricity used: the heat the pump moved in from the outside air for free." : undefined;
    const right = n.side === "out" && index !== hub;
    const isHub = index === hub;
    return (
      <g>
        {nodeTip ? <title>{nodeTip}</title> : null}
        <rect x={x} y={y} width={width} height={Math.max(height, 1)} fill={payload.colour} rx={2} />
        <text x={isHub ? x + width / 2 : right ? x - 6 : x + width + 6} y={isHub ? y - 6 : y + height / 2} textAnchor={isHub ? "middle" : right ? "end" : "start"} dominantBaseline={isHub ? "auto" : "middle"} fontSize={12} fill={ink}>
          {payload.name}
          <tspan fill={ink2}> {kwh(flowOf(index))} kWh</tspan>
        </text>
      </g>
    );
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Link = (props: any) => {
    const { sourceX, targetX, sourceY, targetY, sourceControlX, targetControlX, linkWidth, payload } = props as { sourceX: number; targetX: number; sourceY: number; targetY: number; sourceControlX: number; targetControlX: number; linkWidth: number; payload: { colour: string } };
    return <path d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`} fill="none" stroke={payload.colour} strokeOpacity={0.45} strokeWidth={Math.max(linkWidth, 1)} />;
  };
  const ratio = hpElec > 0 ? outputs / hpElec : 0;
  const freePerKwh = hpElec > 0 ? Math.max(freeAir, 0) / hpElec : 0;
  return (
    <div className="chart">
      <h4>{title}</h4>
      <div className="legend">{sources.map(([label, colour]) => <span key={label} style={{ "--c": colour } as React.CSSProperties}>{label}</span>)}</div>
      <ResponsiveContainer width="100%" height={280}>
        <Sankey data={data} nodePadding={26} nodeWidth={12} margin={{ top: 24, right: 170, bottom: 8, left: 170 }} node={Node} link={Link} linkCurvature={0.55} iterations={32}>
          <Tooltip formatter={(v) => [`${kwh(Number(v))} kWh`, ""]} contentStyle={{ borderRadius: 8, fontSize: 13 }} />
        </Sankey>
      </ResponsiveContainer>
      <p style={{ fontSize: 13, margin: "6px 8px 2px" }}>
        For every 1 kWh of electricity, the heat pump delivered {kwh(ratio)} kWh of heat; {kwh(freePerKwh)} kWh of that came free from the outside air.
        {imp > 0 && costs.whole_home_import.plausible !== false ? ` The heat pump used ${kwh(hpElec)} kWh of the home's ${kwh(imp)} kWh of electricity${days === 1 ? " today" : ` over ${days} days`}.` : imp > 0 ? " Whole-home import is not shown because its data looks wrong; see System health." : ""}
      </p>
      <p className="muted" style={{ fontSize: 12, margin: "2px 8px 4px" }}>
        Free heat from outside air is heat delivered minus electricity used. Heat pump energy figures are Vaillant's own estimates, accurate to a few percent but not a billing meter.
      </p>
    </div>
  );
}
