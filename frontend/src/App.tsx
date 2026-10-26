import { useEffect, useState } from "react";
import { api, type Overview } from "./api";
import OverviewPage from "./pages/OverviewPage";
import CostsPage from "./pages/CostsPage";
import ForecastPage from "./pages/ForecastPage";
import SystemPage from "./pages/SystemPage";

const TABS = ["Overview", "Energy costs", "Forecast", "System health"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    try { return (localStorage.getItem("predheat.tab") as Tab) || "Overview"; } catch { return "Overview"; }
  });
  const [meta, setMeta] = useState<Pick<Overview, "data_mode" | "operating_mode" | "control_enabled"> | null>(null);
  useEffect(() => { api.overview().then((o) => setMeta(o)).catch(() => setMeta(null)); }, []);
  const pick = (t: Tab) => { setTab(t); try { localStorage.setItem("predheat.tab", t); } catch { /* ignore */ } };

  return (
    <div className="shell">
      <header className="top">
        <h1>PredHeat</h1>
        {meta && (
          <span className="mode">
            {meta.data_mode === "simulated" ? "Simulated data" : "Live data"} · mode: {meta.operating_mode} · control {meta.control_enabled ? "ENABLED" : "off"}
          </span>
        )}
      </header>
      <nav className="tabs" aria-label="Sections">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => pick(t)}>{t}</button>
        ))}
      </nav>
      {tab === "Overview" && <OverviewPage />}
      {tab === "Energy costs" && <CostsPage />}
      {tab === "Forecast" && <ForecastPage />}
      {tab === "System health" && <SystemPage />}
    </div>
  );
}
