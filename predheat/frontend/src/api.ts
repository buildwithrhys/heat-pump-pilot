// API calls are resolved relative to the page, not the site root, so the dashboard
// works both at "/" and below the opaque prefix Home Assistant ingress mounts it on.
// VITE_API_BASE overrides this for a split dev setup.
const BASE = import.meta.env.VITE_API_BASE ?? "";

function url(path: string): string {
  if (BASE) return `${BASE.replace(/\/$/, "")}${path}`;
  return new URL(path.replace(/^\//, ""), document.baseURI).toString();
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(url(path));
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export interface Reading {
  value: number | null;
  text: string | null;
  ts: string;
  quality: string;
  stale: boolean;
}

export interface CostBlock {
  kwh_electric: number;
  kwh_heat?: number;
  cop?: number | null;
  cost_pence: number;
  pence_per_kwh_heat?: number | null;
  quality: string;
  prices_complete?: boolean;
}

export interface Costs {
  period: { start: string; end: string };
  tariff_code: string | null;
  space_heating: CostBlock;
  hot_water: CostBlock;
  auxiliary: CostBlock;
  standby: CostBlock;
  heat_pump_total: { kwh_electric: number; cost_pence: number };
  whole_home_import: { kwh: number; cost_pence: number; quality: string; prices_complete: boolean };
  standing_charge_pence: number;
  reconciliation: { reconciled: boolean; worst_difference_kwh: number; slots: number };
}

export interface Overview {
  generated_at: string;
  data_mode: string;
  operating_mode: string;
  control_enabled: boolean;
  indoor_temperature: Reading | null;
  indoor_setpoint: Reading | null;
  outdoor_temperature: Reading | null;
  flow_temperature: Reading | null;
  dhw_tank_temperature: Reading | null;
  hp_electrical_power: Reading | null;
  hp_operating_state: Reading | null;
  tariff_code: string | null;
  import_rate_now_p: number | null;
  today: Costs;
  next_action: { kind: string; reason: string };
}

export interface DailyRow {
  date: string;
  space_heating: CostBlock;
  hot_water: CostBlock;
  heat_pump_total: { kwh_electric: number; cost_pence: number };
  whole_home_import: { kwh: number; cost_pence: number };
  standing_charge_pence: number;
}

export interface Timeline {
  generated_at: string;
  tariff_code: string | null;
  slots: { start: string; end: string; rate_p: number | null; price_class: string; assumed: boolean }[];
  slot_summary: Record<string, number>;
  weather: { valid_at: string; temperature_c: number; cloud_cover_pct: number | null }[];
  solar: { period_start: string; pv_kw: number; pv10_kw: number | null; pv90_kw: number | null }[];
  optimisation: { status: string; reason: string };
}

export interface SystemHealth {
  generated_at: string;
  data_mode: string;
  operating_mode: string;
  control_enabled: boolean;
  providers: { name: string; ok: boolean; last_success_at: string | null; last_error: string | null }[];
  integrations: { platform: string; present: boolean; entities: number; live_entities: number; config_state: string | null }[];
  warnings: string[];
  signals: { signal: string; status: string; quality: string; source: string | null; confidence: number; last_value: number | null; last_text: string | null; age_minutes: number | null; stale: boolean; notes: string }[];
  missing_for_thermal_model: string[];
  recent_events: { ts: string; severity: string; source: string; message: string }[];
  record_counts: Record<string, number>;
}

export const api = {
  overview: () => get<Overview>("/api/overview"),
  costs: (days: number) => get<{ summary: Costs; daily: DailyRow[] }>(`/api/costs?days=${days}`),
  timeline: (hours = 48) => get<Timeline>(`/api/timeline?hours=${hours}`),
  system: () => get<SystemHealth>("/api/system"),
};

export const pounds = (pence: number | null | undefined) =>
  pence == null ? "–" : `£${(pence / 100).toFixed(2)}`;
export const num = (v: number | null | undefined, dp = 1, unit = "") =>
  v == null ? "–" : `${v.toFixed(dp)}${unit}`;
