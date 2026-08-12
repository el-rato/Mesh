import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchJSON } from "./api.js";
import Landing from "./components/Landing.jsx";
import OverviewTab from "./components/OverviewTab.jsx";
import PortfolioTab from "./components/PortfolioTab.jsx";
import ScannerTab from "./components/ScannerTab.jsx";
import FundsTab from "./components/FundsTab.jsx";
import IndexesTab from "./components/IndexesTab.jsx";
import LSTMTab from "./components/LSTMTab.jsx";
import SimulationTab from "./components/SimulationTab.jsx";
import PaperTab from "./components/PaperTab.jsx";
import PaperOrderTicket from "./components/PaperOrderTicket.jsx";
import Drawer from "./components/Drawer.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import SearchBox from "./components/SearchBox.jsx";

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

const PRIMARY_TABS = [
  { key: "overview", fn: "F1", label: "OVERVIEW" },
  { key: "scanner", fn: "F2", label: "SCANNER" },
  { key: "portfolio", fn: "F3", label: "PORTFOLIO" },
  { key: "paper", fn: "F4", label: "PAPER" },
];

const SECONDARY_TABS = [
  { key: "lstm", fn: "F5", label: "LSTM" },
  { key: "sim", fn: "F6", label: "SIM / BACKTEST" },
  { key: "indexes", fn: "F7", label: "INDEXES" },
  { key: "funds", fn: "F8", label: "HEDGE FUNDS" },
];

const TAB_COMPONENTS = {
  overview: OverviewTab,
  portfolio: PortfolioTab,
  scanner: ScannerTab,
  paper: PaperTab,
  lstm: LSTMTab,
  sim: SimulationTab,
  indexes: IndexesTab,
  funds: FundsTab,
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
  const [paperTicket, setPaperTicket] = useState(null);
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
  const [portfolioIds, setPortfolioIds] = useState(new Set());
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef(null);
  const now = useClock();
  const refreshInFlight = useRef(false);

  useEffect(() => {
    const onDoc = (e) => {
      if (moreRef.current && !moreRef.current.contains(e.target)) setMoreOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

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

  const loadPortfolio = useCallback(() => {
    fetchJSON("/api/watchlist")
      .then((list) => setPortfolioIds(new Set((list || []).map((w) => `${w.market}:${w.ticker}`))))
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadPortfolio();
  }, [loadPortfolio]);

  useEffect(() => {
    if (refreshToken) loadPortfolio();
  }, [refreshToken, loadPortfolio]);

  const addToPortfolio = useCallback(
    (market, ticker, company = "") => {
      const id = `${market}:${ticker}`;
      setPortfolioIds((s) => new Set(s).add(id));
      fetchJSON("/api/watchlist?analyze=0", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market, ticker, company }),
      })
        .catch(() => setPortfolioIds((s) => { const n = new Set(s); n.delete(id); return n; }));
    },
    []
  );

  const removeFromPortfolio = useCallback((market, ticker) => {
    const id = `${market}:${ticker}`;
    setPortfolioIds((s) => { const n = new Set(s); n.delete(id); return n; });
    fetchJSON(`/api/watchlist?market=${encodeURIComponent(market)}&ticker=${encodeURIComponent(ticker)}`, { method: "DELETE" }).catch(() => {});
  }, []);

  const inPortfolio = useCallback((market, ticker) => portfolioIds.has(`${market}:${ticker}`), [portfolioIds]);

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
      openPaperTicket: (t) => setPaperTicket(t),
      portfolioIds,
      addToPortfolio,
      removeFromPortfolio,
      inPortfolio,
    }),
    [market, markets, indexes, refreshToken, refreshStatus, theme, portfolioIds, addToPortfolio, removeFromPortfolio, inPortfolio]
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
            {PRIMARY_TABS.map((t) => (
              <button
                key={t.key}
                className={`fn-tab ${tab === t.key ? "active" : ""}`}
                onClick={() => setTab(t.key)}
              >
                <span className="fn">{t.fn}</span>
                {t.label}
              </button>
            ))}
            <div className="more-wrap" ref={moreRef}>
              <button
                className={`fn-tab more-btn ${SECONDARY_TABS.some((t) => t.key === tab) ? "active" : ""}`}
                onClick={() => setMoreOpen((v) => !v)}
              >
                MORE <span className="expand">{moreOpen ? "−" : "+"}</span>
              </button>
              {moreOpen && (
                <div className="more-menu">
                  {SECONDARY_TABS.map((t) => (
                    <button
                      key={t.key}
                      className={`more-item ${tab === t.key ? "active" : ""}`}
                      onClick={() => {
                        setTab(t.key);
                        setMoreOpen(false);
                      }}
                    >
                      <span className="fn">{t.fn}</span>
                      {t.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
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
      <ErrorBoundary key={paperTicket ? `${paperTicket.market}:${paperTicket.ticker}` : "closed"}>
        <PaperOrderTicket ticket={paperTicket} onClose={() => setPaperTicket(null)} />
      </ErrorBoundary>
    </AppContext.Provider>
  );
}
