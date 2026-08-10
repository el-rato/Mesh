import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { Row } from "./ui.jsx";

function FundPanel({ s, onOpen }) {
  const buys = (s.changes || []).filter((c) => c.action === "BUY" || c.action === "NEW");
  const sells = (s.changes || []).filter((c) => c.action === "SELL" || c.action === "EXITED");
  const top = (s.top_holdings || []).slice(0, 4);
  return (
    <div className="panel neutral" onClick={() => onOpen(s)}>
      <div className="panel-head">
        <div>
          <div className="symbol">{s.fund}</div>
          <div className="name">13F · FILED {s.filing_date || "N/A"}</div>
        </div>
      </div>
      <Row k="PERIOD" v={s.period_of_report || "N/A"} />
      <Row k="TOP HOLDINGS" v={s.total_holdings || 0} />
      {top.map((h, i) => (
        <Row key={i} k={h.ticker || h.issuer} v={`$${(h.value / 1e6).toFixed(0)}M`} />
      ))}
      {buys.length > 0 && (
        <div className="reason up">▲ BUYS: {buys.slice(0, 4).map((b) => b.ticker || b.issuer).join(", ")}</div>
      )}
      {sells.length > 0 && (
        <div className="reason down">▼ SELLS: {sells.slice(0, 4).map((x) => x.ticker || x.issuer).join(", ")}</div>
      )}
    </div>
  );
}

export default function FundsTab() {
  const { openDrawer } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [updated, setUpdated] = useState("");

  const load = useCallback(() => {
    fetchJSON("/api/funds")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const refresh = async () => {
    setLoading(true);
    setError("");
    setData(null);
    try {
      const d = await fetchJSON("/api/funds/refresh");
      setData(d.summaries || d);
      setUpdated(new Date().toLocaleTimeString());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="controls">
        <button className="primary" onClick={refresh} disabled={loading}>
          {loading ? "FETCHING 13F…" : "⟳ REFRESH 13F DATA"}
        </button>
        {updated && <span className="label">UPDATED {updated}</span>}
      </div>

      {error && <div className="error">ERROR: {error}</div>}
      {loading && <div className="empty">FETCHING HEDGE FUND 13F FILINGS FROM SEC EDGAR… <span className="spinner" /></div>}
      {data && !data.length && <div className="empty">NO FUND DATA YET — CLICK REFRESH.</div>}

      {data && data.length > 0 && (
        <div className="grid">
          {data.map((s) => (
            <FundPanel key={s.cik} s={s} onOpen={(s) => openDrawer({ type: "fund", s })} />
          ))}
        </div>
      )}
    </>
  );
}
