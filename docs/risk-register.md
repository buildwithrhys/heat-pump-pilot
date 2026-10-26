# Risk register and assumptions

Integration risks identified in Phase 0 from live discovery and external research (25 October 2026). Likelihood and impact are judgements, not measurements.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| myVAILLANT quota throttling (403, hours without data) | High | Medium | Read only through Home Assistant at 30 min state and 2 h energy polls; honour back-off; design for gaps; degrade gracefully. |
| Vaillant breaks or blocks the unofficial API (login changes, proof-of-work challenge in July 2026) | Medium to high | High | Track mypyllant releases; fail safe to manufacturer control; apply for the official Energy Management API trial now. |
| Energy and COP data lag 0.5 to 2 h and midnight gaps | Certain | Medium | Use for next-day learning and reporting, not live control; live power and flow temperature give the real-time view; completeness indicator on reports. |
| No compressor, defrost or return temperature signals | Certain | Medium | Infer operating state from power and flow, labelled model derived; a CT clamp and later a heat meter close the gap. |
| Manufacturer energy figures are estimates, not metered | Certain | Medium | Label as such everywhere; validate against the myVAILLANT app and a CT clamp; never present as metered. |
| Single indoor sensor, located in one room | Certain | Medium | Single-zone model with stated limits; option to re-power Wiser TRVs for nine room temperatures. |
| Setpoint conflicts with EnergyPLUS or EEBUS | Medium | High | Monitor mode until ownership is proven; log observed setpoint changes with energy manager state; disable EnergyPLUS during trials. |
| Planned dispatches cancelled or added late | High | Low to medium | Classify planned slots as assumed; count only started and completed dispatches; re-evaluate every few minutes in later phases. |
| Octopus 100 calls per hour shared limit | Medium | Medium | Read via the HA integration's coordinator; a direct client only for historic product rates at low frequency. |
| Octopus consumption published late or revised | High | Low | Idempotent re-ingest, retrospective correction events, completeness flags. |
| Solcast hobbyist terms and 10 to 50 calls per day; no irradiance | Certain for commercial use | Medium | Open-Meteo for irradiance (licensable); Solcast commercial licence or alternative for the product. |
| OpenWeather One Call costs above 1,000 calls per day | Low | Low | Hourly polling; daily cap; HA integration already handles it. |
| Clock changes mis-handled | Low | High | UTC storage, local day boundaries, tests for 46 and 50 slot days. |
| Commercial licensing gaps (Vaillant partner terms, Octopus partner OAuth, Solcast, Open-Meteo) | High | High | Monitoring-first PoC; open conversations with Vaillant and Octopus before any customer rollout. |
| Home Assistant unavailable or token revoked | Low | Medium | Provider health on System Health page; collector isolates failures; no equipment impact because read-only. |

## Assumptions

Home Assistant long-term statistics are retained indefinitely for the mapped entities (the recorder is in its default location; purge settings were not inspected). Rates from the Octopus integration are inclusive of VAT, as its documentation states. The Hypervolt remains the only Intelligent Octopus device; the Ohme is unused. The household has no gas supply, so the gas comparison will use a representative tariff rather than a historic bill. The heat loss survey figure and design temperatures are not yet entered and will be added through configuration when available.
