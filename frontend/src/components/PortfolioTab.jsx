import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchJSON, paperPortfolio } from "../api.js";
import { useApp } from "../App.jsx";
import { verdictBadge, verdictClass } from "./ui.jsx";
import AddToPortfolioButton from "./AddToPortfolioButton.jsx";
import SecurityLink from "./SecurityLink.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function sig(v) {
  return v === "BULL" ? "bull" : v === "BEAR" ? "bear" : "neutral";
}

function agreement(analysis) {
  const sigs = (analysis?.committee?.signals) || [];
  return `${sigs.filter((s) => s.available).length}/5`;
}

export default function PortfolioTab() {
  const { refreshToken, openDrawer, openPaperTicket, removeFromPortfolio } = useApp();
  const [tracked, setTracked] = useState(null);
  const [analysisMap, setAnalysisMap] = useState({});
  const [positions, setPositions] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    Promise.all([fetchJSON("/api/watchlist"), fetchJSON("/api/verdicts"), paperPortfolio()])
      .then(([wl, vd, pf]) => {
        setTracked(wl || []);
        setAnalysisMap(vd || {});
        setPositions(pf?.positions || []);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken, load]);

  const rows = useMemo(() => {
    return (tracked || []).map((w) => {
      const a = analysisMap[`${w.market}:${w.ticker}`] || {};
      return {
        market: w.market,
        ticker: w.ticker,
        company: w.company || a.company || "",
        verdict: a.verdict || w.verdict,
        confidence: a.confidence ?? w.confidence,
        close: a.close,
        momentum: a.momentum_20,
        agreement: agreement(a),
        status: a.data_status || "ok",
        decided_at: a.updated_at || w.decided_at || "",
      };
    });
  }, [tracked, analysisMap]);

  if (error && !tracked) return <div className="error">ERROR: {error}</div>;
  if (!tracked) return <div className="empty">LOADING PORTFOLIO…</div>;

  const openPos = (p, action) =>
    openPaperTicket({ market: p.market, ticker: p.ticker, company: p.ticker, action });

  return (
    <>
      {error && <div className="scan-warning">⚠ {error}</div>}

      <div className="landing-h" style={{ marginTop: 8 }}>TRACKED SECURITIES</div>
      {!rows.length ? (
        <div className="empty">
          NO SECURITIES TRACKED — USE [+ ADD TO PORTFOLIO] ON SEARCH, SCANNER, OVERVIEW OR A DOSSIER.
        </div>
      ) : (
        <div className="portfolio-table">
          <div className="paper-row paper-row-head">
            <span>SECURITY</span><span>PRICE</span><span>VERDICT</span><span>CONV</span><span>AGREE</span><span>STATUS</span><span>ACTIONS</span>
          </div>
          {rows.map((r) => (
            <div className="paper-row" key={`${r.market}:${r.ticker}`}>
              <span className="sym"><SecurityLink market={r.market} ticker={r.ticker}>{r.ticker}</SecurityLink><span className="dim"> {r.market}</span></span>
              <span>{r.close != null ? num(r.close).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}</span>
              <span>{verdictBadge({ verdict: r.verdict })}</span>
              <span>{(num(r.confidence) * 100).toFixed(0)}%</span>
              <span>{r.agreement}</span>
              <span className={r.status === "no_data" ? "down" : "dim"}>{r.status === "no_data" ? "NO DATA" : "OK"}</span>
              <span className="paper-actions">
                <button className="ghost" onClick={() => openDrawer({ type: "stock", v: { market: r.market, ticker: r.ticker, company: r.company, verdict: r.verdict, confidence: num(r.confidence), combined_score: 0, reason: ["PORTFOLIO"] } })}>DOSSIER</button>
                <button className="paper-buy" onClick={() => openPaperTicket({ market: r.market, ticker: r.ticker, company: r.company, action: "BUY" })}>BUY</button>
                <button className="paper-short" onClick={() => openPaperTicket({ market: r.market, ticker: r.ticker, company: r.company, action: "SHORT" })}>SHORT</button>
                <button className="ghost" onClick={() => removeFromPortfolio(r.market, r.ticker)}>REMOVE</button>
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="landing-h" style={{ marginTop: 14 }}>PAPER POSITIONS</div>
      {!positions.length ? (
        <div className="empty">NO OPEN PAPER POSITIONS — BUY / SHORT TO SIMULATE A TRADE.</div>
      ) : (
        <div className="portfolio-table">
          <div className="paper-row paper-row-head">
            <span>SECURITY</span><span>DIR</span><span>QTY</span><span>ENTRY</span><span>CURRENT</span><span>UNREALIZED</span><span>ACTIONS</span>
          </div>
          {positions.map((p) => (
            <div className="paper-row" key={`${p.market}:${p.ticker}`}>
              <span className="sym"><SecurityLink market={p.market} ticker={p.ticker}>{p.ticker}</SecurityLink></span>
              <span className={p.direction === "LONG" ? "up" : "down"}>{p.direction}</span>
              <span>{p.qty}</span>
              <span>{num(p.entry).toFixed(4)}</span>
              <span>{num(p.price).toFixed(4)}</span>
              <span style={{ color: p.unrealized >= 0 ? "var(--bull)" : "var(--bear)" }}>{p.unrealized > 0 ? "+" : ""}{num(p.unrealized).toFixed(2)}</span>
              <span className="paper-actions">
                <button className="paper-buy" onClick={() => openPos(p, p.direction === "LONG" ? "BUY" : "CLOSE")}>{p.direction === "LONG" ? "BUY" : "CLOSE"}</button>
                <button className="ghost" onClick={() => openPos(p, p.direction === "LONG" ? "SELL" : "COVER")}>{p.direction === "LONG" ? "SELL" : "COVER"}</button>
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}