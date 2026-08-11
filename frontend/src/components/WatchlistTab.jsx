import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { verdictBadge, verdictClass, Row } from "./ui.jsx";

export default function WatchlistTab() {
  const { refreshToken, openDrawer } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchJSON("/api/watchlist")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
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
  if (!data) return <div className="empty">LOADING…</div>;
  if (!data.length)
    return (
      <div className="empty">
        WATCHLIST EMPTY — OPEN <strong>DISCOVER (F3)</strong> AND WATCH A STOCK.
      </div>
    );

  const list = [...data].sort((a, b) => (b.combined_score || -99) - (a.combined_score || -99));

  const remove = async (w) => {
    try {
      await fetchJSON(
        `/api/watchlist?market=${encodeURIComponent(w.market)}&ticker=${encodeURIComponent(w.ticker)}`,
        { method: "DELETE" }
      );
      load();
    } catch (e) {
      alert("Watchlist error: " + e.message);
    }
  };

  return (
    <div className="grid">
      {list.map((w) => {
        const cls = w.verdict ? verdictClass(w.verdict) : "neutral";
        return (
          <div
            key={`${w.market}:${w.ticker}`}
            className={`panel ${cls}`}
            onClick={() =>
              openDrawer({
                type: "stock",
                v: {
                  market: w.market,
                  ticker: w.ticker,
                  verdict: w.verdict || "NEUTRAL",
                  confidence: w.confidence || 0,
                  news_score: w.news_score || 0,
                  price_score: w.price_score || 0,
                  combined_score: w.combined_score || 0,
                  reason: w.reason && w.reason.length ? w.reason : ["WATCHLIST ITEM"],
                },
              })
            }
          >
            <div className="panel-head">
              <div>
                <div className="symbol">{w.ticker}</div>
                <div className="name">
                  {w.company || w.market} ({w.market})
                </div>
              </div>
              {w.verdict ? verdictBadge(w) : <span className="badge neutral">COMPUTING…</span>}
            </div>
            {w.verdict ? (
              <>
                <Row k="CONFIDENCE" v={`${(w.confidence * 100).toFixed(0)}%`} />
                <div className="conf-bar">
                  <span
                    style={{
                      width: (w.confidence * 100).toFixed(0) + "%",
                      background:
                        w.verdict === "BULL"
                          ? "var(--bull)"
                          : w.verdict === "BEAR"
                          ? "var(--bear)"
                          : "var(--neutral)",
                    }}
                  />
                </div>
                <Row k="COMBINED" v={Number(w.combined_score || 0).toFixed(3)} />
              </>
            ) : (
              <div className="reason">FETCHING NEWS &amp; PRICE…</div>
            )}
            <button
              className="ghost"
              style={{ marginTop: 10 }}
              onClick={(e) => {
                e.stopPropagation();
                remove(w);
              }}
            >
              ✕ REMOVE
            </button>
          </div>
        );
      })}
    </div>
  );
}
