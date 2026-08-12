import { useEffect, useState } from "react";
import { simulate } from "../api.js";
import { useApp } from "../App.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function pct(v) {
  return `${v > 0 ? "+" : ""}${num(v).toFixed(2)}%`;
}

function Sparkline({ points }) {
  const vals = points.map((p) => p.equity);
  if (vals.length < 2) return <div className="empty" style={{ padding: 16 }}>NO EQUITY POINTS.</div>;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const w = 600;
  const h = 90;
  const coords = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = vals[vals.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 90 }}>
      <polyline points={coords} fill="none" stroke={last >= vals[0] ? "var(--bull)" : "var(--bear)"} strokeWidth="1.5" />
    </svg>
  );
}

export default function SimulationTab() {
  const { markets } = useApp();
  const [market, setMarket] = useState("NYSE");
  const [ticker, setTicker] = useState("AAPL");
  const [period, setPeriod] = useState("quick");
  const [capital, setCapital] = useState(100000);
  const [threshold, setThreshold] = useState(70);
  const [mode, setMode] = useState("sim");
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);

  const run = (m) => {
    setMode(m);
    setRunning(true);
    setError("");
    setResult(null);
    setSelected(null);
    simulate({ mode: m, market, ticker, period, capital, bull_threshold: threshold, bear_threshold: threshold })
      .then(setResult)
      .catch((e) => setError(e.message))
      .finally(() => setRunning(false));
  };

  const decisionByTime = {};
  (result?.decisions_log || []).forEach((d) => { decisionByTime[d.time] = d; });

  return (
    <div>
      <div className="controls">
        <div className="field"><label>Market</label>
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            {(markets || []).map((m) => <option key={m.code} value={m.code}>{m.code}</option>)}
          </select>
        </div>
        <div className="field"><label>Ticker</label><input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} /></div>
        <div className="field"><label>Period</label>
          <select value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option value="quick">QUICK · 1 trading day</option>
            <option value="day">DAY · 1 session</option>
            <option value="week">WEEK · 5 sessions</option>
            <option value="custom">CUSTOM · 1 month</option>
          </select>
        </div>
        <div className="field"><label>Capital</label><input type="number" value={capital} onChange={(e) => setCapital(Number(e.target.value))} /></div>
        <div className="field"><label>Threshold</label><input type="number" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /></div>
        <button className="primary" disabled={running} onClick={() => run("sim")}>{running && mode === "sim" ? "RUNNING…" : "RUN FAST SIMULATION"}</button>
        <button className="ghost" disabled={running} onClick={() => run("backtest")}>{running && mode === "backtest" ? "RUNNING…" : "RUN BACKTEST"}</button>
      </div>

      {error && <div className="scan-warning">⚠ {error}</div>}
      {running && <div className="empty">PROCESSING HISTORICAL TIMESTAMPS…</div>}

      {result && (result.status === "no_data" || result.status === "partial") && (
        <div className="empty">
          {result.status === "no_data" ? "NO_DATA — " : "PARTIAL — "}
          insufficient historical data for {market}:{ticker} ({result.reason || ""}).
          {result.data_source && result.data_source.attempted_providers?.length > 0 && (
            <span> Attempted: {result.data_source.attempted_providers.join(" → ")}.</span>
          )}
        </div>
      )}

      {result && result.status === "ok" && (
        <>
          <div className="paper-committee" style={{ margin: "10px 0" }}>
            <span className="badge neutral">{mode === "backtest" ? "BACKTEST · HISTORICAL EVALUATION" : "FAST SIMULATION · DEMONSTRATION"}</span>
            <span>{market}:{ticker} · {period.toUpperCase()}</span>
            <span>ISOLATED — DOES NOT TOUCH YOUR PAPER PORTFOLIO</span>
          </div>

          {result.data_source && (
            <div className="paper-committee" style={{ margin: "0 0 10px" }}>
              <span className="dim">DATA SOURCE</span>
              <span>{(result.data_source.provider || "?").toUpperCase()}</span>
              <span>ROWS {result.data_source.rows?.length ?? 0} · {result.data_source.timeframe?.toUpperCase() || "—"}</span>
              {result.data_source.fallback_used && (
                <span className="badge neutral">FALLBACK: {((result.data_source.attempted_providers || []).filter(p => p !== result.data_source.provider) || []).join(" → ")} UNAVAILABLE</span>
              )}
            </div>
          )}

          <div className="landing-stats">
            <div className="landing-stat"><div className="k">STARTING CAPITAL</div><div className="v">{result.starting_capital.toLocaleString()}</div></div>
            <div className="landing-stat"><div className="k">ENDING EQUITY</div><div className="v">{num(result.ending_equity).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div></div>
            <div className="landing-stat"><div className="k">RETURN</div><div className="v" style={{ color: result.return_pct >= 0 ? "var(--bull)" : "var(--bear)" }}>{pct(result.return_pct)}</div></div>
            <div className="landing-stat"><div className="k">TRADES</div><div className="v">{result.trades}</div></div>
            <div className="landing-stat"><div className="k">WIN RATE</div><div className="v">{result.win_rate != null ? (result.win_rate * 100).toFixed(1) + "%" : "—"}</div></div>
            <div className="landing-stat"><div className="k">MAX DRAWDOWN</div><div className="v" style={{ color: result.max_drawdown_pct < 0 ? "var(--bear)" : "var(--bull)" }}>{pct(result.max_drawdown_pct)}</div></div>
            <div className="landing-stat"><div className="k">LONG / SHORT P&L</div><div className="v">{num(result.long_pnl) > 0 ? "+" : ""}{num(result.long_pnl).toFixed(0)} / {num(result.short_pnl) > 0 ? "+" : ""}{num(result.short_pnl).toFixed(0)}</div></div>
          </div>

          <div className="landing-h" style={{ marginTop: 14 }}>EQUITY CURVE</div>
          <div className="paper-equity"><Sparkline points={result.equity_curve} /></div>

          <div className="landing-h" style={{ marginTop: 14 }}>TRADE LOG</div>
          {!result.trades_log?.length ? (
            <div className="empty">NO TRADES — SIGNAL STRENGTH BELOW THRESHOLD IN THIS PERIOD.</div>
          ) : (
            <div className="paper-table">
              <div className="paper-row paper-row-head"><span>TIME</span><span>ACTION</span><span>SIDE</span><span>QTY</span><span>PRICE</span><span>COMMITTEE</span><span>P&L</span></div>
              {result.trades_log.map((t, i) => (
                <div className="paper-row sim-row" key={i} onClick={() => setSelected(selected === i ? null : i)}>
                  <span>{String(t.time).slice(11, 19)}</span>
                  <span className={t.side === "LONG" ? "up" : "down"}>{t.action}</span>
                  <span>{t.side}</span>
                  <span>{t.qty}</span>
                  <span>{num(t.price).toFixed(4)}</span>
                  <span>{t.committee}</span>
                  <span style={{ color: num(t.pnl) >= 0 ? "var(--bull)" : "var(--bear)" }}>{t.pnl != null ? (t.pnl > 0 ? "+" : "") + t.pnl.toFixed(2) : "—"}</span>
                </div>
              ))}
            </div>
          )}
          {selected != null && result.trades_log[selected] && (
            <div className="paper-trade-detail">
              <h3 className="dossier-company">DECISION AT {String(result.trades_log[selected].time).slice(11, 19)}</h3>
              {(() => {
                const d = decisionByTime[result.trades_log[selected].time];
                if (!d) return <div className="empty" style={{ padding: 12 }}>NO DECISION SNAPSHOT FOR THIS BAR.</div>;
                return (
                  <>
                    <div className="row"><span className="label">COMMITTEE</span><span className="value">{d.verdict} · {d.conviction} conviction</span></div>
                    {(d.signals || []).map((s) => (
                      <div className="row" key={s.key}><span className="label">{s.label}</span><span className="value">{s.state} · {s.score != null ? (s.score > 0 ? "+" : "") + s.score.toFixed(2) : "—"}</span></div>
                    ))}
                  </>
                );
              })()}
            </div>
          )}

          <div className="landing-h" style={{ marginTop: 14 }}>DECISION SUMMARY</div>
          <div className="landing-stats">
            <div className="landing-stat"><div className="k">BULL</div><div className="v" style={{ color: "var(--bull)" }}>{result.bull_decisions}</div></div>
            <div className="landing-stat"><div className="k">BEAR</div><div className="v" style={{ color: "var(--bear)" }}>{result.bear_decisions}</div></div>
            <div className="landing-stat"><div className="k">NEUTRAL</div><div className="v">{result.neutral_decisions}</div></div>
            <div className="landing-stat"><div className="k">AVG CONVICTION</div><div className="v">{result.avg_conviction != null ? result.avg_conviction.toFixed(1) : "—"}</div></div>
          </div>

          {mode === "backtest" && result.metrics && (
            <>
              <div className="landing-h" style={{ marginTop: 14 }}>BACKTEST METRICS</div>
              <div className="landing-stats">
                <div className="landing-stat"><div className="k">BULL ACC</div><div className="v">{result.metrics.bull_accuracy != null ? (result.metrics.bull_accuracy * 100).toFixed(1) + "%" : "—"} <span className="dim">N={result.metrics.bull_n}</span></div></div>
                <div className="landing-stat"><div className="k">BEAR ACC</div><div className="v">{result.metrics.bear_accuracy != null ? (result.metrics.bear_accuracy * 100).toFixed(1) + "%" : "—"} <span className="dim">N={result.metrics.bear_n}</span></div></div>
                <div className="landing-stat"><div className="k">FWD 15m</div><div className="v">{result.metrics.forward_15m != null ? pct(result.metrics.forward_15m * 100) : "—"}</div></div>
                <div className="landing-stat"><div className="k">FWD 30m</div><div className="v">{result.metrics.forward_30m != null ? pct(result.metrics.forward_30m * 100) : "—"}</div></div>
                <div className="landing-stat"><div className="k">FWD 60m</div><div className="v">{result.metrics.forward_60m != null ? pct(result.metrics.forward_60m * 100) : "—"}</div></div>
              </div>
              <h3 className="dossier-company">AVG 30M RETURN BY CONVICTION</h3>
              {(result.metrics.conviction_buckets || []).map((b) => (
                <div className="row" key={b.bucket}><span className="label">{b.bucket}%</span><span className="value">{(b.avg_30m != null ? (b.avg_30m * 100).toFixed(3) : "—") + "% · N=" + b.n}</span></div>
              ))}
            </>
          )}

          <div className="team-note" style={{ marginTop: 10 }}>
            {mode === "backtest" ? "BACKTEST · HISTORICAL EVALUATION" : "SIMULATION · DEMONSTRATION"} — NOT STATISTICALLY VALIDATED.
            DECISIONS USE ONLY INFORMATION AVAILABLE AT EACH TIMESTAMP. NO REAL ORDERS.
          </div>
        </>
      )}
    </div>
  );
}