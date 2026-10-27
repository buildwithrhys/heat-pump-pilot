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

async function send<T>(method: "PUT" | "POST", path: string, body?: unknown): Promise<T> {
  const r = await fetch(url(path), { method, headers: body === undefined ? undefined : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

export interface Reading {
  value: number | null;
  text: string | null;
  ts: string;
  confirmed_at?: string;
  age_minutes?: number;
  stale_after_minutes?: number;
  quality: string;
  stale: boolean;
}

export interface Balance {
  status: "ok" | "partial" | "inconsistent";
  days_ok: number;
  days_flagged: { date: string; status: string; elec_kwh: number; heat_kwh: number; env_kwh: number | null; mismatch_pct?: number }[];
  kwh_heat_ok: number;
  kwh_electric_ok: number;
}

export interface CostBlock {
  kwh_electric: number;
  kwh_heat?: number;
  kwh_env?: number | null;
  balance?: Balance;
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
  coverage?: { days: number; days_with_data: number; first_day_with_data: string | null };
  tariff_code: string | null;
  gas_equivalent: GasEquivalent;
  space_heating: CostBlock;
  hot_water: CostBlock;
  auxiliary: CostBlock;
  standby: CostBlock;
  heat_pump_total: { kwh_electric: number; cost_pence: number };
  whole_home_import: { kwh: number; cost_pence: number; quality: string; prices_complete: boolean; source?: string; plausible?: boolean; limit_kwh?: number };
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
  saving_basis?: "vs_dear_period" | "vs_average" | null;
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
  cost_p_baseline?: number;
  elec_kwh?: number;
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
  horizon_hours?: number;
  cost?: { optimised_p: number; baseline_p: number; saving_p: number } | null;
  model?: Record<string, string | number | null> | null;
}

export interface SurveyInfo { kw: number; outdoor_c: number; ua_w_per_k: number; kw_at_design: number }

export interface HeatLoss {
  status: "ok" | "waiting";
  reason?: string;
  samples: number;
  days_with_data: number;
  days_needed: number;
  lookback_days: number;
  window_start: string;
  window_end: string;
  design_outdoor_c: number;
  design_indoor_c: number;
  design_region: string;
  survey: SurveyInfo | null;
  outdoor_range_c: number[] | null;
  heating_hours: number;
  rated_kw: number | null;
  heat_loss_w_per_k?: number;
  ci_low_w_per_k?: number;
  ci_high_w_per_k?: number;
  design_kw?: number;
  design_kw_low?: number;
  design_kw_high?: number;
  capacity_kwh_per_k?: number | null;
  time_constant_h?: number | null;
  cooling_rate_c_per_h_at_5c?: number | null;
  internal_gain_kw?: number | null;
  confidence?: number;
  survey_difference_pct?: number;
  survey_disagrees?: boolean;
  rated_margin_kw?: number;
  notes?: string[];
}

export interface EndUseEfficiency {
  label: string;
  status: "ok" | "waiting";
  reason?: string;
  observed_cop: number | null;
  kwh_electric: number;
  kwh_heat: number;
  running_hours: number;
  days_with_data: number;
  outdoor_range_c: number[] | null;
  cop_at_7: number | null;
  cop_at_minus_2: number | null;
  slope_per_degc: number;
  model_confidence: number;
  normalised_note?: string;
  notes: string[];
}

export interface HeatPump {
  status: "ok" | "waiting";
  lookback_days: number;
  heating: EndUseEfficiency;
  hot_water: EndUseEfficiency;
  observed_mean_cop: number | null;
  cop_at_7: number | null;
  cop_at_minus_2: number | null;
  samples: number;
  days_with_data: number;
  confidence: number;
  t_range: (number | null)[];
  capacity_kw: number;
  capacity_source: string;
  rated_kw: number | null;
  reason?: string | null;
  why_differs: string;
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
  cost_backtest?: { status: string; slots: number; predicted_pence?: number; actual_pence?: number; predicted_kwh?: number; actual_kwh?: number; error_pct?: number | null };
  note?: string;
}

export interface TrialDay { date: string; group: "baseline" | "optimised"; degree_days: number; kwh: number; cost_pence: number; pence_per_degree_day: number; kwh_per_degree_day: number }
export interface TrialReport {
  enabled: boolean;
  days: TrialDay[];
  baseline: { n: number; mean: number | null; se: number | null };
  optimised: { n: number; mean: number | null; se: number | null };
  saving_pence_per_degree_day?: number;
  saving_pct?: number;
  saving_se_pct?: number;
  credible?: boolean;
  note: string;
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
  rates_source?: string;
  current?: boolean;
}

export interface TariffComparison {
  status: "ok" | "no_data" | "implausible";
  reason?: string;
  days: number;
  period?: { start: string; end: string };
  source?: string;
  kwh?: number;
  rates_as_of?: string;
  region?: string | null;
  region_name?: string | null;
  actual?: { code: string; name: string; unit_pence: number; standing_pence: number; total_pence: number; kwh: number; kwh_total: number; coverage_pct: number; days_priced: number; prices_complete: boolean; effective_p_per_kwh: number; difference_vs_best_pence?: number; product?: string | null; on_cheapest?: boolean } | null;
  reconciliation?: { template_unit_pence: number; actual_unit_pence: number; difference_pct: number | null; template_effective_p_per_kwh: number | null; actual_effective_p_per_kwh: number | null } | null;
  kwh_compared?: number;
  results: TariffResult[];
  caveats?: string[];
  scenario?: { ev_kwh_per_day: number; battery_kwh_per_day: number };
  annual?: { status: string; reason?: string; days?: number; kwh_per_day_no_heating?: number; kwh_per_degree_day?: number; annual_degree_days?: number; annual_kwh?: number; climate?: string; climate_source?: string; has_solar?: boolean; upper_estimate?: boolean; note?: string };
  live_rates?: Record<string, string>;
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

export interface ClimateEntity { entity_id: string; name: string; platform: string | null; state: string; current_temperature: number | null; target: number | null }
export interface Precondition { key: string; label: string; met: boolean; detail: string }
export interface Preconditions { checks: Precondition[]; all_met: boolean; max_offset_c: number }

export interface SettingsField {
  type: "enum" | "number" | "bool" | "entity";
  group?: "control" | "comparisons" | "advanced";
  lines?: Record<string, string>;
  default: number | string | null;
  optional?: boolean;
  choices?: string[];
  labels?: Record<string, string>;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  label: string;
  help: string;
}

export interface SettingsDoc {
  values: Record<string, number | string | boolean | null>;
  fields: Record<string, SettingsField>;
  gate: { allow_control: boolean; climate_entity: string | null; control_possible: boolean; message: string | null };
  effective: { operating_mode: string; control_enabled: boolean; climate_entity: string | null };
}

export interface MappingSignal {
  signal: string;
  description: string;
  unit: string;
  source: string | null;
  status: string;
  confidence: number;
  notes: string;
  overridden: boolean;
  candidates: [string, number, string][];
}

export interface MappingView { editable: boolean; signals: MappingSignal[] }

/** Switch the dashboard tab from anywhere (the App listens). */
export const goToTab = (tab: string) => { try { localStorage.setItem("predheat.tab", tab); } catch { /* ignore */ } window.dispatchEvent(new CustomEvent("predheat:tab", { detail: tab })); };
export const announceMode = (mode: string, control: boolean) => window.dispatchEvent(new CustomEvent("predheat:mode", { detail: { mode, control } }));

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
  has_data?: boolean;
  days_without_data?: number;
  period_start?: string;
  period_end?: string;
  label?: string;
  days?: number;
  space_heating: CostBlock;
  hot_water: CostBlock;
  heat_pump_total: { kwh_electric: number; cost_pence: number };
  whole_home_import: { kwh: number; cost_pence: number; plausible?: boolean };
  standing_charge_pence: number;
  gas_equivalent: GasEquivalent;
}

export interface Timeline {
  generated_at: string;
  tariff_code: string | null;
  slots: { start: string; end: string; rate_p: number | null; price_class: string; assumed: boolean; heating_rate_p?: number; source?: "grid" | "battery" | "solar"; shares?: Record<string, number> }[];
  slot_summary: Record<string, number>;
  weather: { valid_at: string; temperature_c: number; cloud_cover_pct: number | null }[];
  solar: { period_start: string; pv_kw: number; pv10_kw: number | null; pv90_kw: number | null }[];
  optimisation: ShadowPlan;
  predbat: PredbatStatus;
}

export interface PredbatStatus {
  installed: boolean; enabled: boolean; in_use: boolean; reason: string;
  prefix?: string; updated_at?: string | null; age_minutes?: number | null; plan_until?: string | null;
  battery_value_p?: number | null; battery_size_kwh?: number | null; entities?: Record<string, string>;
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
  predbat?: PredbatStatus;
  predbat_feed?: PredbatFeed | null;
}

export interface PredbatFeed {
  entity_id: string; attribute: string; apps_yaml: string; enabled: boolean;
  status: "ok" | "not_acting" | "waiting"; reason: string;
  extra_kwh: number; planned_kwh: number; usual_kwh?: number;
}

export interface CurveBand {
  band: string; t_out: number; t_set: number; hours: number; running_hours: number; heat_kwh: number;
  flow_now: number; flow_needed: number; flow_suggested?: number; saving_pct?: number;
  room_offset: number; cycles_per_running_hour: number | null; run_fraction: number; flag: "hot" | "cold" | null;
}
export interface CurveSetting { key: "heating_curve" | "min_flow" | "cutoff"; label: string; now: number | null; suggested: number | null; why: string }
export interface CurveWritable { entity_id: string; label: string; min: number | null; max: number | null; step: number | null; ambiguous: boolean }
export interface CurveChange { at: string; from: string | null; to: string | null; undo: boolean; can_undo: boolean }
export interface CurveBeforeAfter {
  change: { at: string; setting: string; label: string; from: number; to: number };
  status: "ok" | "waiting"; reason?: string;
  before?: { hours: number; cop: number | null; t_out: number | null; room_offset: number | null };
  after?: { hours: number; cop: number | null; t_out: number | null; room_offset: number | null };
  expected_cop_after?: number; efficiency_change_pct?: number; comfort_change_c?: number;
}
export interface HeatingCurve {
  status: "ok" | "waiting"; reason?: string; running_hours?: number; lookback_days?: number;
  direction?: "lower" | "raise" | "keep"; current_curve?: number | null; suggested_curve?: number | null; saving_pct?: number; confidence?: number;
  notes?: string[]; bands?: CurveBand[]; settings?: CurveSetting[];
  chart?: { t: number; now?: number; suggested?: number; needed?: number }[];
  emitter?: { k_kw_per_k13: number; spread: number; hours: number; return_reported: boolean; assumed_flow_return_difference_c: number | null; margin_c: number; output_at_50c_flow_kw: number };
  coverage?: { t_min: number; t_max: number; running_hours: number; cold_end_checked: boolean };
  writable: Record<string, CurveWritable>;
  last_change: Record<string, CurveChange | null>;
  before_after: CurveBeforeAfter | null;
  since_change?: { at: string; label: string; to: number };
}

export const api = {
  overview: () => get<Overview>("/api/overview"),
  costs: (q: { days?: number; start?: string; end?: string; group?: "day" | "week" | "month" }) => {
    const qs = Object.entries(q).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
    return get<{ summary: Costs; daily: DailyRow[]; group: string; period: { start: string; end: string } }>(`/api/costs?${qs}`);
  },
  costsCsvUrl: (q: { days?: number; start?: string; end?: string; group?: string }) => {
    const qs = Object.entries(q).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
    return url(`/api/costs.csv?${qs}`);
  },
  mapping: () => get<MappingView>("/api/mapping"),
  diagnosticsUrl: (days = 30) => url(`/api/diagnostics/report?days=${days}&raw_days=3&download=true`),
  setMapping: (signal: string, entityId: string | null) => send<MappingView>("PUT", `/api/mapping/${signal}`, { entity_id: entityId ?? "" }),
  timeline: (hours = 48) => get<Timeline>(`/api/timeline?hours=${hours}`),
  system: () => get<SystemHealth>("/api/system"),
  heatLoss: (days = 14) => get<HeatLoss>(`/api/heat-loss?days=${days}`),
  heatPump: (days = 30) => get<HeatPump>(`/api/heat-pump?days=${days}`),
  accuracy: (days = 7) => get<ForecastAccuracy>(`/api/accuracy?days=${days}`),
  tariffs: (days = 30, ev = 0, battery = 0, live = false) => get<TariffComparison>(`/api/tariffs?days=${days}&ev_kwh_per_day=${ev}&battery_kwh_per_day=${battery}&live=${live}`),
  trial: (days = 28) => get<TrialReport>(`/api/trial?days=${days}`),
  controlStatus: () => get<ControlStatus>("/api/control-status"),
  settings: () => get<SettingsDoc>("/api/settings"),
  saveSettings: (changes: Record<string, number | string | boolean | null>) => send<SettingsDoc>("PUT", "/api/settings", changes),
  preconditions: () => get<Preconditions>("/api/control-preconditions"),
  climateEntities: () => get<{ entities: ClimateEntity[]; suggested: string | null; water_heaters?: ClimateEntity[] }>("/api/climate-entities"),
  heatingCurve: (days = 14) => get<HeatingCurve>(`/api/heating-curve?days=${days}`),
  applyCurve: (setting: string, value: number) => send<HeatingCurve>("POST", "/api/heating-curve/apply", { setting, value }),
  undoCurve: (setting: string) => send<HeatingCurve>("POST", "/api/heating-curve/undo", { setting }),
  resetSetting: (key?: string) => send<SettingsDoc>("POST", `/api/settings/reset${key ? `?key=${key}` : ""}`),
};

const gb = (v: number, dp: number) => v.toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });
export const pounds = (pence: number | null | undefined) =>
  pence == null ? "–" : `£${gb(pence / 100, 2)}`;
export const num = (v: number | null | undefined, dp = 1, unit = "") =>
  v == null ? "–" : `${gb(v, dp)}${unit}`;
