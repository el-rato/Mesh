import { useEffect, useState } from "react";
import { fetchJSON, CHART_RANGES, rangeLabel } from "../api.js";
import PriceChart from "./PriceChart.jsx";
import { verdictBadge } from "./ui.jsx";

function StockDetail({ v, onClose }) {
  const [range, setRange] = useState("1mo");
  const [news, setNews] = useState([]);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    fetchJSON(`/api/news?market=${encodeURIComponent(v.market)}&ticker=${encodeURIComponent(v.ticker)}`)
      .then(setNews)
      .catch(() => {});
    fetchJSON(`/api/history/${encodeURIComponent(v.market)}/${encodeURIComponent(v.ticker)}`)
      .then(setHistory)
      .catch(() => {});
  }, [v.market, v.ticker]);

  return (
    <>
      <button className="close" onClick={onClose}>✕</button>
      <h2>
        {v.ticker}{" "}
        <span style={{ color: "var(--text-muted)", fontSize: 13, fontWeight: 400 }}>
          ({v.market})
        </span>{" "}
        {verdictBadge(v)}
      </h2>
      <div className="a-sub">
        CONFIDENCE {(v.confidence * 100).toFixed(0)}% · COMBINED {Number(v.combined_score || 0).toFixed(3)}
      </div>
      <div className="a-summary">{(v.reason || []).join("")}</div>

      <div className="drawer-main">
        <div>
          <h3>Price Chart</h3>
          <div className="chart-range-bar">
            {CHART_RANGES.map((r) => (
              <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>
                {rangeLabel(r)}
              </button>
            ))}
          </div>
          <div style={{ height: 240 }}>
            <PriceChart
              url={`/api/chart/${encodeURIComponent(v.market)}/${encodeURIComponent(v.ticker)}?range=${range}`}
              height={240}
              candles={range === "1d"}
            />
          </div>

          <h3>Price History</h3>
          {history.length ? (
            history.slice(0, 8).map((h, i) => (
              <div className="news-item" key={i}>
                <div className="sender">{h.decided_at}</div>
                <div className="src">{h.verdict}</div>
              </div>
            ))
          ) : (
            <div className="news-item"><span className="src">NONE</span></div>
          )}
        </div>

        <div className="news-panel">
          <h3>Latest Headlines</h3>
          {news.length ? (
            news.slice(0, 20).map((n, i) => (
              <div className="news-item" key={i}>
                <div className="sender">{n.title}</div>
                <div className="src">
                  {n.source} · {n.published_at}{" "}
                  <span
                    className={`sent ${
                      n.sentiment_label === "positive"
                        ? "up"
                        : n.sentiment_label === "negative"
                        ? "down"
                        : "neutral"
                    }`}
                  >
                    {n.sentiment_label || "N/A"}
                  </span>
                </div>
              </div>
            ))
          ) : (
            <div className="news-item"><span className="src">NO HEADLINES</span></div>
          )}
        </div>
      </div>
    </>
  );
}

function changeClass(action) {
  if (action === "BUY" || action === "NEW") return "up";
  if (action === "SELL" || action === "EXITED") return "down";
  return "";
}

function FundDetail({ s, onClose }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchJSON(`/api/funds/${encodeURIComponent(s.cik)}`)
      .then(setDetail)
      .catch((e) => setError(e.message));
  }, [s.cik]);

  return (
    <>
      <button className="close" onClick={onClose}>✕</button>
      {error ? (
        <div className="error">ERROR: {error}</div>
      ) : !detail ? (
        <div className="empty">LOADING…</div>
      ) : (
        <>
          <h2>
            {detail.fund}{" "}
            <span style={{ color: "var(--text-muted)", fontSize: 13, fontWeight: 400 }}>
              (13F · {detail.filing_date || "N/A"})
            </span>
          </h2>
          <div className="a-sub">PERIOD OF REPORT: {detail.period_of_report || "N/A"}</div>

          <h3>Top Holdings</h3>
          {(detail.holdings || []).slice(0, 20).map((h, i) => (
            <div className="news-item" key={i}>
              <div className="sender">{h.ticker || h.issuer}</div>
              <div className="src">
                ${(h.value / 1e6).toFixed(0)}M · {(h.shares || 0).toLocaleString()} SH ·{" "}
                {(h.pct_portfolio * 100).toFixed(2)}%
              </div>
            </div>
          ))}
          {!detail.holdings?.length && <div className="news-item"><span className="src">NONE</span></div>}

          <h3>Quarterly Changes</h3>
          {(detail.changes || []).slice(0, 15).map((c, i) => (
            <div className="news-item" key={i}>
              <div className="sender">
                {c.ticker || c.issuer} <span className={`badge ${c.action === "BUY" || c.action === "NEW" ? "bull" : c.action === "SELL" || c.action === "EXITED" ? "bear" : "neutral"}`}>{c.action}</span>
              </div>
              <div className="src">
                Δ {c.change_shares.toLocaleString()} SH ({(c.change_pct * 100).toFixed(1)}%) · $
                {(c.value / 1e6).toFixed(0)}M
              </div>
            </div>
          ))}
          {!detail.changes?.length && <div className="news-item"><span className="src">NEED A PRIOR QUARTER TO COMPARE</span></div>}
        </>
      )}
    </>
  );
}

export default function Drawer({ item, onClose }) {
  useEffect(() => {
    if (!item) return;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, onClose]);

  const open = !!item;

  return (
    <>
      <div className={`overlay ${open ? "open" : ""}`} onClick={onClose} />
      <aside className={`drawer ${open ? "open" : ""}`}>
        {item?.type === "stock" && <StockDetail v={item.v} onClose={onClose} />}
        {item?.type === "fund" && <FundDetail s={item.s} onClose={onClose} />}
      </aside>
    </>
  );
}
