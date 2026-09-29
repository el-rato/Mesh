import { useEffect, useMemo, useRef, useState } from "react";
import { replay, replaySeek } from "../api.js";
import { useApp } from "../App.jsx";
import NumberInput from "./NumberInput.jsx";

const TIMEFRAMES = ["5m", "15m", "30m", "1h", "1d"];
const SPEEDS = [0.5, 1, 2, 5, 10, 25, 50];
const FILTERS = ["ALL", "MARKET", "TRADES", "SIGNALS", "ORDERS", "NEWS"];

const num = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const pct = (value) => `${num(value) >= 0 ? "+" : ""}${num(value).toFixed(2)}%`;
const eventTime = (event) => event?.eventTimestamp || event?.ts || "";
const shortTime = (value) => value ? String(value).replace("T", " ").replace("Z", "").slice(0, 19) : "--";

function directionClass(value) {
  return value > 0 ? "up" : value < 0 ? "down" : "dim";
}

function ReplayChart({ candles, cursor, trades, onHover }) {
  const [hover, setHover] = useState(null);
  const [zoom, setZoom] = useState(1);
  const visible = candles.slice(Math.max(0, candles.length - Math.round(72 * zoom)));
  if (!visible.length) return <div className="replay-chart-empty">STEP FORWARD TO RELEASE THE FIRST MARKET EVENT.</div>;
  const width = 960;
  const height = 360;
  const top = 18;
  const chartBottom = 270;
  const volumeTop = 286;
  const volumeBottom = 344;
  const lows = visible.map((c) => num(c.payload.low));
  const highs = visible.map((c) => num(c.payload.high));
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const range = max - min || 1;
  const x = (index) => visible.length === 1 ? width / 2 : 12 + index * ((width - 24) / visible.length);
  const y = (price) => chartBottom - ((price - min) / range) * (chartBottom - top);
  const maxVolume = Math.max(...visible.map((c) => num(c.payload.volume)), 1);
  const cursorIndex = visible.findIndex((c) => eventTime(c).replace("Z", "") >= cursor.replace("Z", ""));
  const cursorX = cursorIndex >= 0 ? x(cursorIndex) : cursor ? width - 12 : null;
  const onMove = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const index = Math.max(0, Math.min(visible.length - 1, Math.round(((event.clientX - rect.left) / rect.width) * visible.length)));
    const point = visible[index];
    setHover({ index, event: point });
    onHover?.(point);
  };
  return (
    <div className="replay-chart-wrap">
      <div className="replay-chart-tools">
        <span className="dim">CANDLES {visible.length} · FUTURE DATA HIDDEN</span>
        <button type="button" onClick={() => setZoom((value) => Math.max(0.5, value - 0.5))}>−</button>
        <button type="button" onClick={() => setZoom((value) => Math.min(3, value + 0.5))}>+</button>
      </div>
      <svg className="replay-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Replay candlestick chart" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {[0, 1, 2, 3].map((step) => {
          const gy = top + step * ((chartBottom - top) / 3);
          const value = max - step * range / 3;
          return <g key={step}><line x1="0" x2={width} y1={gy} y2={gy} className="replay-grid" /><text x={width - 4} y={gy - 4} textAnchor="end" className="replay-axis">{value.toFixed(2)}</text></g>;
        })}
        {visible.map((c, index) => {
          const open = num(c.payload.open); const close = num(c.payload.close);
          const rising = close >= open; const cx = x(index); const bodyTop = y(Math.max(open, close));
          const bodyHeight = Math.max(2, Math.abs(y(open) - y(close)));
          const volumeHeight = (num(c.payload.volume) / maxVolume) * (volumeBottom - volumeTop);
          return <g key={c.eventId} className={rising ? "candle up" : "candle down"}>
            <line x1={cx} x2={cx} y1={y(num(c.payload.high))} y2={y(num(c.payload.low))} />
            <rect x={cx - 3.5} y={bodyTop} width="7" height={bodyHeight} />
            <rect className="candle-volume" x={cx - 3.5} y={volumeBottom - volumeHeight} width="7" height={volumeHeight} />
          </g>;
        })}
        {trades.map((trade) => {
          const index = visible.findIndex((c) => eventTime(c) >= trade.ts);
          if (index < 0) return null;
          return <path key={trade.decision_id} className="trade-marker" d={`M ${x(index)} ${trade.action === "BUY" || trade.action === "COVER" ? 12 : 250} l -5 ${trade.action === "BUY" || trade.action === "COVER" ? 8 : -8} h 10 z`} />;
        })}
        {cursorX != null && <line className="replay-cursor" x1={cursorX} x2={cursorX} y1="0" y2={height} />}
        {hover && <line className="replay-crosshair" x1={x(hover.index)} x2={x(hover.index)} y1="0" y2={height} />}
      </svg>
      <div className="replay-chart-footer"><span>{shortTime(visible[0].eventTimestamp)}</span><span>{hover ? `${shortTime(hover.event.eventTimestamp)} · ${num(hover.event.payload.close).toFixed(2)}` : "CROSSHAIR READY"}</span><span>{shortTime(visible[visible.length - 1].eventTimestamp)}</span></div>
    </div>
  );
}

function Metric({ label, value, tone = "" }) {
  return <div className="replay-metric"><span>{label}</span><strong className={tone}>{value}</strong></div>;
}

export default function ReplayTab() {
  const { markets } = useApp();
  const [market, setMarket] = useState("NYSE");
  const [ticker, setTicker] = useState("AAPL");
  const [startDate, setStartDate] = useState("2025-01-02");
  const [endDate, setEndDate] = useState("2025-06-30");
  const [timeframe, setTimeframe] = useState("1d");
  const [interval, setInterval] = useState("1d");
  const [capital, setCapital] = useState(100000);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState(null);
  const [hoveredCandle, setHoveredCandle] = useState(null);
  const [error, setError] = useState("");
  const [seekState, setSeekState] = useState(null);
  const timer = useRef(null);
  const decisions = result?.decisions_log || [];
  const candles = result?.replay_events || [];
  const current = cursor > 0 ? candles[cursor - 1] : null;
  const currentTimestamp = current?.eventTimestamp || "";
  const releasedCandles = candles.slice(0, cursor);
  const releasedDecisions = useMemo(
    () => decisions.filter((decision) => !currentTimestamp || decision.ts.replace("Z", "") <= currentTimestamp.replace("Z", "")),
    [decisions, currentTimestamp],
  );
  const latestDecision = releasedDecisions[releasedDecisions.length - 1];
  const stream = useMemo(() => {
    const marketEvents = releasedCandles.slice(-48).map((event) => ({ id: event.eventId, ts: eventTime(event), kind: "CANDLE", detail: `${num(event.payload.close).toFixed(2)} · VOL ${num(event.payload.volume).toLocaleString()}`, group: "MARKET" }));
    const strategyEvents = releasedDecisions.flatMap((decision) => {
      const items = [{ id: `${decision.decision_id}-signal`, ts: decision.ts, kind: "SIGNAL", detail: `${decision.verdict} · ${decision.conviction}`, group: "SIGNALS" }];
      if (decision.orders?.length) items.push({ id: `${decision.decision_id}-order`, ts: decision.ts, kind: "ORDER", detail: `${decision.action} ${num(decision.quantity)}`, group: "ORDERS" });
      return items;
    });
    return [...marketEvents, ...strategyEvents].sort((a, b) => a.ts.localeCompare(b.ts)).filter((item) => filter === "ALL" || item.group === filter);
  }, [releasedCandles, releasedDecisions, filter]);

  useEffect(() => {
    if (!playing) return undefined;
    timer.current = setInterval(() => setCursor((value) => {
      if (value >= candles.length) { setPlaying(false); return value; }
      return value + 1;
    }), Math.max(20, 700 / speed));
    return () => clearInterval(timer.current);
  }, [playing, speed, candles.length]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.target.matches("input, select, textarea")) return;
      if (event.key === " ") { event.preventDefault(); setPlaying((value) => !value); }
      if (event.key === "ArrowRight") { event.preventDefault(); seekTo(Math.min(candles.length, cursor + (event.shiftKey ? 5 : 1))); }
      if (event.key === "ArrowLeft") { event.preventDefault(); seekTo(Math.max(0, cursor - (event.shiftKey ? 5 : 1))); }
      if (event.key.toLowerCase() === "r") { setPlaying(false); setCursor(0); setSelected(null); }
      if ([1, 2, 5].includes(Number(event.key))) setSpeed(Number(event.key));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [candles.length, cursor]);

  const run = () => {
    setRunning(true); setPlaying(false); setCursor(0); setSelected(null); setSeekState(null); setError("");
    replay({ market, ticker, start_date: startDate, end_date: endDate, timeframe, decision_interval: interval, capital })
      .then(setResult).catch((reason) => setError(reason.message)).finally(() => setRunning(false));
  };
  const reset = () => { setPlaying(false); setCursor(0); setSelected(null); };
  const seekTo = (value) => {
    const next = Number(value);
    setPlaying(false);
    setCursor(next);
    const timestamp = candles[Math.max(0, next - 1)]?.eventTimestamp;
    if (!timestamp) { setSeekState(null); return; }
    replaySeek({ market, ticker, start_date: startDate, end_date: endDate, timeframe, decision_interval: interval, capital, timestamp })
      .then(setSeekState).catch(() => setSeekState(null));
  };
  const selectedDecision = selected ? decisions.find((item) => item.decision_id === selected) : null;

  return <div className="replay-workspace">
    <div className="replay-toolbar">
      <div className="replay-toolbar-title"><span className="eyebrow">MARKET REPLAY</span><strong>{ticker}</strong><span className="dim">{market} · {timeframe.toUpperCase()}</span></div>
      <label>SYMBOL<select value={market} onChange={(event) => setMarket(event.target.value)}>{(markets || []).map((item) => <option key={item.code} value={item.code}>{item.code}</option>)}</select></label>
      <label>TICKER<input value={ticker} onChange={(event) => setTicker(event.target.value.toUpperCase())} /></label>
      <label>SESSION<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
      <label>TO<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
      <label>TIMEFRAME<select value={timeframe} onChange={(event) => { const value = event.target.value; setTimeframe(value); setInterval(value); }}>{TIMEFRAMES.map((item) => <option key={item}>{item}</option>)}</select></label>
      <div className="replay-number-field"><label htmlFor="replay-capital">CAPITAL</label><NumberInput id="replay-capital" stepperLabel="replay capital" stepperStep={1000} value={capital} onChange={(event) => setCapital(Number(event.target.value))} /></div>
      <button className="primary replay-run" disabled={running} onClick={run}>{running ? "LOADING" : "LOAD DATA"}</button>
    </div>
    {error && <div className="replay-error">DATA_UNAVAILABLE · {error}</div>}
    {running && <div className="replay-empty-state">NORMALIZING HISTORICAL EVENTS · BUILDING CAUSAL REPLAY DATASET</div>}
    {!result && !running && <div className="replay-empty-state"><strong>READY TO REPLAY</strong><span>Load a historical session to release market events one timestamp at a time.</span></div>}
    {result?.status !== "ok" && result && <div className="replay-empty-state"><strong>{result.status === "no_data" ? "DATA_UNAVAILABLE" : "REPLAY_INCOMPLETE"}</strong><span>{result.reason || "The selected session cannot be replayed."}</span></div>}
    {result?.status === "ok" && <>
      <div className="replay-session-line"><span className="status-dot" /> <strong>{playing ? "RUNNING" : cursor >= candles.length ? "COMPLETED" : cursor ? "PAUSED" : "READY"}</strong><span>{result.security_id}</span><span>{result.start_date} → {result.end_date}</span><span className="dim">{result.data_source?.provider || "NORMALIZED"} · {result.replay_events.length} EVENTS</span>{seekState && <span className="dim">STATE REBUILT · {seekState.cursor}</span>}</div>
      <div className="replay-grid">
        <section className="replay-panel replay-chart-panel"><div className="panel-heading"><span>PRICE / VOLUME</span><span className="dim">{current ? shortTime(currentTimestamp) : "REPLAY CLOCK STANDBY"}</span></div><ReplayChart candles={releasedCandles} cursor={currentTimestamp} trades={releasedDecisions.filter((item) => item.orders?.length)} onHover={setHoveredCandle} /><div className="replay-readout">{hoveredCandle ? <><span>{shortTime(hoveredCandle.eventTimestamp)}</span><strong>{num(hoveredCandle.payload.close).toFixed(2)}</strong><span>O {num(hoveredCandle.payload.open).toFixed(2)} · H {num(hoveredCandle.payload.high).toFixed(2)} · L {num(hoveredCandle.payload.low).toFixed(2)}</span></> : <span>HOVER A CANDLE FOR OHLCV</span>}</div></section>
        <section className="replay-panel replay-stream-panel"><div className="panel-heading"><span>EVENT STREAM</span><div className="replay-filter-row">{FILTERS.map((item) => <button key={item} className={filter === item ? "active" : ""} onClick={() => setFilter(item)}>{item}</button>)}</div></div><div className="replay-stream">{stream.length ? stream.map((item) => <button type="button" key={item.id} className="replay-stream-row" onClick={() => setSelected(item.id.replace(/-(signal|order)$/, ""))}><time>{shortTime(item.ts).slice(11)}</time><strong>{item.kind}</strong><span>{item.detail}</span></button>) : <div className="replay-panel-empty">NO RELEASED EVENTS</div>}</div></section>
        <section className="replay-panel replay-trades-panel"><div className="panel-heading"><span>STRATEGY / TRADES</span><span className="dim">{releasedDecisions.length} / {decisions.length}</span></div><div className="replay-ledger">{releasedDecisions.length ? releasedDecisions.map((decision) => <button type="button" key={decision.decision_id} className={selected === decision.decision_id ? "selected" : ""} onClick={() => { setSelected(decision.decision_id); setPlaying(false); }}><time>{shortTime(decision.ts)}</time><strong className={directionClass(decision.action === "BUY" || decision.action === "COVER" ? 1 : ["SELL", "SHORT"].includes(decision.action) ? -1 : 0)}>{decision.action}</strong><span>{decision.verdict} · {decision.conviction}</span><span>{num(decision.execution_price).toFixed(2)}</span></button>) : <div className="replay-panel-empty">PRESS STEP OR PLAY TO RELEASE STRATEGY EVENTS</div>}</div></section>
        <section className="replay-panel replay-inspector"><div className="panel-heading"><span>INSPECTOR</span><span className="dim">{selectedDecision ? "DECISION" : "SESSION"}</span></div>{selectedDecision ? <div className="replay-inspector-body"><div className="inspector-title"><strong>{selectedDecision.action}</strong><span>{shortTime(selectedDecision.ts)}</span></div><Metric label="VERDICT" value={`${selectedDecision.verdict} · ${selectedDecision.conviction}`} /><Metric label="REFERENCE" value={num(selectedDecision.reference_price).toFixed(4)} /><Metric label="EXECUTION" value={num(selectedDecision.execution_price).toFixed(4)} /><Metric label="QUANTITY" value={num(selectedDecision.quantity).toFixed(4)} /><Metric label="ALIGNMENT" value={selectedDecision.signal_alignment} /><div className="inspector-note">{selectedDecision.reason}</div></div> : <div className="replay-inspector-body"><Metric label="CURRENT TIME" value={shortTime(currentTimestamp)} /><Metric label="RELEASED" value={`${seekState?.released_event_ids?.length ?? releasedCandles.length} candles`} /><Metric label="POSITION" value={seekState?.portfolio?.position_direction || latestDecision?.portfolio_after?.position_direction || "FLAT"} /><Metric label="EQUITY" value={num(seekState?.portfolio?.equity ?? latestDecision?.portfolio_after?.equity ?? result.starting_capital).toLocaleString()} /><div className="inspector-note">Select a strategy event to inspect its causal inputs and execution state.</div></div>}</section>
      </div>
      <div className="replay-playback"><div className="replay-playback-buttons"><button title="Jump to beginning" onClick={reset}>|◀</button><button title="Step backward" onClick={() => seekTo(Math.max(0, cursor - 1))}>◀</button><button className="primary" title="Play or pause (Space)" onClick={() => setPlaying((value) => !value)}>{playing ? "Ⅱ" : "▶"}</button><button title="Step forward" onClick={() => seekTo(Math.min(candles.length, cursor + 1))}>▶</button><button title="Jump to end" onClick={() => seekTo(candles.length)}>▶|</button></div><input className="replay-scrubber" type="range" min="0" max={Math.max(candles.length, 1)} value={cursor} onChange={(event) => seekTo(event.target.value)} /><div className="replay-time-readout"><span>{shortTime(currentTimestamp)}</span><select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>{SPEEDS.map((item) => <option key={item} value={item}>{item}x</option>)}</select><button onClick={reset}>RESTART</button><span>{cursor} / {candles.length}</span></div></div>
      <div className="replay-metrics"><Metric label="TOTAL P&L" value={pct(result.return_pct)} tone={directionClass(result.return_pct)} /><Metric label="ENDING EQUITY" value={num(result.ending_equity).toLocaleString()} /><Metric label="MAX DRAWDOWN" value={pct(result.max_drawdown_pct)} tone="down" /><Metric label="WIN RATE" value={result.win_rate == null ? "--" : `${(result.win_rate * 100).toFixed(1)}%`} /><Metric label="TRADES" value={result.trades} /><Metric label="FEES / SLIPPAGE" value="PAPER MODEL" /></div>
    </>}
  </div>;
}
