import { ResponsiveContainer, Sankey, Tooltip } from "recharts";
import type { Costs } from "../api";

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

/**
 * Energy flow for a period: where the electricity came from, what the heat pump
 * turned it into, and how much free heat it pulled from the outside air.
 * Everything is derived from the cost summary, so it carries the same quality tags.
 */
export function EnergyFlow({ costs, title }: { costs: Costs; title: string }) {
  const hpElec = costs.heat_pump_total.kwh_electric;
  const shElec = costs.space_heating.kwh_electric;
  const dhwElec = costs.hot_water.kwh_electric;
  const other = Math.max(hpElec - shElec - dhwElec, 0); // standby + backup heater
  const shHeat = costs.space_heating.kwh_heat ?? shElec;
  const dhwHeat = costs.hot_water.kwh_heat ?? dhwElec;
  const envSh = Math.max(shHeat - shElec, 0);
  const envDhw = Math.max(dhwHeat - dhwElec, 0);
  const imp = costs.whole_home_import.kwh;
  const gridToHp = Math.min(hpElec, imp);
  const onsite = Math.max(hpElec - imp, 0); // heat pump electricity not explained by import: solar or battery
  const restActual = Math.max(imp - hpElec, 0);
  const restShown = restActual <= 3 * hpElec; // otherwise it flattens every other flow; shown as text instead
  const rest = restShown ? restActual : 0;
  if (hpElec < 0.05) return <p className="muted">No heat pump energy recorded for this period yet.</p>;

  const ink = css("--ink"), ink2 = css("--ink-2");
  // Colour follows the source of the energy: grid, on-site generation, outside air, or the heat pump's output.
  const C = { grid: css("--flow-grid"), onsite: css("--flow-onsite"), air: css("--flow-air"), hp: css("--flow-hp") };
  const nodes = [
    { name: "Grid import", colour: C.grid },        // 0
    { name: "Solar / battery", colour: C.onsite },  // 1
    { name: "Outside air", colour: C.air },         // 2
    { name: "Heat pump", colour: C.hp },            // 3
    { name: "Space heating", colour: C.hp },        // 4
    { name: "Hot water", colour: C.hp },            // 5
    { name: "Rest of home", colour: C.grid },       // 6
    { name: "Standby and backup", colour: C.hp },   // 7
  ];
  const raw = [
    { source: 0, target: 3, value: gridToHp, colour: C.grid },
    { source: 1, target: 3, value: onsite, colour: C.onsite },
    { source: 2, target: 3, value: envSh + envDhw, colour: C.air },
    { source: 3, target: 4, value: shHeat, colour: C.hp },
    { source: 3, target: 5, value: dhwHeat, colour: C.hp },
    { source: 3, target: 7, value: other, colour: C.hp },
    { source: 0, target: 6, value: rest, colour: C.grid },
  ].filter((l) => l.value > 0.05);
  // drop nodes with no links and re-index
  const used = Array.from(new Set(raw.flatMap((l) => [l.source, l.target]))).sort((a, b) => a - b);
  const index = new Map(used.map((n, i) => [n, i]));
  const data = {
    nodes: used.map((n) => nodes[n]),
    links: raw.map((l) => ({ ...l, source: index.get(l.source)!, target: index.get(l.target)! })),
  };
  const total = (i: number) => data.links.filter((l) => l.source === i || l.target === i).reduce((a, l) => a + (l.target === i ? l.value : 0), 0) || data.links.filter((l) => l.source === i).reduce((a, l) => a + l.value, 0);

  // recharts' NodeProps/LinkProps carry our extra "colour" field in payload but are not generic, hence the loose typing.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Node = (props: any) => {
    const { x, y, width, height, index, payload } = props as { x: number; y: number; width: number; height: number; index: number; payload: { name: string; colour: string } };
    const right = x > 200;
    return (
      <g>
        <rect x={x} y={y} width={width} height={height} fill={payload.colour} rx={2} />
        <text x={right ? x - 6 : x + width + 6} y={y + height / 2} textAnchor={right ? "end" : "start"} dominantBaseline="middle" fontSize={12} fill={ink}>
          {payload.name}
          <tspan fill={ink2}> {total(index).toFixed(1)} kWh</tspan>
        </text>
      </g>
    );
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Link = (props: any) => {
    const { sourceX, targetX, sourceY, targetY, sourceControlX, targetControlX, linkWidth, payload } = props as { sourceX: number; targetX: number; sourceY: number; targetY: number; sourceControlX: number; targetControlX: number; linkWidth: number; payload: { colour: string } };
    return <path d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`} fill="none" stroke={payload.colour} strokeOpacity={0.35} strokeWidth={linkWidth} />;
  };
  return (
    <div className="chart">
      <h4>{title}</h4>
      <div className="legend">
        <span style={{ "--c": C.grid } as React.CSSProperties}>From the grid</span>
        <span style={{ "--c": C.onsite } as React.CSSProperties}>From solar / battery</span>
        <span style={{ "--c": C.air } as React.CSSProperties}>From outside air</span>
        <span style={{ "--c": C.hp } as React.CSSProperties}>Heat pump output</span>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <Sankey data={data} nodePadding={28} nodeWidth={10} margin={{ top: 8, right: 130, bottom: 8, left: 110 }} node={Node} link={Link} linkCurvature={0.5}>
          <Tooltip formatter={(v) => [`${Number(v).toFixed(1)} kWh`, ""]} contentStyle={{ borderRadius: 8, fontSize: 13 }} />
        </Sankey>
      </ResponsiveContainer>
      <p className="muted" style={{ fontSize: "0.8rem", margin: "4px 0 0" }}>
        Outside air is the heat the pump harvested for free (heat delivered minus electricity used). Solar / battery is heat pump electricity not matched by grid import.{!restShown && restActual > 0 ? ` The rest of the home used ${restActual.toLocaleString("en-GB", { maximumFractionDigits: 1 })} kWh from the grid, not drawn so the heat pump flows stay readable.` : ""}
      </p>
    </div>
  );
}
