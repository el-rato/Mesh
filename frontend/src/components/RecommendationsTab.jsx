import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { actionBadge, Row } from "./ui.jsx";

const ORDER = { BUY: 0, HOLD: 1, SELL: 2, AVOID: 3 };

export default function RecommendationsTab() {
  const { market, provider, refreshToken, openDrawer } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setError("");
    const q = market ? `?market=${encodeURIComponent(market)}` : "";
    fetchJSON("/api/agent" + q)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [market]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken]);

  const run = async () => {
    setLoading(true);
    setError("");
    setData(null);
    try {
      const params = new URLSearchParams({ live: 1, provider });
      if (market) params.append("market", market);
      const d = await fetchJSON(`/api/agent?${params.toString()}`);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const list = data?.recommendations || [];

  return (
    <>
      <div className="controls">
        <span className="row" style={{ margin: 0, alignItems: "center", gap: 10 }}>
          <span className="label">
            {data?.generated_at ? `GENERATED ${new Date(data.generated_at).toLocaleString()}` : "NO RUN YET"}
          </span>
        </span>
        <button className="primary" onClick={run} disabled={loading}>
          {loading ? "ASKING LLM…" : "⟳ RUN AI ANALYSIS"}
        </button>
      </div>

      {error && <div className="error">ERROR: {error}</div>}
      {loading && <div className="empty">ASKING {provider.toUpperCase()}… THIS CAN TAKE A MINUTE.</div>}
      {data && !list.length && (
        <div className="empty">NO RECOMMENDATIONS YET — RUN AI ANALYSIS.</div>
      )}
      {data && list.length > 0 && (
        <div className="grid">
          {[...list]
            .sort(
              (a, b) =>
                (ORDER[a.action] ?? 9) - (ORDER[b.action] ?? 9) || b.confidence - a.confidence
            )
            .map((r) => {
              const barColor =
                r.action === "BUY"
                  ? "var(--bull)"
                  : r.action === "SELL"
                  ? "var(--bear)"
                  : r.action === "HOLD"
                  ? "var(--neutral)"
                  : "var(--purple)";
              return (
                <div
                  key={`${r.market}:${r.ticker}`}
                  className="panel"
                  onClick={() =>
                    openDrawer({
                      type: "stock",
                      v: {
                        market: r.market,
                        ticker: r.ticker,
                        verdict:
                          r.action === "BUY" ? "BULL" : r.action === "SELL" ? "BEAR" : "NEUTRAL",
                        confidence: r.confidence,
                        news_score: 0,
                        price_score: 0,
                        combined_score: 0,
                        reason: [r.rationale],
                      },
                    })
                  }
                >
                  <div className="panel-head">
                    <div>
                      <div className="symbol">{r.ticker}</div>
                      <div className="name">
                        {r.company || r.market} ({r.market})
                      </div>
                    </div>
                    {actionBadge(r.action)}
                  </div>
                  <Row k="CONFIDENCE" v={`${(r.confidence * 100).toFixed(0)}%`} />
                  <div className="conf-bar">
                    <span style={{ width: (r.confidence * 100).toFixed(0) + "%", background: barColor }} />
                  </div>
                  <div className="reason">{r.rationale}</div>
                </div>
              );
            })}
        </div>
      )}
    </>
  );
}
