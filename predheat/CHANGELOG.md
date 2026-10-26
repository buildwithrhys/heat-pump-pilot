# Changelog

## 0.1.1

- Heat loss figure on the Overview, fitted from your own data, with range, cold-day kW, time constant and confidence. Saved as a building model version at most every six hours. New `/api/heat-loss` endpoint.
- "What PredHeat would do": a 24-hour rule-based plan (hot water, pre-heat, coast) with a reason per line, shown on the Overview and Forecast tabs in both modes. Shadow mode records every plan.
- Energy flow diagram on the Overview (today) and Energy costs (chosen period).
- Energy costs table and chart use UK date formatting.
- Add-on icon and logo, option names and descriptions on the Configuration page.
- Documentation rewritten for users.

## 0.1.0

- First add-on release. Phase 0 discovery and Phase 1 data platform.
- Reads Home Assistant through the Supervisor proxy, no long-lived token needed.
- Dashboard served through ingress from the add-on sidebar panel.
- Read-only: active control is disabled and cannot be enabled from the add-on.
