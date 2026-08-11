import { useEffect, useState, useCallback } from "react";
import { scanner } from "../api.js";
import { useApp } from "../App.jsx";
import { verdictBadge, verdictClass } from "./ui.jsx";

function lstmBadge(s) {
  if (!s || s.signal === "N/A") return null;
  const bull = s.signal === "BULL";
  return (
    <span className={`badge ${bull ? "bull" : "bear"}`} style={{ fontSize: 9, marginLeft: 4 }}>
      LSTM {bull ? "↑" : "↓"}
    </span>
  );
}

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

export default function ScannerTab() {
  const { market, markets, refreshToken, openDrawer } = useApp();
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
    setError("");
    const params = { market: market || "", limit: 150, ...filters };
    if (!filters.min_momentum) delete params.min_momentum;
    if (!filters.min_technical) delete params.min_technical;
    if (!filters.min_news) delete params.min_news;
    if (!filters.min_confidence) delete params.min_confidence;
    scanner(params)
      .then(setRows)
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

  if (error)
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
                  <div className="symbol">{r.ticker}</div>
                  <div className="name">{r.market} · {r.company || r.symbol}</div>
                </div>
                <div>
                  {verdictBadge(r)}
                  {lstmBadge(r.lstm)}
                </div>
              </div>
              <div className="row">
                <span className="label">CLOSE</span>
                <span className="value">{num(r.close).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
              </div>
              <div className="row">
                <span className="label">MOMENTUM 20D</span>
                <span className={`value ${num(r.momentum_20) >= 0 ? "up" : "down"}`}>{num(r.momentum_20).toFixed(2)}</span>
              </div>
              <div className="row">
                <span className="label">TECH</span>
                <span className="value">{num(r.technical.score).toFixed(2)}</span>
                <span className="label">NEWS</span>
                <span className="value">{num(r.news.score).toFixed(2)}</span>
              </div>
              <div className="row">
                <span className="label">CONFIDENCE</span>
                <span className="value">{(num(r.confidence) * 100).toFixed(0)}%</span>
              </div>
              <div className="conf-bar">
                <span
                  style={{
                    width: Math.round(num(r.confidence) * 100) + "%",
                    background: r.verdict === "BULL" ? "var(--bull)" : r.verdict === "BEAR" ? "var(--bear)" : "var(--neutral)",
                  }}
                />
              </div>
              <div className="row">
                <span className="label">COMBINED</span>
                <span className="value">{num(r.combined_score).toFixed(3)}</span>
              </div>
              {r.lstm && r.lstm.probability_up != null && (
                <div className="row">
                  <span className="label">LSTM P(↑)</span>
                  <span className="value" style={{ color: num(r.lstm.probability_up) >= 0.5 ? "var(--bull)" : "var(--bear)" }}>
                    {(num(r.lstm.probability_up) * 100).toFixed(1)}%
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}