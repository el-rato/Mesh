import { useEffect, useState, useCallback } from "react";
import { fetchJSON, notifications, notificationsScan } from "../api.js";
import { useApp } from "../App.jsx";
import { verdictBadge, verdictClass } from "./ui.jsx";
import SecurityLink, { SecurityText } from "./SecurityLink.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function pct(v) {
  return `${v > 0 ? "+" : ""}${(num(v) * 100).toFixed(2)}%`;
}

function sevClass(sev) {
  if (sev === "HIGH") return "high";
  if (sev === "IMPORTANT") return "important";
  return "";
}

export default function OverviewTab() {
  const { market, markets, indexes, refreshToken, setTab, openDrawer, setScreenerPrefill } = useApp();
  const [verdicts, setVerdicts] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState("");

  const loadVerdicts = useCallback(() => {
    setError("");
    fetchJSON("/api/verdicts")
      .then((d) => setVerdicts(Object.values(d)))
      .catch((e) => setError(e.message));
  }, []);

  const loadAlerts = useCallback(() => {
    notifications(40)
      .then(setAlerts)
      .catch(() => {});
  }, []);

  useEffect(() => {
    // Bootstrap the event detectors once, then poll. Repeated scans are
    // idempotent (deterministic event keys).
    notificationsScan().catch(() => {});
    loadAlerts();
    const t = setInterval(loadAlerts, 20000);
    return () => clearInterval(t);
  }, [loadAlerts]);

  useEffect(() => {
    loadVerdicts();
    const t = setInterval(loadVerdicts, 30000);
    return () => clearInterval(t);
  }, [loadVerdicts]);

  useEffect(() => {
    if (refreshToken) {
      loadVerdicts();
      loadAlerts();
    }
  }, [refreshToken, loadVerdicts, loadAlerts]);

  const bulls = verdicts.filter((v) => verdictClass(v.verdict) === "bull").length;
  const bears = verdicts.filter((v) => verdictClass(v.verdict) === "bear").length;
  const neut = verdicts.length - bulls - bears;
  const avgConf = verdicts.length
    ? ((verdicts.reduce((s, v) => s + (v.confidence || 0), 0) / verdicts.length) * 100).toFixed(1)
    : "--";
  const marketsOpen = (markets || []).filter((m) => m.status?.status === "open").length;

  const topMovers = [...(indexes || [])]
    .sort((a, b) => (b.change_pct || 0) - (a.change_pct || 0))
    .slice(0, 5);

  const topVerdicts = [...verdicts]
    .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
    .slice(0, 6);

  const important = alerts.filter((a) => a.severity === "HIGH" || a.severity === "IMPORTANT").slice(0, 6);
  const significant = alerts.filter((a) => a.severity === "HIGH").slice(0, 6);

  const openDossier = (a) =>
    openDrawer({
      type: "stock",
      v: { market: a.market || "", ticker: a.ticker || "", company: "", reason: ["NOTIFICATION"] },
    });

  const screenSimilar = (a) => {
    const prefill = { market: a.market || "" };
    if (a.type === "significant_trade" || a.type === "position_reversed") prefill.market = a.market || "";
    if (a.payload?.notional) prefill.min_conviction = "0.60";
    setScreenerPrefill(prefill);
    setTab("screener");
  };

  return (
    <div className="landing">
      <div className="landing-h" style={{ fontSize: 15 }}>
        OVERVIEW <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>· {market || "ALL MARKETS"}</span>
      </div>

      {error && (
        <div className="error">
          <div style={{ marginBottom: 10 }}>ERROR: {error}</div>
          <button className="primary" onClick={loadVerdicts}>⟳ RETRY</button>
        </div>
      )}

      {/* Market status */}
      <div className="market-status-strip">
        <span className="dim">MARKET STATUS</span>
        {(markets || []).map((m) => {
          const st = m.status || {};
          const open = st.status === "open";
          return (
            <span key={m.code} className={`market-chip ${open ? "open" : "closed"}`} title={`${m.name} · ${st.timezone || m.timezone || ""} local ${st.local_time || ""}`}>
              <span className="dot" />
              {m.code}
              {open ? <span className="chip-time">OPEN {st.opened_at}</span> : <span className="chip-time">{st.opening_soon ? "SOON" : st.local_time || "CLOSED"}</span>}
            </span>
          );
        })}
        <span className="dim" style={{ marginLeft: "auto" }}>{marketsOpen}/{markets?.length || 0} MARKETS OPEN</span>
      </div>

      <div className="landing-stats">
        <div className="landing-stat"><div className="k">STOCKS SCORED</div><div className="v">{verdicts.length || "—"}</div></div>
        <div className="landing-stat"><div className="k">BULL</div><div className="v" style={{ color: "var(--bull)" }}>{bulls}</div></div>
        <div className="landing-stat"><div className="k">BEAR</div><div className="v" style={{ color: "var(--bear)" }}>{bears}</div></div>
        <div className="landing-stat"><div className="k">NEUTRAL</div><div className="v">{neut}</div></div>
        <div className="landing-stat"><div className="k">AVG CONFIDENCE</div><div className="v">{verdicts.length ? `${avgConf}%` : "--"}</div></div>
        <div className="landing-stat"><div className="k">ALERTS</div><div className="v" style={{ color: significant.length ? "var(--bear)" : "var(--blue)" }}>{alerts.length}</div></div>
      </div>

      <div className="landing-cols">
        {/* Major movement */}
        <div className="landing-col">
          <div className="landing-h">MAJOR MARKET MOVEMENT</div>
          <div className="grid landing-index-grid">
            {topMovers.map((s) => {
              const up = (s.change_pct || 0) >= 0;
              return (
                <div key={s.symbol} className={`panel ${up ? "bull" : "bear"}`} onClick={() => setTab("indexes")}>
                  <div className="panel-head">
                    <div>
                      <div className="symbol" style={{ fontSize: 13 }}>{s.name}</div>
                      <div className="name">{s.market} · {s.symbol}</div>
                    </div>
                    <span className={`badge ${up ? "bull" : "bear"}`}>{up ? "+" : ""}{(s.change_pct * 100).toFixed(2)}%</span>
                  </div>
                  <div className="row">
                    <span className="label">CLOSE</span>
                    <span className="value">{Number(s.close).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                </div>
              );
            })}
            {!topMovers.length && <div className="empty">NO INDEX DATA — RUN A REFRESH.</div>}
          </div>

          <div className="landing-h" style={{ marginTop: 12 }}>TOP COMMITTEE VIEWS</div>
          {topVerdicts.length ? (
            <div className="grid landing-verdict-grid">
              {topVerdicts.map((v) => (
                <div key={`${v.market}:${v.ticker}`} className={`panel ${verdictClass(v.verdict)}`} onClick={() => openDrawer({ type: "stock", v: { market: v.market, ticker: v.ticker, company: v.company || "", reason: ["OVERVIEW"] } })}>
                  <div className="panel-head">
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

        {/* Alerts */}
        <div className="landing-col">
          <div className="landing-h">IMPORTANT ALERTS</div>
          {important.length ? (
            <div className="notification-list">
              {important.map((a) => (
                <div key={a.event_key} className={`notification-item ${sevClass(a.severity)}`}>
                  <div className="notif-head">
                    <span className={`badge sev ${sevClass(a.severity)}`}>{a.severity}</span>
                    <span className="notif-title"><SecurityText text={a.title} securityId={a.security_id} market={a.market} ticker={a.ticker} /></span>
                    <span className="dim notif-time">{String(a.created_at).slice(11, 19)}</span>
                  </div>
                  <div className="notif-msg"><SecurityText text={a.message} securityId={a.security_id} market={a.market} ticker={a.ticker} /></div>
                  <div className="notif-actions">
                    {a.security_id && <button className="ghost" onClick={() => openDossier(a)}>OPEN DOSSIER</button>}
                    <button className="ghost" onClick={() => screenSimilar(a)}>SCREEN SIMILAR</button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty">NO IMPORTANT ALERTS — THE TERMINAL IS QUIET RIGHT NOW.</div>
          )}

          <div className="landing-h" style={{ marginTop: 12 }}>SIGNIFICANT EVENTS</div>
          {significant.length ? (
            <div className="notification-list">
              {significant.map((a) => (
                <div key={a.event_key} className={`notification-item high`}>
                  <div className="notif-head">
                    <span className="badge sev high">{a.severity}</span>
                    <span className="notif-title"><SecurityText text={a.title} securityId={a.security_id} market={a.market} ticker={a.ticker} /></span>
                    <span className="dim notif-time">{String(a.created_at).slice(11, 19)}</span>
                  </div>
                  <div className="notif-msg"><SecurityText text={a.message} securityId={a.security_id} market={a.market} ticker={a.ticker} /></div>
                  <div className="notif-actions">
                    {a.security_id && <button className="ghost" onClick={() => openDossier(a)}>OPEN DOSSIER</button>}
                    <button className="ghost" onClick={() => screenSimilar(a)}>SCREEN SIMILAR</button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty">NO SIGNIFICANT EVENTS DETECTED.</div>
          )}
        </div>
      </div>

      <div className="team-note" style={{ marginTop: 12 }}>
        WORKFLOW: EVENT → SCREENER (F5) → DOSSIER (click a security) → PAPER TRADE (PAPER tab / dossier button).
        NOTIFICATIONS ARE DETERMINISTIC — A MARKET OPEN OR SIGNIFICANT EVENT FIRES ONCE PER SESSION, NOT PER REFRESH.
      </div>
    </div>
  );
}
