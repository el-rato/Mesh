import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { RefreshStatus } from "./ui.jsx";

function signalClass(signal) {
  if (signal === "BULL") return "bull";
  if (signal === "BEAR") return "bear";
  return "neutral";
}

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

export default function LSTMTab() {
  const { market, refreshToken, refreshStatus, openDrawer } = useApp();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    fetchJSON("/api/verdicts")
      .then((d) => {
        const out = [];
        for (const v of Object.values(d || {})) {
          const l = v.lstm || {};
          if (l.signal && l.signal !== "N/A") {
            out.push({
              market: v.market,
              ticker: v.ticker,
              signal: l.signal,
              probability_up: l.probability_up,
              predicted_return: l.predicted_return,
              confidence: l.model_confidence,
            });
          }
        }
        setRows(out);
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

  const retry = () => {
    fetchJSON("/api/refresh", { method: "POST" }).then(() => {
      setTimeout(load, 8000);
    }).catch(() => {});
  };

  const sorted = [...(rows || [])].sort((a, b) => num(b.probability_up) - num(a.probability_up));
  const bull = sorted.filter((p) => p.signal === "BULL").length;
  const bear = sorted.filter((p) => p.signal === "BEAR").length;
  const neut = sorted.filter((p) => p.signal === "NEUTRAL").length;
  const lastSlow = refreshStatus?.last_slow_at ? new Date(refreshStatus.last_slow_at) : null;
  const nextIn = Math.round((refreshStatus?.next_slow_in || 0) / 60);

  const status = refreshStatus?.running
    ? { label: "RUNNING", cls: "refresh-status running" }
    : refreshStatus?.error
    ? { label: "STALE", cls: "refresh-status error" }
    : lastSlow
    ? { label: "UP TO DATE", cls: "refresh-status live" }
    : { label: "NO DATA", cls: "refresh-status" };

  return (
    <div>
      <div className="controls" style={{ marginBottom: 14 }}>
        <RefreshStatus status={refreshStatus} />
        <span className={`refresh-status ${status.cls.split(" ")[1] || ""}`}>{status.label}</span>
        <span className="refresh-status">
          LAST UPDATED {lastSlow ? lastSlow.toLocaleTimeString() : "—"}
        </span>
        <span className="refresh-status">NEXT UPDATE {refreshStatus?.running ? "…" : `${nextIn}m`}</span>
        {refreshStatus?.error && (
          <button className="ghost" onClick={retry}>⟳ RETRY</button>
        )}
      </div>

      {error && <div className="scan-warning">⚠ {error} — showing last known data.</div>}

      <div className="landing-stats" style={{ marginBottom: 14 }}>
        <div className="landing-stat"><div className="k">BULL</div><div className="v" style={{ color: "var(--bull)" }}>{bull}</div></div>
        <div className="landing-stat"><div className="k">BEAR</div><div className="v" style={{ color: "var(--bear)" }}>{bear}</div></div>
        <div className="landing-stat"><div className="k">NEUTRAL</div><div className="v">{neut}</div></div>
        <div className="landing-stat"><div className="k">MODELS</div><div className="v">{sorted.length}</div></div>
      </div>

      {!rows ? (
        <div className="empty">LOADING…</div>
      ) : !sorted.length ? (
        <div className="empty">NO LSTM RESULTS YET — THE BACKGROUND ANALYSIS WILL POPULATE THIS AUTOMATICALLY.</div>
      ) : (
        <div className="grid">
          {sorted.map((p) => {
            const isUp = num(p.probability_up, 0.5) >= 0.5;
            return (
              <div
                key={`${p.market}:${p.ticker}`}
                className={`panel ${signalClass(p.signal)}`}
                onClick={() =>
                  openDrawer({ type: "stock", v: { market: p.market, ticker: p.ticker, verdict: p.signal, confidence: num(p.confidence), combined_score: num(p.predicted_return), reason: ["LSTM RESULT"] } })
                }
              >
                <div className="panel-head">
                  <div><div className="symbol">{p.ticker}</div><div className="name">{p.market}</div></div>
                  <span className={`badge ${signalClass(p.signal)}`}>{p.signal}</span>
                </div>
                <div className="row"><span className="label">PRED RET</span><span className="value" style={{ color: isUp ? "var(--bull)" : "var(--bear)" }}>{num(p.predicted_return) > 0 ? "+" : ""}{(num(p.predicted_return) * 100).toFixed(2)}%</span></div>
                <div className="row"><span className="label">P(UP)</span><span className="value">{(num(p.probability_up, 0.5) * 100).toFixed(1)}%</span></div>
                <div className="conf-bar"><span style={{ width: Math.round(num(p.probability_up, 0.5) * 100) + "%", background: isUp ? "var(--bull)" : "var(--bear)" }} /></div>
                <div className="row"><span className="label">CONFIDENCE</span><span className="value">{(num(p.confidence) * 100).toFixed(0)}%</span></div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}