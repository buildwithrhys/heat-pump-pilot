# Validation

How PredHeat measures its own accuracy, and the bar it must clear before any saving is claimed.

## Measurement validation (Phases 1 and 2)

Daily heat pump electricity from the Vaillant counters is compared with the myVAILLANT app and, when a CT clamp is fitted, with metered kWh; target agreement within 2 percent. The identity heat generated = electricity consumed + environmental energy is checked per day as a consistency test on Vaillant's estimates. Whole-home import from the Huawei meter, the Home Mini and Octopus billed half-hours are reconciled daily; disagreement beyond 3 percent raises a data quality event. Category sums reconcile to totals within 0.01 kWh per slot (enforced in code and tests).

## Pricing validation

Rates stored by PredHeat are compared with Octopus's published half-hourly costs for the previous day (`previous_accumulative_cost` and its `charges`). Clock-change days (46 and 50 slots) and dispatch-adjusted slots are covered by unit tests using the pilot's own 25 October 2026 rate list.

## Model validation (Phase 3)

The 1R1C fitter is validated two ways. Against the simulator, where ground truth is known: the current implementation recovers the heat-loss coefficient within 4 percent and the thermal capacity within 1 percent from ten days of half-hourly data (test `test_rc_fit_recovers_ground_truth_from_half_hourly_data`). The reported confidence interval is statistical only (residual noise); it does not include discretisation or model-structure bias, so the interval can be narrower than the true error. Phase 3 will widen it with a bias term estimated from held-out days.

Against the real property: rolling-origin evaluation. Fit on days 1 to N, forecast indoor temperature and heating energy for day N+1 using the weather forecast that was issued at the time (not the observed weather), and record RMSE and bias. Metrics tracked: indoor temperature forecast error (degC), thermal demand error (kWh/day), electricity error (kWh/day), COP prediction error, cost prediction error (pence/day). Baselines: persistence (tomorrow equals today) and a degree-day regression. The model must beat both before it is used in optimisation.

Identifiability is reported, not assumed: the fitter flags low indoor and outdoor temperature variation and low heating variation. Some properties will need longer than others; no fixed learning period is promised.

## Shadow validation (Phase 4)

The optimiser's plan is stored with the forecasts and model versions it used. The next day its predicted electricity, cost and indoor temperature are compared with what actually happened under the manufacturer's controls. A plan that predicts savings is only evidence that the model thinks so.

## Proving savings (Phase 5)

Baseline period first, with manufacturer controls. Then alternating days or blocks of baseline and optimised operation under comparable conditions, randomised where practical. Outcomes normalised for heating degree hours, hot water draw and comfort delivered. Savings reported as a difference with a confidence interval from the paired comparison; a result whose interval includes zero is reported as no demonstrated saving. Comfort is measured as time outside the comfort band and must not worsen.

## Test suite

`pytest` covers: slot generation on clock-change days; rate lookup and pro-rata costing; gap flagging; IOG classification of guaranteed, confirmed and planned slots; standing charge pro rata; discovery mapping, quality classification and exclusion of distractors on the pilot's anonymised entity dump; unit scaling; counter conversion with resets and duplicates; category reconciliation; simulator plausibility; RC fitting against ground truth and identifiability warnings; end-to-end bootstrap, reporting and API in simulated mode, including the 403 on control routes.
