import { useEffect, useState } from "react";
import { api, MODE_LABEL, type Overview } from "./api";
import OverviewPage from "./pages/OverviewPage";
import CostsPage from "./pages/CostsPage";
import ForecastPage from "./pages/ForecastPage";
import SystemPage from "./pages/SystemPage";
import TariffsPage from "./pages/TariffsPage";
import SettingsPage from "./pages/SettingsPage";

const TABS = ["Overview", "Energy costs", "Forecast", "Tariffs", "System health", "Settings"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    try { return (localStorage.getItem("predheat.tab") as Tab) || "Overview"; } catch { return "Overview"; }
  });
  const [meta, setMeta] = useState<Pick<Overview, "data_mode" | "operating_mode" | "control_enabled"> | null>(null);
  useEffect(() => {
    api.overview().then((o) => setMeta(o)).catch(() => setMeta(null));
    const onTab = (e: Event) => setTab((e as CustomEvent<string>).detail as Tab);
    const onMode = (e: Event) => { const d = (e as CustomEvent<{ mode: string; control: boolean }>).detail; setMeta((m) => (m ? { ...m, operating_mode: d.mode, control_enabled: d.control } : m)); };
    window.addEventListener("predheat:tab", onTab);
    window.addEventListener("predheat:mode", onMode);
    return () => { window.removeEventListener("predheat:tab", onTab); window.removeEventListener("predheat:mode", onMode); };
  }, []);
  const pick = (t: Tab) => { setTab(t); try { localStorage.setItem("predheat.tab", t); } catch { /* ignore */ } };

  return (
    <div className="shell">
      <header className="top">
        <h1>PredHeat</h1>
        {meta && (
          <span className="mode">
            {meta.data_mode === "simulated" ? "Simulated data" : "Live data"} · {MODE_LABEL[meta.operating_mode] ?? meta.operating_mode} · {meta.control_enabled ? "controlling the heating" : "not controlling the heating"}
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
      {tab === "Tariffs" && <TariffsPage />}
      {tab === "System health" && <SystemPage />}
      {tab === "Settings" && <SettingsPage />}
    </div>
  );
}
