import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchJSON } from "./api.js";
import Landing from "./components/Landing.jsx";
import OverviewTab from "./components/OverviewTab.jsx";
import WatchlistTab from "./components/WatchlistTab.jsx";
import FundsTab from "./components/FundsTab.jsx";
import IndexesTab from "./components/IndexesTab.jsx";
import LSTMTab from "./components/LSTMTab.jsx";
import ScannerTab from "./components/ScannerTab.jsx";
import Drawer from "./components/Drawer.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import SearchBox from "./components/SearchBox.jsx";

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

const TABS = [
  { key: "overview", fn: "F1", label: "OVERVIEW" },
  { key: "watchlist", fn: "F2", label: "WATCHLIST" },
  { key: "funds", fn: "F3", label: "HEDGE FUNDS" },
  { key: "indexes", fn: "F4", label: "INDEXES" },
  { key: "lstm", fn: "F5", label: "LSTM" },
  { key: "scanner", fn: "F6", label: "SCANNER" },
];

const TAB_COMPONENTS = {
  overview: OverviewTab,
  watchlist: WatchlistTab,
  funds: FundsTab,
  indexes: IndexesTab,
  lstm: LSTMTab,
  scanner: ScannerTab,
};

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function TickerTape({ indexes }) {
  const items = (indexes || []).slice(0, 40);
  if (!items.length) return <div className="ticker-tape" />;
  return (
    <div className="ticker-tape">
      <div className="tape-inner">
        {[...items, ...items].map((s, i) => (
          <span className="tape-item" key={i}>
            <span className="t">{s.symbol.replace("^", "")}</span>{" "}
            {Number(s.close || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}{" "}
            <span className={(s.change_pct || 0) >= 0 ? "up" : "down"}>
              {(s.change_pct || 0) >= 0 ? "+" : ""}
              {(s.change_pct * 100).toFixed(2)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState("landing");
  const [market, setMarket] = useState("");
  const [markets, setMarkets] = useState([]);
  const [indexes, setIndexes] = useState([]);
  const [tab, setTab] = useState("overview");
  const [drawer, setDrawer] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [refreshStatus, setRefreshStatus] = useState({
    running: false,
    last_fast_at: null,
    last_slow_at: null,
    next_fast_in: 0,
    next_slow_in: 0,
    error: "",
  });
  const [theme, setTheme] = useState(() => localStorage.getItem("sv-theme") || "system");
  const [resolvedTheme, setResolvedTheme] = useState("dark");
  const now = useClock();
  const refreshInFlight = useRef(false);

  // Resolve dark/light/system and apply to <html data-theme>.
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const apply = () => {
      const resolved = theme === "system" ? (media.matches ? "light" : "dark") : theme;
      document.documentElement.setAttribute("data-theme", resolved);
      setResolvedTheme(resolved);
    };
    apply();
    localStorage.setItem("sv-theme", theme);
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    fetchJSON("/api/markets")
      .then(setMarkets)
      .catch(() => setMarkets([]));
  }, []);

  const loadIndexes = () => {
    fetchJSON("/api/indexes")
      .then(setIndexes)
      .catch(() => {});
  };

  useEffect(() => {
    loadIndexes();
    const t = setInterval(loadIndexes, 60000);
    return () => clearInterval(t);
  }, []);

  const runBackgroundRefresh = useCallback(() => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    setRefreshStatus((prev) => ({ ...prev, running: true, error: "" }));
    fetch("/api/refresh", { method: "POST" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((payload) => {
        setRefreshStatus(payload);
        setRefreshToken((t) => t + 1);
        setLastUpdated(new Date());
      })
      .catch(() => {
        setRefreshStatus((prev) => ({ ...prev, running: false, error: "update failed" }));
      })
      .finally(() => {
        refreshInFlight.current = false;
      });
  }, []);

  useEffect(() => {
    if (view !== "terminal") return undefined;
    runBackgroundRefresh();
    const t = setInterval(runBackgroundRefresh, 15000);
    return () => clearInterval(t);
  }, [view, runBackgroundRefresh]);

  const ctx = useMemo(
    () => ({
      market,
      markets,
      indexes,
      theme,
      setTheme,
      setMarket,
      setTab,
      refreshAll: () => {
        setRefreshToken((t) => t + 1);
        setLastUpdated(new Date());
      },
      refreshToken,
      refreshStatus,
      openDrawer: (d) => setDrawer(d),
    }),
    [market, markets, indexes, refreshToken, refreshStatus, theme]
  );

  const enterTerminal = () => {
    setTab("overview");
    setView("terminal");
  };

  const ActiveTab = TAB_COMPONENTS[tab];

  return (
    <AppContext.Provider value={ctx}>
      {view === "landing" ? (
        <Landing onEnter={enterTerminal} />
      ) : (
        <div className="terminal">
          <header className="topbar">
            <button className="logo" style={{ border: "none", background: "transparent", cursor: "pointer" }} onClick={() => setView("landing")}>
              SV<span className="dim"> | STOCK VERDICT</span>
            </button>
            <TickerTape indexes={indexes} />
            <SearchBox />
            <select className="theme-toggle" value={theme} onChange={(e) => setTheme(e.target.value)} title="Theme">
              <option value="dark">DARK</option>
              <option value="light">LIGHT</option>
              <option value="system">SYSTEM</option>
            </select>
            <span className="clock">{now.toLocaleTimeString()}</span>
          </header>

          <nav className="tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`fn-tab ${tab === t.key ? "active" : ""}`}
                onClick={() => setTab(t.key)}
              >
                <span className="fn">{t.fn}</span>
                {t.label}
              </button>
            ))}
          </nav>

          <div className="controls" style={{ padding: "10px 16px 0" }}>
            <div className="field">
              <label>Market</label>
              <select value={market} onChange={(e) => setMarket(e.target.value)}>
                <option value="">ALL</option>
                {markets.map((m) => (
                  <option key={m.code} value={m.code}>
                    {m.code} — {m.name}
                  </option>
                ))}
              </select>
            </div>
            <button className="primary" onClick={() => ctx.refreshAll()}>
              ⟳ REFRESH
            </button>
            <button className="ghost" onClick={() => setTab("indexes")}>
              INDEX TAPE
            </button>
          </div>
          <main className="content">
            <ErrorBoundary key={tab}>
              <ActiveTab />
            </ErrorBoundary>
          </main>

          <footer className="statusbar">
            <span>SV 0.1.0</span>
            <span className={now.getSeconds() % 2 ? "pulse" : ""}>● LIVE</span>
            <span>
              LAST UPDATED{" "}
              {lastUpdated ? lastUpdated.toLocaleTimeString() : "--:--:--"}
            </span>
            <span>MARKET: {market || "ALL"}</span>
            <span style={{ marginLeft: "auto" }}>
              {now.toLocaleDateString()} {now.toLocaleTimeString()}
            </span>
          </footer>
        </div>
      )}
      <ErrorBoundary key={drawer ? `${drawer.type}:${drawer.v?.ticker || drawer.s?.cik || "?"}` : "closed"}>
        <Drawer item={drawer} onClose={() => setDrawer(null)} />
      </ErrorBoundary>
    </AppContext.Provider>
  );
}
