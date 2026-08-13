import { useEffect, useState, useCallback } from "react";
import { scanner } from "../api.js";
import { useApp } from "../App.jsx";
import AddToPortfolioButton from "./AddToPortfolioButton.jsx";
import SecurityLink from "./SecurityLink.jsx";
import { verdictBadge, verdictClass, RefreshStatus } from "./ui.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function dirToken(v) {
  let d = null;
  if (v && typeof v === "object") d = v.direction;
  else if (typeof v === "number") d = v > 0.05 ? "BULL" : v < -0.05 ? "BEAR" : "NEUTRAL";
  if (d === "BULL") return <span className="up">↑</span>;
  if (d === "BEAR") return <span className="down">↓</span>;
  return <span className="dim">–</span>;
}

function agreement(r) {
  const sigs = (r.committee && r.committee.signals) || [];
  return `${sigs.filter((s) => s.available).length}/5`;
}

function freshness(r) {
  if (!r.updated_at) return "—";
  const s = Math.max(0, Math.round((Date.now() - new Date(r.updated_at).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

function status(r) {
  return r.data_status === "no_data" ? "NO DATA" : "OK";
}

export default function ScannerTab() {
  const { market, markets, refreshToken, refreshStatus, openDrawer, openPaperTicket } = useApp();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState({
    verdict: "",
    signal_lstm: "",
    min_confidence: 0,
    min_momentum: "",
    min_technical: "",
    min_news: "",
    sort: "combined",
  });

  const set = (k, v) => setFilters((f) => ({ ...f, [k]: v }));

  const load = useCallback(() => {
    const params = { market: market || "", limit: 150, ...filters };
    if (!filters.min_momentum) delete params.min_momentum;
    if (!filters.min_technical) delete params.min_technical;
    if (!filters.min_news) delete params.min_news;
    if (!filters.min_confidence) delete params.min_confidence;
    scanner(params)
      .then((next) => {
        setRows(next);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, [market, filters]);

  useEffect(() => {
    load();
    const t = setInterval(load, 45000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken]);

  if (!rows && error)
    return (
      <div className="error">
        <div style={{ marginBottom: 12 }}>ERROR: {error}</div>
        <button className="primary" onClick={load}>⟳ RETRY</button>
      </div>
    );
  if (!rows) return <div className="empty">LOADING SCANNER…</div>;

  return (
    <>
      <div className="controls">
        <RefreshStatus status={refreshStatus} />
        <div className="field">
          <label>Market</label>
          <select value={market} onChange={(e) => {}} disabled>
            {market ? <option>{market}</option> : <option>ALL</option>}
          </select>
        </div>
        <div className="field">
          <label>Verdict</label>
          <select value={filters.verdict} onChange={(e) => set("verdict", e.target.value)}>
            <option value="">ANY</option>
            <option value="BULL">BULL</option>
            <option value="BEAR">BEAR</option>
            <option value="NEUTRAL">NEUTRAL</option>
          </select>
        </div>
        <div className="field">
          <label>LSTM</label>
          <select value={filters.signal_lstm} onChange={(e) => set("signal_lstm", e.target.value)}>
            <option value="">ANY</option>
            <option value="BULL">BULL</option>
            <option value="BEAR">BEAR</option>
          </select>
        </div>
        <div className="field">
          <label>Momentum ≥</label>
          <input
            type="number"
            step="0.05"
            value={filters.min_momentum}
            placeholder="0.00"
            onChange={(e) => set("min_momentum", e.target.value)}
          />
        </div>
        <div className="field">
          <label>Technical ≥</label>
          <input
            type="number"
            step="0.05"
            value={filters.min_technical}
            placeholder="0.00"
            onChange={(e) => set("min_technical", e.target.value)}
          />
        </div>
        <div className="field">
          <label>News ≥</label>
          <input
            type="number"
            step="0.05"
            value={filters.min_news}
            placeholder="0.00"
            onChange={(e) => set("min_news", e.target.value)}
          />
        </div>
        <div className="field">
          <label>Sort</label>
          <select value={filters.sort} onChange={(e) => set("sort", e.target.value)}>
            <option value="combined">COMBINED</option>
            <option value="confidence">CONFIDENCE</option>
            <option value="momentum">MOMENTUM</option>
            <option value="prop_up">P(UP)</option>
          </select>
        </div>
        <button className="primary" onClick={load}>⟳ SCAN</button>
      </div>

      {error && rows && (
        <div className="scan-warning">⚠ SCAN FAILED · SHOWING LAST KNOWN DATA — {error}</div>
      )}

      {!rows.length ? (
        <div className="empty">NO MATCHES — ADJUST FILTERS OR RUN A PRICE FETCH / SEARCH TO EXPAND THE UNIVERSE.</div>
      ) : (
        <div className="grid">
          {rows.map((r) => (
            <div
              key={`${r.market}:${r.ticker}`}
              className={`panel ${verdictClass(r.verdict)}`}
              onClick={() =>
                openDrawer({
                  type: "stock",
                  v: {
                    market: r.market,
                    ticker: r.ticker,
                    symbol: r.symbol,
                    company: r.company || "",
                    verdict: r.verdict,
                    confidence: num(r.confidence),
                    combined_score: num(r.combined_score),
                    reason: ["SCANNER RESULT"],
                  },
                })
              }
            >
              <div className="panel-head">
                <div>
                  <SecurityLink market={r.market} ticker={r.ticker} className="symbol">{r.ticker}</SecurityLink>
                  <div className="name">{r.market} · <SecurityLink market={r.market} ticker={r.ticker} className="link-inline">{r.company || r.symbol}</SecurityLink></div>
                </div>
                <div>
                  {verdictBadge(r)}
                  <span className="conv-pct">{(num(r.confidence) * 100).toFixed(0)}%</span>
                </div>
              </div>
              <div className="scanner-primary">
                <span className="lbl">PRICE</span><span className="val">{num(r.close).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
                <span className="lbl">QUANT</span><span className="val">{dirToken(r.quantitative)}</span>
                <span className="lbl">TECH</span><span className="val">{dirToken(r.technical && num(r.technical.score))}</span>
                <span className="lbl">NEWS</span><span className="val">{dirToken(r.news_available ? num(r.news_score) : null)}</span>
              </div>
              <div className="scanner-meta">
                <span>AGREEMENT {agreement(r)}</span>
                <span>FRESH {freshness(r)}</span>
                <span className={r.data_status === "no_data" ? "down" : "dim"}>{status(r)}</span>
              </div>
              <div className="row paper-actions" onClick={(e) => e.stopPropagation()}>
                <AddToPortfolioButton market={r.market} ticker={r.ticker} company={r.company} />
                <button className="paper-buy" onClick={() => openPaperTicket({ market: r.market, ticker: r.ticker, symbol: r.symbol, company: r.company, action: "BUY" })}>BUY</button>
                <button className="paper-short" onClick={() => openPaperTicket({ market: r.market, ticker: r.ticker, symbol: r.symbol, company: r.company, action: "SHORT" })}>SHORT</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}