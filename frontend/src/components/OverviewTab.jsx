import { useEffect, useState, useCallback } from "react";
import { fetchJSON, newsFeed, watchlist, tickerStrip } from "../api.js";
import { useApp } from "../App.jsx";
import { verdictBadge, verdictClass, SectionHeader, StatusIndicator } from "./ui.jsx";
import SecurityLink from "./SecurityLink.jsx";
import AgentPanel from "./AgentPanel.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function pct(v) {
  const n = num(v);
  return `${n > 0 ? "+" : ""}${(n * 100).toFixed(2)}%`;
}

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return { icon: "🌙", label: "Night owl mode" };
  if (h < 12) return { icon: "🌅", label: "Good morning" };
  if (h < 18) return { icon: "☀️", label: "Good afternoon" };
  return { icon: "🌆", label: "Good evening" };
}

const SHORTCUTS = [
  { key: "stock", label: "stocks" },
  { key: "crypto", label: "crypto" },
  { key: "etf", label: "ETFs & funds" },
  { key: "index", label: "indices" },
  { key: "bond", label: "bonds" },
  { key: "forex", label: "Forex pairs" },
  { key: "portfolio", label: "portfolio" },
  { key: "watchlist", label: "watchlist" },
];

export default function OverviewTab() {
  const { market, markets, indexes, refreshToken, openDrawer, userEmail } = useApp();
  const [verdicts, setVerdicts] = useState([]);
  const [news, setNews] = useState([]);
  const [watch, setWatch] = useState([]);
  const [active, setActive] = useState([]);
  const [error, setError] = useState("");

  const loadVerdicts = useCallback(() => {
    setError("");
    fetchJSON("/api/verdicts")
      .then((d) => setVerdicts(Object.values(d)))
      .catch((e) => setError(e.message));
  }, []);

  const loadRails = useCallback(() => {
    newsFeed(6).then(setNews).catch(() => {});
    watchlist().then(setWatch).catch(() => {});
    tickerStrip()
      .then((rows) =>
        setActive(
          [...(rows || [])]
            .filter((r) => r.change_pct != null)
            .sort((a, b) => (b.change_pct || 0) - (a.change_pct || 0))
            .slice(0, 6)
        )
      )
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadVerdicts();
    loadRails();
    const t = setInterval(loadVerdicts, 30000);
    return () => clearInterval(t);
  }, [loadVerdicts, loadRails]);

  useEffect(() => {
    if (refreshToken) {
      loadVerdicts();
      loadRails();
    }
  }, [refreshToken, loadVerdicts, loadRails]);

  const topVerdicts = [...verdicts]
    .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
    .slice(0, 6);

  const strip = [...(indexes || [])].slice(0, 14);
  const openCount = (markets || []).filter((m) => m.status?.status === "open").length;
  const greet = greeting();

  const openDossier = (v) =>
    openDrawer({
      type: "stock",
      v: { market: v.market, ticker: v.ticker, company: v.company || "", reason: ["OVERVIEW"] },
    });

  return (
    <div className="overview">
      <div className="ov-frame">
        {/* Top index strip */}
        <div className="ov-strip">
          {strip.map((s) => {
            const up = (s.change_pct || 0) >= 0;
            return (
              <span key={`${s.market}:${s.symbol}`} className="ov-strip-item" onClick={() => openDrawer({ type: "stock", v: { market: s.market, ticker: s.symbol, company: s.name || "", reason: ["OVERVIEW"] } })}>
                <span className="ov-strip-sym">{s.symbol}</span>
                <span className="ov-strip-px">{num(s.close).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                <span className={`ov-strip-chg ${up ? "up" : "down"}`}>{pct(s.change_pct)}</span>
              </span>
            );
          })}
        </div>

        <div className="ov-body">
          {/* LEFT MAIN */}
          <main className="ov-main">
            {/* Greeting */}
            <header className="ov-greet">
              <span className="ov-greet-mark">{greet.icon}</span>
              <span className="ov-greet-text">
                <strong>{greet.label}</strong>, {userEmail?.split("@")[0] || "you"}.{" "}
                {market ? `${market} markets await.` : "Markets await."}
              </span>
              <span className="ov-greet-status dim">
                <span className="dot" /> {openCount}/{markets?.length || 0} OPEN
              </span>
            </header>

            {/* Shortcuts */}
            <div className="ov-card ov-shortcuts">
              <div className="ov-panel-label">SHORTCUTS</div>
              <div className="ov-shortcut-grid">
                {SHORTCUTS.map((s) => (
                  <span key={s.key} className="ov-shortcut">
                    <span className="ov-shortcut-token">/{s.key}</span>
                    <span className="ov-shortcut-label">{s.label}</span>
                  </span>
                ))}
              </div>
            </div>

            {/* Agent */}
            <AgentPanel />

            {/* Committee views */}
            <div className="ov-card">
              <SectionHeader title="TOP COMMITTEE VIEWS" />
              {topVerdicts.length ? (
                <div className="ov-verdict-grid">
                  {topVerdicts.map((v) => (
                    <div key={`${v.market}:${v.ticker}`} className={`ov-panel ${verdictClass(v.verdict)}`} onClick={() => openDossier(v)}>
                      <div className="ov-panel-head">
                        <div>
                          <SecurityLink market={v.market} ticker={v.ticker} className="symbol" style={{ fontSize: 13 }}>{v.ticker}</SecurityLink>
                          <div className="name">{v.market}</div>
                        </div>
                        {verdictBadge(v)}
                      </div>
                      <div className="conf-bar">
                        <span style={{ width: (v.confidence * 100).toFixed(0) + "%", background: v.verdict === "BULL" ? "var(--bull)" : v.verdict === "BEAR" ? "var(--bear)" : "var(--neutral)" }} />
                      </div>
                      <div className="row"><span className="label">CONFIDENCE</span><span className="value">{(v.confidence * 100).toFixed(0)}%</span></div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty">NO VERDICTS YET — RUN A REFRESH.</div>
              )}
            </div>
          </main>

          {/* RIGHT RAIL */}
          <aside className="ov-rail">
            <div className="ov-panel">
              <div className="ov-panel-label">LIVE NEWS</div>
              {news.length ? (
                <div className="ov-news-list">
                  {news.map((n, i) => (
                    <a key={`${n.url}-${i}`} className="ov-news-item" href={n.url} target="_blank" rel="noopener noreferrer">
                      <div className="ov-news-title">{n.title}</div>
                      <div className="ov-news-meta dim">
                        {n.source}
                        {(n.ticker || n.security_id) && <span className="ov-news-tk">{n.ticker || n.security_id}</span>}
                        <span>{String(n.published_at || n.fetched_at || "").slice(0, 10)}</span>
                      </div>
                    </a>
                  ))}
                </div>
              ) : (
                <div className="empty">LOADING NEWS…</div>
              )}
            </div>

            <div className="ov-panel">
              <div className="ov-panel-label">WATCHLIST</div>
              {watch.length ? (
                <div className="ov-watch-list">
                  {watch.slice(0, 6).map((w) => (
                    <div key={`${w.market}:${w.ticker}`} className="ov-watch-item" onClick={() => openDossier(w)}>
                      <span className="ov-watch-tk">{w.ticker}</span>
                      <span className="ov-watch-name dim">{w.market}</span>
                      {w.verdict && (
                        <span className={`ov-watch-verdict ${verdictClass(w.verdict)}`}>{w.verdict}</span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty">NO WATCHLIST ITEMS.</div>
              )}
            </div>

            <div className="ov-panel">
              <div className="ov-panel-label">MOST ACTIVE</div>
              {active.length ? (
                <div className="ov-active-list">
                  {active.map((a, i) => {
                    const up = (a.change_pct || 0) >= 0;
                    return (
                      <div key={a.security_id || i} className="ov-active-item" onClick={() => openDossier(a)}>
                        <span className="ov-active-tk">{a.ticker}</span>
                        <span className="ov-active-px">{num(a.close).toLocaleString()}</span>
                        <span className={`ov-active-chg ${up ? "up" : "down"}`}>{pct(a.change_pct)}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="empty">LOADING…</div>
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
