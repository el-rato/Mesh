import { useState } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { Row } from "./ui.jsx";

export default function RedditTab() {
  const { openDrawer } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [subs, setSubs] = useState("wallstreetbets,stocks,investing");
  const [limit, setLimit] = useState(50);
  const [timeFilter, setTimeFilter] = useState("day");
  const [minMentions, setMinMentions] = useState(2);
  const [minScore, setMinScore] = useState(10);

  const run = async () => {
    setLoading(true);
    setError("");
    setData(null);
    try {
      const params = new URLSearchParams();
      if (subs.trim()) params.append("subreddits", subs.trim());
      params.append("limit", limit);
      params.append("time_filter", timeFilter);
      params.append("min_mentions", minMentions);
      params.append("min_score", minScore);
      const d = await fetchJSON(`/api/reddit?${params.toString()}`);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const recs = data?.recommendations || [];

  return (
    <>
      <div className="controls">
        <div className="field">
          <label>Subreddits</label>
          <input
            type="text"
            value={subs}
            onChange={(e) => setSubs(e.target.value)}
            style={{ minWidth: 220 }}
          />
        </div>
        <div className="field">
          <label>Posts / Sub</label>
          <input
            type="number"
            value={limit}
            min="10"
            max="200"
            onChange={(e) => setLimit(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Time Filter</label>
          <select value={timeFilter} onChange={(e) => setTimeFilter(e.target.value)}>
            <option value="hour">HOUR</option>
            <option value="day">DAY</option>
            <option value="week">WEEK</option>
            <option value="month">MONTH</option>
            <option value="year">YEAR</option>
          </select>
        </div>
        <div className="field">
          <label>Min Mentions</label>
          <input
            type="number"
            value={minMentions}
            min="1"
            max="20"
            onChange={(e) => setMinMentions(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Min Score</label>
          <input
            type="number"
            value={minScore}
            min="0"
            max="1000"
            onChange={(e) => setMinScore(e.target.value)}
          />
        </div>
        <button className="primary" onClick={run} disabled={loading}>
          {loading ? "SCANNING…" : "⟳ SCAN REDDIT"}
        </button>
      </div>

      {error && <div className="error">ERROR: {error}</div>}
      {loading && <div className="empty">SCANNING REDDIT… THIS CAN TAKE A MINUTE.</div>}
      {data && !recs.length && (
        <div className="empty">NO RECOMMENDATIONS FROM REDDIT WITH THOSE FILTERS.</div>
      )}

      {recs.length > 0 && (
        <div className="grid">
          {recs.map((r) => (
            <div
              key={`${r.ticker}:${r.subreddit}`}
              className="panel bull"
              onClick={() =>
                openDrawer({
                  type: "stock",
                  v: {
                    market: r.market || "NYSE",
                    ticker: r.ticker,
                    verdict: r.sentiment === "positive" ? "BULL" : r.sentiment === "negative" ? "BEAR" : "NEUTRAL",
                    reason: [
                      `r/${r.subreddit}: ${r.mentions} mentions · score ${r.score} · sentiment ${r.sentiment || "n/a"}`,
                    ],
                  },
                })
              }
            >
              <div className="panel-head">
                <div>
                  <div className="symbol">{r.ticker}</div>
                  <div className="name">r/{r.subreddit}</div>
                </div>
                <span className="badge neutral">{r.mentions} MENTIONS</span>
              </div>
              <Row k="SCORE" v={r.score} />
              <Row k="SENTIMENT" v={r.sentiment ? r.sentiment.toUpperCase() : "N/A"} />
              <div className="reason">{r.post_titles ? r.post_titles.slice(0, 3).map((t, i) => <div key={i}>→ {t}</div>) : ""}</div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
