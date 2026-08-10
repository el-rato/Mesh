import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import PriceChart from "./PriceChart.jsx";
import { verdictBadge, verdictClass, Row } from "./ui.jsx";

export default function VerdictsTab() {
  const { market, refreshToken, openDrawer } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    const q = market ? `?market=${encodeURIComponent(market)}` : "";
    fetchJSON("/api/verdicts" + q)
      .then((d) => setData(Object.values(d)))
      .catch((e) => setError(e.message));
  }, [market]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken]);

  if (error) return <div className="error">ERROR: {error}</div>;
  if (!data) return <div className="empty">LOADING…</div>;
  if (!data.length)
    return (
      <div className="empty">
        NO VERDICTS YET. RUN <code>stock-alert-app verdict</code> THEN REFRESH.
      </div>
    );

  const list = [...data].sort((a, b) => b.combined_score - a.combined_score);

  return (
    <div className="grid">
      {list.map((v) => {
        const cls = verdictClass(v.verdict);
        const confPct = ((v.confidence || 0) * 100).toFixed(0);
        const confColor =
          v.verdict === "BULL" ? "var(--bull)" : v.verdict === "BEAR" ? "var(--bear)" : "var(--neutral)";
        return (
          <div
            key={`${v.market}:${v.ticker}`}
            className={`panel ${cls}`}
            onClick={() => openDrawer({ type: "stock", v })}
          >
            <div className="panel-head">
              <div>
                <div className="symbol">{v.ticker}</div>
                <div className="name">{v.market}</div>
              </div>
              {verdictBadge(v)}
            </div>
            <Row k="CONFIDENCE" v={`${confPct}%`} />
            <div className="conf-bar">
              <span style={{ width: confPct + "%", background: confColor }} />
            </div>
            <Row k="COMBINED" v={Number(v.combined_score || 0).toFixed(3)} />
            <div style={{ marginTop: 8, height: 80 }}>
              <PriceChart
                url={`/api/chart/${encodeURIComponent(v.market)}/${encodeURIComponent(v.ticker)}?range=1mo`}
                height={80}
                hideAxes
              />
            </div>
            <div className="reason">{(v.reason || []).join("")}</div>
          </div>
        );
      })}
    </div>
  );
}
