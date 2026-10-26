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

export interface GasEquivalent {
  kwh_gas: number;
  cost_pence: number;
  price_p_per_kwh: number;
  boiler_efficiency: number;
  saving_pence: number;
  note: string;
}

export interface Costs {
  period: { start: string; end: string };
  tariff_code: string | null;
  gas_equivalent: GasEquivalent;
  space_heating: CostBlock;
  hot_water: CostBlock;
  auxiliary: CostBlock;
  standby: CostBlock;
  heat_pump_total: { kwh_electric: number; cost_pence: number };
  whole_home_import: { kwh: number; cost_pence: number; quality: string; prices_complete: boolean };
  standing_charge_pence: number;
  reconciliation: { reconciled: boolean; worst_difference_kwh: number; slots: number };
}

export interface PlanAction {
  kind: string;
  label: string;
  start: string;
  end: string;
  rate_p: number | null;
  reason: string;
  saving_p_per_kwh: number | null;
}

export interface NextAction {
  kind: string;
  label?: string;
  start?: string;
  end?: string;
  rate_p?: number | null;
  reason: string;
  state?: "now" | "upcoming";
}

export interface PredictedSlot {
  start: string;
  t_in: number;
  t_in_baseline: number;
  heat_kw: number;
  heat_kw_baseline: number;
  rate_p: number;
  t_out: number;
  cost_p: number;
  mode: string;
}

export interface ShadowPlan {
  status: "indicative" | "model_based" | "not_available";
  reason: string;
  actions: PlanAction[];
  next_action?: NextAction;
  assumptions?: string[];
  horizon_start?: string;
  horizon_end?: string;
  predicted?: PredictedSlot[];
  cost?: { optimised_p: number; baseline_p: number; saving_p: number } | null;
  model?: Record<string, string | number | null> | null;
}

export interface HeatPump {
  status: "ok" | "early" | "prior";
  cop_at_7: number;
  cop_at_2: number;
  cop_at_minus_2: number;
  slope_per_degc: number;
  observed_mean_cop: number | null;
  samples: number;
  confidence: number;
  rmse: number | null;
  t_range: (number | null)[];
  capacity_kw: number;
  capacity_source: string;
  notes: string[];
}

export interface ForecastAccuracy {
  status: "ok" | "waiting" | "no_data";
  reason?: string;
  runs: number;
  points: number;
  rmse_baseline_c?: number;
  mae_baseline_c?: number;
  bias_baseline_c?: number;
  rmse_optimised_c?: number;
  target_rmse_c?: number;
  meets_target?: boolean;
  by_run?: { issued_at: string; points: number; rmse_baseline_c: number; bias_baseline_c: number }[];
  note?: string;
}

export interface HeatLoss {
  status: "ok" | "insufficient_data" | "not_identified";
  reason?: string;
  samples: number;
  window_start: string;
  window_end: string;
  heat_loss_w_per_k?: number;
  ci_low_w_per_k?: number | null;
  ci_high_w_per_k?: number | null;
  design_kw_at_minus_2?: number;
  capacity_kwh_per_k?: number | null;
  time_constant_h?: number | null;
  internal_gain_kw?: number | null;
  confidence?: number;
  survey_w_per_k?: number | null;
  notes?: string[];
}

export interface TariffResult {
  code: string;
  name: string;
  description?: string;
  replayable: boolean;
  unit_pence?: number;
  standing_pence?: number;
  total_pence?: number;
  kwh?: number;
  days?: number;
  cheap_share?: number | null;
  effective_p_per_kwh?: number | null;
  annualised_pounds?: number;
  requires?: string[];
  note?: string;
}

export interface TariffComparison {
  status: "ok" | "no_data";
  reason?: string;
  days: number;
  period?: { start: string; end: string };
  source?: string;
  kwh?: number;
  rates_as_of?: string;
  actual?: { code: string; name: string; unit_pence: number; standing_pence: number; total_pence: number; kwh: number; prices_complete: boolean; effective_p_per_kwh: number; difference_vs_best_pence?: number } | null;
  results: TariffResult[];
  caveats?: string[];
}

export interface ControlStatus {
  configured: boolean;
  reason: string;
  entity?: string | null;
  holding: boolean;
  occupant_setpoint_c?: number | null;
  commanded_c?: number | null;
  override_until?: string | null;
  last?: { status: string; reason: string; target_c: number | null; mode: string | null } | null;
  max_offset_c?: number;
  mode?: string;
}

export const MODE_LABEL: Record<string, string> = { monitor: "Monitor mode", shadow: "Shadow mode", balanced: "Balanced", max_savings: "Max savings" };

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
  plan: ShadowPlan;
  next_action: NextAction;
  heat_loss: HeatLoss;
  heat_pump: HeatPump;
}

export interface DailyRow {
  date: string;
  space_heating: CostBlock;
  hot_water: CostBlock;
  heat_pump_total: { kwh_electric: number; cost_pence: number };
  whole_home_import: { kwh: number; cost_pence: number };
  standing_charge_pence: number;
  gas_equivalent: GasEquivalent;
}

export interface Timeline {
  generated_at: string;
  tariff_code: string | null;
  slots: { start: string; end: string; rate_p: number | null; price_class: string; assumed: boolean }[];
  slot_summary: Record<string, number>;
  weather: { valid_at: string; temperature_c: number; cloud_cover_pct: number | null }[];
  solar: { period_start: string; pv_kw: number; pv10_kw: number | null; pv90_kw: number | null }[];
  optimisation: ShadowPlan;
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
  forecast_accuracy: ForecastAccuracy;
  control: ControlStatus;
}

export const api = {
  overview: () => get<Overview>("/api/overview"),
  costs: (days: number) => get<{ summary: Costs; daily: DailyRow[] }>(`/api/costs?days=${days}`),
  timeline: (hours = 48) => get<Timeline>(`/api/timeline?hours=${hours}`),
  system: () => get<SystemHealth>("/api/system"),
  heatLoss: (days = 14) => get<HeatLoss>(`/api/heat-loss?days=${days}`),
  heatPump: (days = 30) => get<HeatPump>(`/api/heat-pump?days=${days}`),
  accuracy: (days = 7) => get<ForecastAccuracy>(`/api/accuracy?days=${days}`),
  tariffs: (days = 30) => get<TariffComparison>(`/api/tariffs?days=${days}`),
  controlStatus: () => get<ControlStatus>("/api/control-status"),
};

export const pounds = (pence: number | null | undefined) =>
  pence == null ? "–" : `£${(pence / 100).toFixed(2)}`;
export const num = (v: number | null | undefined, dp = 1, unit = "") =>
  v == null ? "–" : `${v.toFixed(dp)}${unit}`;
