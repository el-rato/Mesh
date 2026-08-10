import { useState } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { scoreBadge, Row } from "./ui.jsx";

export default function DiscoverTab() {
  const { market, refreshToken, openDrawer } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [minScore, setMinScore] = useState(0.2);
  const [maxResults, setMaxResults] = useState(20);
  const [minArticles, setMinArticles] = useState(5);

  const run = async () => {
    setLoading(true);
    setError("");
    setData(null);
    try {
      const params = new URLSearchParams();
      if (market) params.append("market", market);
      params.append("min_score", minScore);
      params.append("max_results", maxResults);
      params.append("min_articles", minArticles);
      const d = await fetchJSON(`/api/discover?${params.toString()}`);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const watch = async (d) => {
    try {
      await fetchJSON("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market: d.market, ticker: d.ticker, company: d.company || "" }),
      });
    } catch (e) {
      alert("Watch error: " + e.message);
    }
  };

  const list = data ? [...data].sort((a, b) => b.score - a.score) : [];

  return (
    <>
      <div className="controls">
        <div className="field">
          <label>Min Score</label>
          <input
            type="number"
            value={minScore}
            step="0.05"
            min="0"
            max="1"
            onChange={(e) => setMinScore(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Max Results</label>
          <input
            type="number"
            value={maxResults}
            step="5"
            min="1"
            max="100"
            onChange={(e) => setMaxResults(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Min Articles</label>
          <input
            type="number"
            value={minArticles}
            step="1"
            min="1"
            max="50"
            onChange={(e) => setMinArticles(e.target.value)}
          />
        </div>
        <button className="primary" onClick={run} disabled={loading}>
          {loading ? "SCANNING…" : "⟳ RUN DISCOVERY"}
        </button>
      </div>

      {error && <div className="error">ERROR: {error}</div>}
      {loading && <div className="empty">SCANNING FEEDS… <span className="spinner" /></div>}
      {data && !data.length && (
        <div className="empty">NO BULLISH DISCOVERIES — ADJUST SCORE OR RETRY.</div>
      )}
      {data && data.length > 0 && (
        <div className="grid">
          {list.map((d) => (
            <div
              key={`${d.market}:${d.ticker}`}
              className="panel bull"
              onClick={() => openDrawer({ type: "stock", v: { market: d.market, ticker: d.ticker, verdict: "BULL", reason: d.headlines.slice(0, 2) } })}
            >
              <div className="panel-head">
                <div>
                  <div className="symbol">{d.ticker}</div>
                  <div className="name">
                    {d.company} ({d.market})
                  </div>
                </div>
                {scoreBadge(d.score)}
              </div>
              <Row k="SENTIMENT" v={d.score.toFixed(3)} />
              <Row k="ARTICLES" v={d.article_count ?? d.headlines.length} />
              <div className="reason">
                {d.headlines.slice(0, 2).map((h, i) => (
                  <div key={i}>→ {h}</div>
                ))}
              </div>
              <button
                className="ghost"
                style={{ marginTop: 8 }}
                onClick={(e) => {
                  e.stopPropagation();
                  watch(d);
                }}
              >
                ★ WATCH
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
