import { useEffect, useRef, useState } from "react";
import { simulate } from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";

const TIMEFRAMES = ["5m", "15m", "30m", "1h", "1d"];
const SPEEDS = [1, 2, 5, 10];

const SIG_LABELS = {
  quant: "QUANTITATIVE",
  technical: "TECHNICAL",
  news: "NEWS",
  social: "SOCIAL",
  regime: "MARKET REGIME",
};

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function pct(v) {
  return `${v > 0 ? "+" : ""}${num(v).toFixed(2)}%`;
}

function Sparkline({ points }) {
  const vals = (points || []).map((p) => p.equity);
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

function actionColor(action) {
  if (action === "BUY" || action === "INCREASE") return "var(--bull)";
  if (action === "SHORT" || action === "REDUCE" || action === "SELL" || action === "COVER") return "var(--bear)";
  if (action === "REVERSE") return "var(--accent, #8b5cf6)";
  return "inherit";
}

export default function SimulationTab() {
  const { markets } = useApp();
  const [market, setMarket] = useState("NYSE");
  const [ticker, setTicker] = useState("AAPL");
  const [startDate, setStartDate] = useState("2025-01-02");
  const [endDate, setEndDate] = useState("2025-06-30");
  const [timeframe, setTimeframe] = useState("15m");
  const [interval, setInterval] = useState("15m");
  const [capital, setCapital] = useState(100000);
  const [bullTh, setBullTh] = useState(70);
  const [bearTh, setBearTh] = useState(70);
  const [sizeRatio, setSizeRatio] = useState(0.25);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [selected, setSelected] = useState(null);
  const timer = useRef(null);

  const decisions = result?.decisions_log || [];
  const revealed = playing || cursor < decisions.length ? decisions.slice(0, cursor) : decisions;

  const run = () => {
    setPlaying(false);
    setRunning(true);
    setError("");
    setResult(null);
    setSelected(null);
    setCursor(0);
    simulate({
      market, ticker, start_date: startDate, end_date: endDate,
      timeframe, decision_interval: interval, capital,
      bull_threshold: bullTh, bear_threshold: bearTh, size_ratio: sizeRatio,
    })
      .then((r) => { setResult(r); setCursor(0); })
      .catch((e) => setError(e.message))
      .finally(() => setRunning(false));
  };

  // Playback timer — presentation only; calculations already happened chronologically.
  useEffect(() => {
    if (!playing) return undefined;
    timer.current = setInterval(() => {
      setCursor((c) => {
        if (c >= decisions.length) { setPlaying(false); return c; }
        return c + 1;
      });
    }, 1000 / speed);
    return () => clearInterval(timer.current);
  }, [playing, speed, decisions.length]);

  const current = cursor > 0 ? decisions[cursor - 1] : null;
  const done = result && cursor >= decisions.length && decisions.length > 0;

  return (
    <div>
      <div className="controls">
        <div className="field"><label>Market</label>
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            {(markets || []).map((m) => <option key={m.code} value={m.code}>{m.code}</option>)}
          </select>
        </div>
        <div className="field"><label>Ticker</label><input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} /></div>
        <div className="field"><label>Start date</label><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></div>
        <div className="field"><label>End date</label><input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></div>
        <div className="field"><label>Timeframe</label>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
          </select>
        </div>
        <div className="field"><label>Decision every</label>
          <select value={interval} onChange={(e) => setInterval(e.target.value)}>
            {TIMEFRAMES.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
          </select>
        </div>
        <div className="field"><label>Capital</label><input type="number" value={capital} onChange={(e) => setCapital(Number(e.target.value))} /></div>
        <div className="field"><label>Bull th.</label><input type="number" value={bullTh} onChange={(e) => setBullTh(Number(e.target.value))} /></div>
        <div className="field"><label>Bear th.</label><input type="number" value={bearTh} onChange={(e) => setBearTh(Number(e.target.value))} /></div>
        <div className="field"><label>Size ratio</label><input type="number" step="0.05" value={sizeRatio} onChange={(e) => setSizeRatio(Number(e.target.value))} /></div>
        <button className="primary" disabled={running} onClick={run}>{running ? "RUNNING…" : "▶ START HISTORICAL REPLAY"}</button>
      </div>

      {error && <div className="scan-warning">⚠ {error}</div>}
      {running && <div className="empty">LOADING HISTORICAL DATASET… REPLAY WILL BE CALCULATED CHRONOLOGICALLY.</div>}

      {result && (result.status === "error" || result.status === "no_data" || result.status === "partial") && (
        <div className="empty">
          {result.status === "error" ? "INVALID — " : result.status === "no_data" ? "NO_DATA — " : "PARTIAL — "}
          {result.reason || "insufficient historical data"}.
          {result.data_source?.attempted_providers?.length > 0 && (
            <span> Attempted: {result.data_source.attempted_providers.join(" → ")}.</span>
          )}
        </div>
      )}

      {result && result.status === "ok" && (
        <>
          <div className="paper-committee" style={{ margin: "10px 0" }}>
            <span className="badge neutral">HISTORICAL REPLAY</span>
            <SecurityLink securityId={result.security_id}>{result.security_id}</SecurityLink>
            <span>{result.start_date} → {result.end_date}</span>
            <span>TIMEFRAME {result.timeframe.toUpperCase()} · DECIDE EVERY {result.decision_interval.toUpperCase()}</span>
            <span>ISOLATED — DOES NOT TOUCH YOUR PAPER PORTFOLIO</span>
          </div>

          {result.data_source && (
            <div className="paper-committee" style={{ margin: "0 0 10px" }}>
              <span className="dim">DATA SOURCE</span>
              <span>{(result.data_source.provider || "?").toUpperCase()}</span>
              <span>ROWS {result.data_source.rows?.length ?? 0}</span>
              <span className="dim">REQUESTED {result.data_source.requested_start} → {result.data_source.requested_end}</span>
              {result.data_source.fallback_used && (
                <span className="badge neutral">FALLBACK: {((result.data_source.attempted_providers || []).filter((p) => p !== result.data_source.provider) || []).join(" → ")} UNAVAILABLE</span>
              )}
            </div>
          )}

          {/* ---------- Realtime-like panel ---------- */}
          <div className="replay-panel">
            <div className="replay-panel-head">
              <span className="badge neutral">{playing ? "▶ PLAYING" : done ? "■ COMPLETED" : "HISTORICAL CLOCK"}</span>
              <span className="dim">{done ? "END OF REPLAY" : "PLAYBACK IS PRESENTATION ONLY — DECISIONS WERE CALCULATED CHRONOLOGICALLY."}</span>
            </div>
            <div className="replay-clock">
              <span className="dim">HISTORICAL TIMESTAMP</span>
              <span className="big">{current ? current.ts.replace("T", "  ") : (result.start_date + "  —")}</span>
            </div>
            {current && (
              <div className="landing-stats replay-stats">
                <div className="landing-stat"><div className="k">PORTFOLIO VALUE</div><div className="v">{num(current.portfolio_after.equity).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div></div>
                <div className="landing-stat"><div className="k">CASH</div><div className="v">{num(current.portfolio_after.cash).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div></div>
                <div className="landing-stat"><div className="k">POSITION</div><div className="v">{current.portfolio_after.position_direction ? `${current.portfolio_after.position_direction} ${num(current.portfolio_after.position_qty)}` : "FLAT"}</div></div>
                <div className="landing-stat"><div className="k">EXPOSURE</div><div className="v">{current.portfolio_after.exposure_pct != null ? current.portfolio_after.exposure_pct.toFixed(1) + "%" : "—"}</div></div>
                <div className="landing-stat"><div className="k">REALIZED P&L</div><div className="v" style={{ color: num(current.portfolio_after.realized_pnl) >= 0 ? "var(--bull)" : "var(--bear)" }}>{num(current.portfolio_after.realized_pnl).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div></div>
                <div className="landing-stat"><div className="k">COMMITTEE</div><div className="v" style={{ color: current.verdict === "BULL" ? "var(--bull)" : current.verdict === "BEAR" ? "var(--bear)" : "inherit" }}>{current.verdict} · {current.conviction}</div></div>
                <div className="landing-stat"><div className="k">ACTION</div><div className="v" style={{ color: actionColor(current.action) }}>{current.action}</div></div>
              </div>
            )}
          </div>

          {/* ---------- Playback controls ---------- */}
          <div className="replay-controls">
            <button className="ghost" disabled={cursor === 0} onClick={() => { setPlaying(false); setSelected(null); setCursor(0); }}>⏮</button>
            <button className="ghost" disabled={cursor === 0} onClick={() => { setPlaying(false); setCursor((c) => Math.max(0, c - 1)); }}>◀ STEP</button>
            <button className="primary" onClick={() => setPlaying((p) => !p)}>{playing ? "⏸ PAUSE" : "▶ PLAY"}</button>
            <button className="ghost" disabled={cursor >= decisions.length} onClick={() => { setPlaying(false); setCursor((c) => Math.min(decisions.length, c + 1)); }}>STEP ▶</button>
            <button className="ghost" disabled={cursor >= decisions.length} onClick={() => { setPlaying(false); setCursor(decisions.length); }}>⏭ END</button>
            <span className="dim">SPEED</span>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEEDS.map((s) => <option key={s} value={s}>{s}x</option>)}
            </select>
            <span className="dim progress">REVEALED {cursor} / {decisions.length}</span>
          </div>

          {/* ---------- Equity curve ---------- */}
          <div className="landing-h" style={{ marginTop: 14 }}>EQUITY CURVE</div>
          <div className="paper-equity"><Sparkline points={result.equity_curve} /></div>

          {/* ---------- Timeline ---------- */}
          <div className="landing-h" style={{ marginTop: 14 }}>DECISION TIMELINE — {revealed.length} EVENTS</div>
          <div className="replay-timeline">
            {revealed.length === 0 && <div className="empty" style={{ padding: 12 }}>PRESS PLAY OR STEP TO REVEAL DECISIONS IN CHRONOLOGICAL ORDER.</div>}
            {revealed.map((d, i) => (
              <div
                key={d.decision_id}
                className={`replay-event ${selected === i ? "active" : ""} ${d.action}`}
                onClick={() => { setSelected(selected === i ? null : i); setPlaying(false); }}
              >
                <span className="replay-time">{String(d.ts).slice(0, 16)}</span>
                <span className="replay-action" style={{ color: actionColor(d.action) }}>{d.action}</span>
                <span className="replay-verdict" style={{ color: d.verdict === "BULL" ? "var(--bull)" : d.verdict === "BEAR" ? "var(--bear)" : "inherit" }}>{d.verdict}</span>
                <span className="dim">{d.conviction}</span>
              </div>
            ))}
          </div>

          {/* ---------- Decision detail ---------- */}
          {selected != null && revealed[selected] && (
            <div className="paper-trade-detail replay-detail">
              {(() => {
                const d = revealed[selected];
                const qty = num(d.quantity);
                return (
                  <>
                    <h3 className="dossier-company"><SecurityLink securityId={result.security_id}>{result.security_id}</SecurityLink> · {String(d.ts).replace("T", "  ")}</h3>
                    <div className="row"><span className="label">ACTION</span><span className="value" style={{ color: actionColor(d.action) }}>{d.action}{d.tag ? ` — ${d.tag}` : ""}</span></div>
                    <div className="row"><span className="label">COMMITTEE</span><span className="value">{d.verdict} · {d.conviction} conviction</span></div>
                    <div className="row"><span className="label">REFERENCE PRICE</span><span className="value">{num(d.reference_price).toFixed(4)}</span></div>
                    <div className="row"><span className="label">EXECUTION PRICE</span><span className="value">{num(d.execution_price).toFixed(4)} {d.orders?.length ? `(${d.orders.length} order${d.orders.length > 1 ? "s" : ""})` : "—"}</span></div>
                    {qty > 0 && <div className="row"><span className="label">QUANTITY</span><span className="value">{qty}</span></div>}
                    <div className="row"><span className="label">PORTFOLIO VALUE</span><span className="value">{num(d.portfolio_after.equity).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span></div>
                    <div className="row"><span className="label">SIGNAL ALIGNMENT</span><span className="value">{d.signal_alignment} available signals with committee</span></div>

                    <h4 className="landing-h" style={{ fontSize: 12, margin: "10px 0 4px" }}>SIGNAL BREAKDOWN</h4>
                    {["quant", "technical", "news", "social", "regime"].map((k) => (
                      <div className="row" key={k}>
                        <span className="label">{SIG_LABELS[k]}</span>
                        <span className="value">
                          <span className={d.signal_statuses?.[k] === "AVAILABLE" ? (d.signal_states?.[k] === "BULL" ? "up" : d.signal_states?.[k] === "BEAR" ? "down" : "") : "dim"}>
                            {d.signal_statuses?.[k] === "AVAILABLE" ? d.signal_states?.[k] || "NEUTRAL" : "NO_DATA"}
                          </span>
                          {d.signal_confidences?.[k] != null ? ` · ${(d.signal_confidences[k] * 100).toFixed(0)}` : ""}
                          {d.signal_scores?.[k] != null ? ` · ${d.signal_scores[k] > 0 ? "+" : ""}${d.signal_scores[k].toFixed(3)}` : ""}
                        </span>
                      </div>
                    ))}

                    <h4 className="landing-h" style={{ fontSize: 12, margin: "10px 0 4px" }}>RESEARCH</h4>
                    {d.research && d.research.status === "ok" ? (
                      <>
                        <div className="row"><span className="label">STATUS</span><span className="value">AVAILABLE</span></div>
                        {(d.research.provenance || []).map((p, i) => <div className="row" key={i}><span className="label">SOURCE</span><span className="value">{p}</span></div>)}
                      </>
                    ) : (
                      <div className="empty" style={{ padding: 8 }}>Research unavailable for this timestamp.</div>
                    )}

                    <h4 className="landing-h" style={{ fontSize: 12, margin: "10px 0 4px" }}>RISK</h4>
                    <div className="row"><span className="label">EXPOSURE BEFORE</span><span className="value">{d.portfolio_before.exposure_pct != null ? d.portfolio_before.exposure_pct.toFixed(1) + "%" : "—"}</span></div>
                    <div className="row"><span className="label">EXPOSURE AFTER</span><span className="value">{d.portfolio_after.exposure_pct != null ? d.portfolio_after.exposure_pct.toFixed(1) + "%" : "—"}</span></div>
                    <div className="row"><span className="label">CASH AFTER</span><span className="value">{num(d.portfolio_after.cash).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span></div>
                    {d.orders?.length > 0 && (
                      <div className="row"><span className="label">REALIZED P&L</span><span className="value" style={{ color: num(d.orders[0].pnl) >= 0 ? "var(--bull)" : "var(--bear)" }}>{d.orders[0].pnl != null ? (d.orders[0].pnl > 0 ? "+" : "") + d.orders[0].pnl.toFixed(2) : "—"}</span></div>
                    )}

                    <h4 className="landing-h" style={{ fontSize: 12, margin: "10px 0 4px" }}>DECISION</h4>
                    <div className="replay-reason">{d.reason}</div>

                    <h4 className="landing-h" style={{ fontSize: 12, margin: "10px 0 4px" }}>FORWARD OUTCOMES (AFTER THE DECISION)</h4>
                    <div className="landing-stats">
                      {["p5", "p15", "p30", "p60"].map((h) => (
                        <div className="landing-stat" key={h}><div className="k">+{h.slice(1)}m</div><div className="v" style={{ color: num(d.forward?.[h]) >= 0 ? "var(--bull)" : "var(--bear)" }}>{d.forward?.[h] != null ? pct(d.forward[h] * 100) : "—"}</div></div>
                      ))}
                    </div>
                  </>
                );
              })()}
            </div>
          )}

          {/* ---------- Performance summary ---------- */}
          <div className="landing-h" style={{ marginTop: 16 }}>PERFORMANCE SUMMARY</div>
          <div className="landing-stats">
            <div className="landing-stat"><div className="k">STARTING CAPITAL</div><div className="v">{num(result.starting_capital).toLocaleString()}</div></div>
            <div className="landing-stat"><div className="k">ENDING EQUITY</div><div className="v">{num(result.ending_equity).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div></div>
            <div className="landing-stat"><div className="k">TOTAL RETURN</div><div className="v" style={{ color: result.return_pct >= 0 ? "var(--bull)" : "var(--bear)" }}>{pct(result.return_pct)}</div></div>
            <div className="landing-stat"><div className="k">MAX DRAWDOWN</div><div className="v" style={{ color: result.max_drawdown_pct < 0 ? "var(--bear)" : "var(--bull)" }}>{pct(result.max_drawdown_pct)}</div></div>
            <div className="landing-stat"><div className="k">TRADES</div><div className="v">{result.trades}</div></div>
            <div className="landing-stat"><div className="k">WIN RATE</div><div className="v">{result.win_rate != null ? (result.win_rate * 100).toFixed(1) + "%" : "—"}</div></div>
            <div className="landing-stat"><div className="k">LONG / SHORT P&L</div><div className="v">{num(result.long_pnl) > 0 ? "+" : ""}{num(result.long_pnl).toFixed(0)} / {num(result.short_pnl) > 0 ? "+" : ""}{num(result.short_pnl).toFixed(0)}</div></div>
          </div>

          <div className="landing-h" style={{ marginTop: 14 }}>DECISION QUALITY</div>
          <div className="landing-stats">
            <div className="landing-stat"><div className="k">BULL ACC</div><div className="v">{result.bull_accuracy != null ? (result.bull_accuracy * 100).toFixed(1) + "%" : "—"} <span className="dim">N={result.bull_n}</span></div></div>
            <div className="landing-stat"><div className="k">BEAR ACC</div><div className="v">{result.bear_accuracy != null ? (result.bear_accuracy * 100).toFixed(1) + "%" : "—"} <span className="dim">N={result.bear_n}</span></div></div>
            <div className="landing-stat"><div className="k">NO-TRADE FWD 30m</div><div className="v">{result.no_trade_forward_30m != null ? pct(result.no_trade_forward_30m * 100) : "—"}</div></div>
            <div className="landing-stat"><div className="k">AVG CONVICTION</div><div className="v">{result.avg_conviction != null ? result.avg_conviction.toFixed(1) : "—"}</div></div>
          </div>

          <div className="landing-h" style={{ marginTop: 14 }}>AVERAGE FORWARD RETURN BY DECISION</div>
          <div className="landing-stats">
            {["p5", "p15", "p30", "p60"].map((h) => (
              <div className="landing-stat" key={h}><div className="k">+{h.slice(1)}m</div><div className="v" style={{ color: num(result[`forward_${h.slice(1)}m`]) >= 0 ? "var(--bull)" : "var(--bear)" }}>{result[`forward_${h.slice(1)}m`] != null ? pct(result[`forward_${h.slice(1)}m`] * 100) : "—"}</div></div>
            ))}
            <div className="landing-stat"><div className="k">DECISIONS</div><div className="v">{result.decisions}</div></div>
          </div>

          <div className="team-note" style={{ marginTop: 10 }}>
            HISTORICAL REPLAY — DECISIONS USE ONLY INFORMATION AVAILABLE AT EACH HISTORICAL TIMESTAMP.
            NO FUTURE DATA IS USED. LSTM IS MARKED NO_DATA DURING REPLAY (TRAINED ON FUTURE DATA).
            PRESENTATION ONLY. NO REAL ORDERS.
          </div>
        </>
      )}
    </div>
  );
}
