import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { fetchJSON } from "./api.js";
import LandingPage from "./components/LandingPage.jsx";
import VerdictsTab from "./components/VerdictsTab.jsx";
import WatchlistTab from "./components/WatchlistTab.jsx";
import DiscoverTab from "./components/DiscoverTab.jsx";
import RiskTab from "./components/RiskTab.jsx";
import FundsTab from "./components/FundsTab.jsx";
import IndexesTab from "./components/IndexesTab.jsx";
import Drawer from "./components/Drawer.jsx";

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

const TABS = [
  { key: "home", fn: "F1", label: "HOME" },
  { key: "verdicts", fn: "F2", label: "VERDICTS" },
  { key: "watchlist", fn: "F3", label: "WATCHLIST" },
  { key: "discover", fn: "F4", label: "DISCOVER" },
  { key: "risk", fn: "F5", label: "RISK" },
  { key: "funds", fn: "F6", label: "HEDGE FUNDS" },
  { key: "indexes", fn: "F7", label: "INDEXES" },
];

const TAB_COMPONENTS = {
  home: LandingPage,
  verdicts: VerdictsTab,
  watchlist: WatchlistTab,
  discover: DiscoverTab,
  risk: RiskTab,
  funds: FundsTab,
  indexes: IndexesTab,
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
  const up = items[0].change_pct >= 0;
  return (
    <div className="ticker-tape">
      <div className="tape-inner">
        {[...items, ...items].map((s, i) => (
          <span className="tape-item" key={i}>
            <span className="t">{s.symbol.replace("^", "")}</span> {fmt(s.close)}{" "}
            <span className={s.change_pct >= 0 ? "up" : "down"}>
              {s.change_pct >= 0 ? "+" : ""}
              {(s.change_pct * 100).toFixed(2)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
  function fmt(n) {
    return Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
}

export default function App() {
  const [market, setMarket] = useState("");
  const [markets, setMarkets] = useState([]);
  const [indexes, setIndexes] = useState([]);
  const [tab, setTab] = useState("home");
  const [drawer, setDrawer] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const now = useClock();

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

  const ctx = useMemo(
    () => ({
      market,
      setMarket,
      setTab,
      refreshAll: () => {
        setRefreshToken((t) => t + 1);
        setLastUpdated(new Date());
      },
      refreshToken: refreshToken.current,
      openDrawer: (d) => setDrawer(d),
      closeDrawer: () => setDrawer(null),
    }),
    [market, refreshToken]
  );

  const ActiveTab = TAB_COMPONENTS[tab];

  return (
    <AppContext.Provider value={ctx}>
      <div className="terminal">
        <header className="topbar">
          <div className="logo">
            SV<span className="dim"> | STOCK VERDICT</span>
          </div>
          <TickerTape indexes={indexes} />
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
          <ActiveTab key={tab} />
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
      <Drawer item={drawer} onClose={() => setDrawer(null)} />
    </AppContext.Provider>
  );
}
