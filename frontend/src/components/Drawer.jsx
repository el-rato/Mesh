import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchJSON, CHART_RANGES, rangeLabel, dossier, meshAnalysis } from "../api.js";
import { useApp } from "../App.jsx";
import PriceChart from "./PriceChart.jsx";
import AddToPortfolioButton from "./AddToPortfolioButton.jsx";
import { reasonText, RefreshStatus } from "./ui.jsx";

function sigCls(state) {
  const s = String(state || "").toLowerCase();
  if (s === "bull" || s === "bullish") return "bull";
  if (s === "bear" || s === "bearish") return "bear";
  if (s === "conflicted") return "conflicted";
  if (s === "uncertain") return "uncertain";
  return "neutral";
}

function plainStateLabel(state) {
  const s = String(state || "N/A").toUpperCase();
  if (s === "CONFLICTED") return "MIXED OUTLOOK";
  if (s === "TRENDING_BULL") return "UPTREND";
  if (s === "TRENDING_BEAR") return "DOWNTREND";
  return s;
}

function StateBadge({ state }) {
  return <span className={`badge ${sigCls(state)}`}>{plainStateLabel(state)}</span>;
}

function num(v, def = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : def;
}

function plainOutlook(state) {
  const s = String(state || "").toUpperCase();
  if (s === "BULL" || s === "BULLISH") return "Positive outlook";
  if (s === "BEAR" || s === "BEARISH") return "Negative outlook";
  if (s === "CONFLICTED") return "Mixed outlook";
  if (s === "UNCERTAIN") return "Unclear outlook";
  if (s === "NEUTRAL") return "Neutral outlook";
  return "No outlook yet";
}

function plainDirection(state) {
  const s = String(state || "").toUpperCase();
  if (s === "BULL" || s === "BULLISH") return "Up";
  if (s === "BEAR" || s === "BEARISH") return "Down";
  if (s === "CONFLICTED") return "Mixed";
  if (s === "NEUTRAL") return "Sideways";
  if (s === "UNCERTAIN") return "Unclear";
  return "No estimate";
}

function plainRisk(level) {
  const s = String(level || "").toUpperCase();
  if (s === "HIGH") return "High risk";
  if (s === "LOW") return "Low risk";
  if (s === "MEDIUM") return "Moderate risk";
  return "Risk unavailable";
}

function plainRegime(regime) {
  const labels = {
    TRENDING_BULL: "Uptrend",
    TRENDING_BEAR: "Downtrend",
    RANGE: "Sideways market",
    HIGH_VOL: "Volatile market",
    LOW_VOL: "Calm market",
    RISK_OFF: "Risk-off market",
  };
  return labels[String(regime || "").toUpperCase()] || "Market condition unclear";
}

function forecastTerm(horizon, index) {
  const labels = { "1D": "SHORT TERM", "5D": "MEDIUM TERM", "20D": "LONG TERM" };
  return labels[String(horizon || "").toUpperCase()] || ["SHORT TERM", "MEDIUM TERM", "LONG TERM"][index] || "OUTLOOK";
}

function plainEvidence(row, fallback) {
  const text = String(row?.reason || row?.direction || fallback || "No material signal identified.")
    .replace(/P\(up\)/gi, "chance of price increase")
    .replace(/TRENDING_BULL/gi, "uptrend")
    .replace(/TRENDING_BEAR/gi, "downtrend")
    .replace(/HIGH_VOL/gi, "high volatility")
    .replace(/LOW_VOL/gi, "low volatility")
    .replace(/_/g, " ");
  const sentence = (text.match(/^[^.!?]+/)?.[0] || text).trim();
  return sentence.charAt(0).toUpperCase() + sentence.slice(1);
}

/* ---------------- Analysis workspace ---------------- */

function DossierHeader({ dossierData, v, analysisState, onAnalyze }) {
  const { refreshStatus, openPaperTicket } = useApp();
  const verdict = dossierData.verdict || {};
  const mesh = verdict.mesh_signal || {};
  const meshState = analysisState || dossierData.mesh_analysis?.status || "IDLE";
  const inst = dossierData.instrument || {};
  const openTicket = (action) =>
    openPaperTicket({
      market: inst.market || v.market,
      ticker: inst.ticker || v.ticker,
      symbol: inst.symbol || "",
      company: inst.company || v.company || "",
      action,
      decision: verdict.decision || null,
    });
  return (
    <div className="dossier-header">
      <div className="dossier-identity">
        <div className="symbol-lg">{inst.ticker || v.ticker}</div>
        <div className="dossier-company">
          {[inst.company, inst.exchange || inst.market || v.market, inst.quote_type || "EQUITY"].filter(Boolean).join(" · ")}
        </div>
      </div>
      <div className="dossier-header-right">
        <button className="primary mesh-run mesh-header-action" type="button" onClick={onAnalyze} disabled={meshState === "ANALYZING"}>
          {meshState === "ANALYZING" ? "ANALYZING…" : "RUN MESH ANALYSIS"}
        </button>
        <AddToPortfolioButton market={inst.market || v.market} ticker={inst.ticker || v.ticker} company={inst.company || v.company} />
        <button className="paper-buy" onClick={() => openTicket("BUY")} title="Open paper BUY ticket">BUY</button>
        <button className="paper-short" onClick={() => openTicket("SHORT")} title="Open paper SHORT ticket">SHORT</button>
        <RefreshStatus status={refreshStatus} />
      </div>
      <div className="dossier-header-meta">
        MESH <strong className={sigCls(mesh.direction || meshState)}>{meshState}</strong>
        {mesh.direction && <> · OUTLOOK <strong className={sigCls(mesh.direction)}>{plainOutlook(mesh.direction)}</strong></>}
        {dossierData.stale && <span className="stale-flag">STALE</span>}
        {dossierData.mesh_analysis?.analyzed_at && <span className="dim"> · ANALYZED {String(dossierData.mesh_analysis.analyzed_at).slice(0, 19).replace("T", " ")}</span>}
      </div>
    </div>
  );
}

function ChartSection({ v, symbol, dossierData }) {
  const { theme } = useApp();
  const inst = dossierData?.instrument || {};
  const price = dossierData?.verdict?.price || {};
  const [range, setRange] = useState("1mo");
  const [chartType, setChartType] = useState("candlestick");
  const [showVolume, setShowVolume] = useState(true);
  const [sma, setSma] = useState([50, 200]);
  const [ema, setEma] = useState([]);
  const [bollinger, setBollinger] = useState(null);
  const [vwap, setVwap] = useState(false);
  const [rsi, setRsi] = useState(null);
  const [macd, setMacd] = useState(null);
  const [logScale, setLogScale] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);

  const close = Number(price.close);
  const open = Number(price.open);
  const changePct = Number.isFinite(close) && Number.isFinite(open) && open !== 0 ? (close - open) / open : 0;
  const up = changePct >= 0;
  const market = inst.market || v.market;
  const ticker = inst.ticker || v.ticker;
  // Build the chart URL from props (not from dossier data) so the price chart
  // can start loading immediately — in parallel with the dossier analysis call
  // — instead of waiting for that (slower) request to finish first.
  const chartUrl = symbol
    ? `/api/chart/${encodeURIComponent(market)}/${encodeURIComponent(ticker)}?range=${range}&symbol=${encodeURIComponent(symbol)}`
    : `/api/chart/${encodeURIComponent(market)}/${encodeURIComponent(ticker)}?range=${range}`;
  const toggle = (setter) => () => setter((val) => !val);

  const toggleInSet = (setter) => (p) => setter((arr) => (arr.includes(p) ? arr.filter((x) => x !== p) : [...arr, p].sort((a, b) => a - b)));

  return (
    <section className={`dossier-chart-pane${fullscreen ? " is-fullscreen" : ""}`}>
      <div className="chart-pane-head">
        <div className="chart-id">
          <div className="chart-symbol">
            <span className="chart-ticker">{market}:{ticker}</span>
            <span className="chart-company">{inst.company || v.company || ""}</span>
          </div>
          <div className="chart-price-row">
            <span className="chart-price">{Number.isFinite(close) ? close.toFixed(2) : "N/A"}</span>
            <span className={`chart-change ${up ? "bull" : "bear"}`}>
              {Number.isFinite(close) && Number.isFinite(open) ? `${up ? "+" : ""}${(close - open).toFixed(2)}` : "—"}
            </span>
            <span className={`chart-change ${up ? "bull" : "bear"}`}>
              {up ? "▲" : "▼"} {Math.abs(changePct * 100).toFixed(2)}%
            </span>
          </div>
        </div>
        <div className="chart-controls">
          <div className="chart-range-bar" role="group" aria-label="Timeframe">
            {CHART_RANGES.map((r) => (
              <button key={r} className={range === r ? "active" : ""} aria-pressed={range === r} onClick={() => setRange(r)}>
                {rangeLabel(r)}
              </button>
            ))}
          </div>
          <div className="indicator-bar">
            <button className={chartType === "candlestick" ? "active" : ""} aria-pressed={chartType === "candlestick"} onClick={() => setChartType("candlestick")}>Candles</button>
            <button className={chartType === "line" ? "active" : ""} aria-pressed={chartType === "line"} onClick={() => setChartType("line")}>Line</button>
            <button className={toolsOpen ? "active" : ""} aria-expanded={toolsOpen} onClick={() => setToolsOpen((val) => !val)}>Indicators</button>
            <button disabled title="Comparison is not available in this chart">Compare</button>
            <button className={showVolume ? "active" : ""} onClick={toggle(setShowVolume)} title="Volume overlay">VOL</button>
            <button className={sma.includes(50) ? "active" : ""} onClick={() => toggleInSet(setSma)(50)} title="SMA 50">MA50</button>
            <button className={sma.includes(200) ? "active" : ""} onClick={() => toggleInSet(setSma)(200)} title="SMA 200">MA200</button>
            <button className={vwap ? "active" : ""} onClick={toggle(setVwap)} title="VWAP">VWAP</button>
            <button className={rsi ? "active" : ""} onClick={() => setRsi(rsi ? null : { period: 14 })} title="RSI subpanel">RSI</button>
            <button className={macd ? "active" : ""} onClick={() => setMacd(macd ? null : { fast: 12, slow: 26, signal: 9 })} title="MACD subpanel">MACD</button>
            <button className={logScale ? "active" : ""} onClick={toggle(setLogScale)} title="Logarithmic scale">LOG</button>
            <select className="chart-type-select" value={chartType} onChange={(event) => setChartType(event.target.value)} aria-label="Chart type" title="Chart type">
              <option value="candlestick">CANDLES</option>
              <option value="ohlc">BARS</option>
              <option value="line">LINE</option>
              <option value="area">AREA</option>
            </select>
            <button className={`icon-btn ${toolsOpen ? "active" : ""}`} onClick={() => setToolsOpen((val) => !val)} title="Chart tools & indicators">⚙</button>
            <button className="icon-btn" onClick={toggle(setFullscreen)} title={fullscreen ? "Exit fullscreen" : "Fullscreen"}>
              {fullscreen ? "EXIT" : "⤢"}
            </button>
          </div>
        </div>
        {toolsOpen && (
          <div className="chart-tools">
            <div className="chart-tools-row">
              <span className="chart-tools-label">SMA</span>
              {[20, 50, 100, 200].map((p) => (
                <button key={p} className={`chip ${sma.includes(p) ? "on" : ""}`} onClick={() => toggleInSet(setSma)(p)}>{p}</button>
              ))}
            </div>
            <div className="chart-tools-row">
              <span className="chart-tools-label">EMA</span>
              {[9, 12, 26, 50].map((p) => (
                <button key={p} className={`chip ${ema.includes(p) ? "on" : ""}`} onClick={() => toggleInSet(setEma)(p)}>{p}</button>
              ))}
            </div>
            <div className="chart-tools-row">
              <span className="chart-tools-label">BOLLINGER</span>
              <button className={`chip ${bollinger ? "on" : ""}`} onClick={() => setBollinger(bollinger ? null : { period: 20, std: 2 })}>
                {bollinger ? `BB ${bollinger.period},${bollinger.std}σ` : "OFF"}
              </button>
              {bollinger && (
                <>
                  <label className="chart-tools-num">P<input type="number" min="2" max="100" value={bollinger.period} onChange={(e) => setBollinger({ ...bollinger, period: Number(e.target.value) || 20 })} /></label>
                  <label className="chart-tools-num">σ<input type="number" min="1" max="5" step="0.5" value={bollinger.std} onChange={(e) => setBollinger({ ...bollinger, std: Number(e.target.value) || 2 })} /></label>
                </>
              )}
            </div>
            <div className="chart-tools-row">
              <span className="chart-tools-label">RSI</span>
              <button className={`chip ${rsi ? "on" : ""}`} onClick={() => setRsi(rsi ? null : { period: 14 })}>{rsi ? `RSI ${rsi.period}` : "OFF"}</button>
              {rsi && <label className="chart-tools-num">P<input type="number" min="2" max="50" value={rsi.period} onChange={(e) => setRsi({ period: Number(e.target.value) || 14 })} /></label>}
            </div>
            <div className="chart-tools-row">
              <span className="chart-tools-label">MACD</span>
              <button className={`chip ${macd ? "on" : ""}`} onClick={() => setMacd(macd ? null : { fast: 12, slow: 26, signal: 9 })}>{macd ? `${macd.fast},${macd.slow},${macd.signal}` : "OFF"}</button>
              {macd && (
                <>
                  <label className="chart-tools-num">F<input type="number" min="2" max="50" value={macd.fast} onChange={(e) => setMacd({ ...macd, fast: Number(e.target.value) || 12 })} /></label>
                  <label className="chart-tools-num">S<input type="number" min="2" max="100" value={macd.slow} onChange={(e) => setMacd({ ...macd, slow: Number(e.target.value) || 26 })} /></label>
                  <label className="chart-tools-num">Sig<input type="number" min="2" max="50" value={macd.signal} onChange={(e) => setMacd({ ...macd, signal: Number(e.target.value) || 9 })} /></label>
                </>
              )}
            </div>
          </div>
        )}
      </div>
      <div className="chart-workspace">
        <PriceChart
          url={chartUrl}
          chartType={chartType}
          showVolume={showVolume}
          sma={sma}
          ema={ema}
          bollinger={bollinger}
          vwap={vwap}
          rsi={rsi}
          macd={macd}
          logScale={logScale}
          theme={theme}
        />
      </div>
      <div className="chart-legend">
        <span className="legend-price">PRICE</span>
        {sma.map((p) => <span key={`s${p}`} className="legend-sma50">SMA {p}</span>)}
        {ema.map((p) => <span key={`e${p}`} className="legend-sma50">EMA {p}</span>)}
        {bollinger && <span className="legend-sma200">BB {bollinger.period}</span>}
        {vwap && <span className="legend-volume">VWAP</span>}
        {showVolume && <span className="legend-volume">VOLUME</span>}
        {rsi && <span className="legend-sma50">RSI {rsi.period}</span>}
        {macd && <span className="legend-sma200">MACD</span>}
      </div>
    </section>
  );
}

const EVIDENCE_LABELS = {
  ml: "ML", trend: "TREND", momentum: "MOMENTUM", volume: "VOLUME",
  volatility: "VOLATILITY", candlesticks: "CANDLESTICKS",
  market_regime: "MARKET CONTEXT", market_context: "MARKET CONTEXT",
  relative_strength: "MARKET CONTEXT",
};

function EvidenceRows({ rows, opposing = false }) {
  if (!rows?.length) return <div className="mesh-empty">NO MATERIAL EVIDENCE</div>;
  return rows.map((row, i) => (
    <div className={`mesh-evidence-row ${opposing ? "opposing" : "supporting"}`} key={`${row.category}-${i}`}>
      <span>{EVIDENCE_LABELS[row.category] || String(row.category || "EVIDENCE").toUpperCase()}</span>
      <strong>{plainEvidence(row)}</strong>
      <em>{row.score > 0 ? "+" : ""}{num(row.score).toFixed(2)}</em>
    </div>
  ));
}

function MeshSignalSection({ verdict, analysisState = "IDLE", analysisError }) {
  const mesh = verdict.mesh_signal || {};
  if (analysisState !== "READY" && analysisState !== "PARTIAL") {
    return (
      <div className={`mesh-status-row ${analysisState === "ERROR" ? "mesh-error" : ""}`}>
        <div className="mesh-idle-copy">
          <span className="mesh-kicker">MESH ANALYSIS</span>
          <strong>{analysisState === "ANALYZING" ? "ANALYZING WITH MESH…" : analysisState === "ERROR" ? "MESH ANALYSIS FAILED" : "Run deep analysis for this stock"}</strong>
          {analysisError && <span className="mesh-error-text">{analysisError}</span>}
        </div>
        <strong className={`mesh-status-code ${sigCls(analysisState)}`}>{analysisState}</strong>
      </div>
    );
  }
  const forecasts = (mesh.forecasts || []).slice(0, 3);
  const strongestSupport = mesh.supporting_evidence?.[0];
  const strongestCaution = mesh.opposing_evidence?.[0];
  const explanation = strongestSupport && strongestCaution
    ? `The positive case is led by ${plainEvidence(strongestSupport).toLowerCase()}, while ${plainEvidence(strongestCaution).toLowerCase()} limits confidence.`
    : strongestSupport
      ? `The outlook is mainly supported by ${plainEvidence(strongestSupport).toLowerCase()}.`
      : strongestCaution
        ? `The main caution is ${plainEvidence(strongestCaution).toLowerCase()}.`
        : "The available signals do not yet point clearly in one direction.";
  return (
    <div className={`mesh-intel ${sigCls(mesh.direction)}`}>
      <div className="mesh-primary">
        <div className="mesh-signal-main">
          <span className="mesh-kicker">OVERALL OUTLOOK</span>
          <div className="mesh-signal-value">
            <strong className={`mesh-direction ${sigCls(mesh.direction)}`}>{plainOutlook(mesh.direction)}</strong>
          </div>
        </div>
        <div className="mesh-stat"><span>RISK</span><strong>{plainRisk(mesh.risk_level)}</strong></div>
        <div className="mesh-stat"><span>MARKET CONDITION</span><strong>{plainRegime(mesh.regime)}</strong></div>
      </div>

      <div className="mesh-forecasts">
        {[0, 1, 2].map((index) => {
          const row = forecasts[index] || {};
          return (
          <div className="mesh-forecast" key={row.horizon || index}>
            <span>{forecastTerm(row.horizon, index)}</span>
            <strong className={`mesh-forecast-direction ${sigCls(row.direction)}`}>{plainDirection(row.direction)}</strong>
          </div>
          );
        })}
      </div>
      <div className="mesh-explanation">{explanation}</div>
      <div className="mesh-why-caution">
        <div className="mesh-reason why"><span>WHY</span><p>{plainEvidence(strongestSupport, "No strong supporting signal was identified.")}</p></div>
        <div className="mesh-reason caution"><span>CAUTION</span><p>{plainEvidence(strongestCaution, "No strong opposing signal was identified.")}</p></div>
      </div>
    </div>
  );
}

function MeshAnalysisDetail({ verdict }) {
  const mesh = verdict.mesh_signal || {};
  const candles = (mesh.evidence || []).find((row) => row.category === "candlesticks") || {};
  const patterns = candles.metrics?.patterns || [];
  const reliability = mesh.reliability || {};
  const evidenceHeader = (
    <div className="mesh-evidence-head" aria-hidden="true">
      <span>CATEGORY</span><span>EXPLANATION</span><span>SCORE</span>
    </div>
  );
  return (
    <div className="mesh-analysis-detail">
      <section className="mesh-diagnostics">
        <h3>MESH DIAGNOSTICS</h3>
        <div className="mesh-diagnostic-grid">
          <div><span>MESH CONFIDENCE</span><strong>{mesh.confidence == null ? "N/A" : `${(num(mesh.confidence) * 100).toFixed(0)}%`}</strong></div>
          <div><span>SUPPORTING SIGNALS</span><strong>{mesh.agreement_score == null ? "N/A" : `${(num(mesh.agreement_score) * 100).toFixed(0)}%`}</strong></div>
          <div><span>CONFLICTING SIGNALS</span><strong>{mesh.contradiction_score == null ? "N/A" : `${(num(mesh.contradiction_score) * 100).toFixed(0)}%`}</strong></div>
          <div><span>MARKET CONDITION CODE</span><strong>{mesh.regime || "UNKNOWN"}</strong></div>
          <div><span>CONDITION CONFIDENCE</span><strong>{mesh.regime_confidence == null ? "N/A" : `${(num(mesh.regime_confidence) * 100).toFixed(0)}%`}</strong></div>
        </div>
      </section>
      <section className="mesh-advanced-forecasts">
        <h3>DIRECTION ESTIMATES</h3>
        <div className="mesh-advanced-forecast-head" aria-hidden="true">
          <span>TERM</span><span>DIRECTION</span><span>CHANCE OF PRICE INCREASE</span><span>CALIBRATION</span>
        </div>
        {(mesh.forecasts || []).map((row, index) => (
          <div className="mesh-advanced-forecast" key={row.horizon || index}>
            <span>{forecastTerm(row.horizon, index)} ({row.horizon || "N/A"})</span>
            <strong className={sigCls(row.direction)}>{plainDirection(row.direction)}</strong>
            <span>{row.probability_up == null ? "NO DATA" : `${(num(row.probability_up) * 100).toFixed(1)}%`}</span>
            <span>{row.calibrated ? "CALIBRATED" : row.status === "ok" ? "UNCALIBRATED" : "UNAVAILABLE"}</span>
          </div>
        ))}
      </section>
      <div className="mesh-evidence-grid">
        <section className="supporting"><h3>SUPPORTING EVIDENCE</h3>{evidenceHeader}<EvidenceRows rows={mesh.supporting_evidence} /></section>
        <section className="opposing"><h3>OPPOSING EVIDENCE</h3>{evidenceHeader}<EvidenceRows rows={mesh.opposing_evidence} opposing /></section>
      </div>
      <div className="mesh-candles">
        <h3>CANDLESTICK ANALYSIS</h3>
        {patterns.length ? patterns.map((pattern) => (
          <div className="mesh-pattern" key={pattern}>
            <strong>{String(pattern).replace(/_/g, " ").toUpperCase()}</strong>
            <span>{candles.direction || "NEUTRAL"} · CONTEXT {Math.round(num(candles.confidence) * 100)}%</span>
            <span>{candles.metrics?.volume_confirmed ? "VOLUME CONFIRMED" : "AWAITING VOLUME CONFIRMATION"}</span>
            <small>{candles.reason || "Pattern is contextual evidence, not an automatic trade signal."}</small>
          </div>
        )) : <div className="mesh-empty">NO CONTEXT-CONFIRMED PATTERN</div>}
      </div>
      <div className="mesh-reliability">
        <span>MODEL RELIABILITY</span>
        <strong>{reliability.model ? `${String(reliability.model).toUpperCase()} / ${reliability.model_version || "UNVERSIONED"}` : "NO MODEL"}</strong>
        <span>{reliability.calibration_status || "NO DATA"}</span>
        <span>{reliability.status === "INSUFFICIENT_DATA" ? `INSUFFICIENT DATA · N=${reliability.sample_count}/${reliability.minimum_samples || 30}` : reliability.historical_hit_rate == null ? "SIMILAR-CASE HIT RATE N/A" : `${(num(reliability.historical_hit_rate) * 100).toFixed(0)}% HIT RATE · N=${reliability.sample_count}`}</span>
        <span>{reliability.last_evaluated_at ? `EVALUATED ${String(reliability.last_evaluated_at).slice(0, 10)}` : "NOT YET EVALUATED"}</span>
      </div>
      <div className="mesh-transparency">MODEL PROBABILITY feeds the fusion layer. MESH CONFIDENCE reflects cross-engine agreement, regime and risk.</div>
    </div>
  );
}

function QuoteSection({ dossierData, analysisError }) {
  const verdict = dossierData.verdict || {};
  const decision = verdict.decision || {};
  const price = verdict.price || {};
  const signals = decision.signals || {};
  const availSignals = Object.values(signals).filter((s) => s.status === "AVAILABLE");
  const aligned = availSignals.filter((s) => s.direction === decision.verdict).length;
  const coverage = `${availSignals.length}/${Object.keys(signals).length || 0}`;
  const rows = [
    ["CLOSE", num(price.close).toFixed(2)], ["OPEN", num(price.open).toFixed(2)],
    ["HIGH", num(price.high).toFixed(2)], ["LOW", num(price.low).toFixed(2)],
    ["VOLUME", num(price.volume, 0).toLocaleString()], ["MOMENTUM 20D", num(price.momentum_20).toFixed(2)],
    ["RSI 14", num(price.rsi_14).toFixed(0)], ["SMA 50", num(price.sma_50).toFixed(2)],
    ["SMA 200", num(price.sma_200).toFixed(2)],
  ];
  return (
    <div className="quote-section">
      <MeshSignalSection verdict={verdict} analysisState={dossierData.mesh_analysis?.status || "IDLE"} analysisError={analysisError} />
      <details className="mesh-advanced">
        <summary>ADVANCED DETAILS</summary>
        <div className="mesh-advanced-body">
      <section className="overview-section overview-committee">
        <h3>COMMITTEE DECISION</h3>
      {decision.status === "ok" ? (
        <>
          <div className="decision-primary">
            <div className="decision-verdict">
              <span className={`badge ${sigCls(decision.verdict)}`}>{plainStateLabel(decision.verdict)}</span>
              <span className="decision-conviction">{decision.conviction != null ? Math.round(decision.conviction * 100) : "—"} CONVICTION</span>
            </div>
            <div className="decision-agreement">{aligned}/{availSignals.length} SIGNALS ALIGNED · DATA {coverage}</div>
          </div>
          {decision.thesis && <div className="decision-thesis">{decision.thesis}</div>}
          {decision.primary_risks?.[0] && (
            <div className="decision-line"><span className="lbl">KEY RISK</span>{decision.primary_risks[0]}</div>
          )}
          {decision.key_disagreement && (
            <div className="decision-line"><span className="lbl">CONFLICTING VIEW</span>{decision.key_disagreement}</div>
          )}
          {decision.view_changes_if && (
            <div className="decision-line dim"><span className="lbl">VIEW CHANGES IF</span>{decision.view_changes_if}</div>
          )}
        </>
      ) : (
        <div className="decision-primary">
          <span className={`badge neutral`}>NO DECISION</span>
          <div className="decision-agreement">DATA {coverage} · VERDICT {String(verdict.verdict || "N/A")}</div>
        </div>
      )}
      </section>

      <section className="overview-section overview-quotes">
        <h3>QUOTE &amp; PRICE</h3>
        <div className="quote-grid">
          {rows.map(([key, value]) => <div className="quote-cell" key={key}><span>{key}</span><strong>{value}</strong></div>)}
        </div>
      </section>
      {(dossierData.mesh_analysis?.status === "READY" || dossierData.mesh_analysis?.status === "PARTIAL") && <MeshAnalysisDetail verdict={verdict} />}
      <details className="paper-detail">
        <summary>FULL REASONING</summary>
        <div className="dossier-summary">{reasonText(verdict.reason) || "No additional explanation available."}</div>
      </details>
        </div>
      </details>
    </div>
  );
}

/* ---------------- Investment Committee ---------------- */

function QuantDetail({ signal }) {
  const [open, setOpen] = useState(true);
  const models = signal?.models || [];
  return (
    <>
      <div className="team-row" onClick={() => setOpen((v) => !v)} style={{ cursor: "pointer" }}>
        <span className="team-label">QUANTITATIVE <span className="expand">{open ? "−" : "+"}</span></span>
        <StateBadge state={signal.state} />
        <span className="team-value">{signal.score == null ? "N/A" : `${signal.score > 0 ? "+" : ""}${signal.score.toFixed(2)}`}</span>
        <span className="team-value">{signal.confidence == null ? "N/A" : `${(signal.confidence * 100).toFixed(0)}%`}</span>
        <span className="team-value">{(signal.weight * 100).toFixed(0)}%</span>
        <span className="team-value">{signal.contribution == null ? "N/A" : `${signal.contribution > 0 ? "+" : ""}${signal.contribution.toFixed(2)}`}</span>
      </div>
      {open && (
        <div className="team-models">
          {(models.length ? models : []).map((m) => (
            <div className="team-model" key={m.model_name}>
              <span className="tm-name">{m.model_name.toUpperCase()}</span>
              {m.status === "ok" ? (
                <>
                  <span className="tm-dir">{m.direction || "NEUTRAL"}</span>
                  <span className="tm-score">{m.score == null ? "—" : (m.score > 0 ? "+" : "") + m.score.toFixed(2)}</span>
                  <span className="tm-conf">{m.confidence == null ? "—" : (m.confidence * 100).toFixed(0) + "%"}</span>
                </>
              ) : (
                <span className="tm-na">{m.status === "no_data" ? "NO DATA" : "UNAVAILABLE"}</span>
              )}
            </div>
          ))}
          {signal.available && (
            <div className="team-model ensemble">
              <span className="tm-name">ENSEMBLE</span>
              <span className="tm-dir">{signal.direction}</span>
              <span className="tm-score">{signal.score == null ? "—" : (signal.score > 0 ? "+" : "") + signal.score.toFixed(2)}</span>
              <span className="tm-conf">{signal.confidence == null ? "—" : (signal.confidence * 100).toFixed(0) + "%"}</span>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function CommitteeSection({ committee }) {
  const confidence = committee.confidence == null ? "N/A" : `${(Number(committee.confidence) * 100).toFixed(0)}%`;
  const score = committee.score == null ? "N/A" : `${committee.score > 0 ? "+" : ""}${Number(committee.score).toFixed(2)}`;
  const quant = (committee.signals || []).find((s) => s.key === "quant");
  return (
    <div className="team">
      <div className="team-head">
        <span>SIGNAL</span><span>DIRECTION</span><span>SCORE</span><span>CONF.</span><span>WEIGHT</span><span>CONTRIB.</span>
      </div>
      {(committee.signals || []).map((s) =>
        s.key === "quant" ? (
          <QuantDetail signal={s} key={s.key} />
        ) : (
          <div className={`team-row ${s.available ? "" : "na"}`} key={s.key}>
            <span className="team-label">{s.label}</span>
            <StateBadge state={s.state} />
            <span className="team-value">{s.score == null ? "N/A" : `${s.score > 0 ? "+" : ""}${s.score.toFixed(2)}`}</span>
            <span className="team-value">{s.confidence == null ? "N/A" : `${(s.confidence * 100).toFixed(0)}%`}</span>
            <span className="team-value">{(s.weight * 100).toFixed(0)}%</span>
            <span className="team-value">{s.contribution == null ? "N/A" : `${s.contribution > 0 ? "+" : ""}${s.contribution.toFixed(2)}`}</span>
          </div>
        )
      )}
      <div className="team-row final">
        <span className="team-label">FINAL</span>
        <StateBadge state={committee.verdict} />
        <span className="team-value">{score}</span>
        <span className="team-score">{confidence} CONFIDENCE</span>
      </div>
      <div className="team-why">
        <strong>WHY</strong>
        {(committee.why || []).map((reason, i) => <div key={i}>• {reason}</div>)}
      </div>
      <div className="team-note">
        MISSING SIGNALS ARE EXCLUDED · QUANTITATIVE = ENSEMBLE OF AVAILABLE MODELS
      </div>
    </div>
  );
}

/* ---------------- Bull / Bear ---------------- */

function FactorList({ factors }) {
  return (
    <div className="bullbear">
      <div className="bb-col">
        <h3 className="bb-h bull">BULL CASE</h3>
        {(factors.bull || []).length ? (
          factors.bull.map((f, i) => (
            <div className="factor bull" key={i}>
              <span className="plus">+</span> {f}
            </div>
          ))
        ) : (
          <div className="factor none">NO SUPPORTING FACTORS</div>
        )}
      </div>
      <div className="bb-col">
        <h3 className="bb-h bear">BEAR CASE</h3>
        {(factors.bear || []).length ? (
          factors.bear.map((f, i) => (
            <div className="factor bear" key={i}>
              <span className="minus">−</span> {f}
            </div>
          ))
        ) : (
          <div className="factor none">NO CONTRARY FACTORS</div>
        )}
      </div>
    </div>
  );
}

/* ---------------- Model ---------------- */

function ModelSection({ verdict }) {
  const quant = verdict.quantitative || {};
  const models = verdict.models || [];
  const lstm = verdict.lstm || {};
  const prob = lstm.probability_up;
  const isUp = num(prob, 0.5) >= 0.5;
  const metrics = lstm.metrics || {};
  const mesh = verdict.mesh_signal || {};
  const reliability = mesh.reliability || {};
  const availableModels = models.filter((m) => m.status === "ok");
  const quantRows = [
    ["ENSEMBLE", quant.status === "ok" ? `${quant.direction || "NEUTRAL"} · ${quant.score != null ? (quant.score > 0 ? "+" : "") + quant.score.toFixed(3) : "n/a"} · ${quant.confidence != null ? (quant.confidence * 100).toFixed(0) + "%" : "n/a"}` : "UNAVAILABLE"],
    ["MODELS AVAILABLE", `${availableModels.length}/${models.length}`],
    ["FORECAST HORIZON", verdict.forecast_horizon || "1 trading day"],
  ];
  return (
    <div className="model">
      <h3>QUANTITATIVE MODEL — ENSEMBLE</h3>
      <div className="dossier-rows">
        {quantRows.map(([k, val]) => (
          <div className="row" key={k}>
            <span className="label">{k}</span>
            <span className="value">{val}</span>
          </div>
        ))}
      </div>
      <h3>MODELS</h3>
      <div className="team-models">
        {models.length ? (
          models.map((m) => (
            <div className="team-model" key={m.model_name}>
              <span className="tm-name">{m.model_name.toUpperCase()}</span>
              {m.status === "ok" ? (
                <>
                  <span className="tm-dir">{m.direction || "NEUTRAL"}</span>
                  <span className="tm-score">{m.score == null ? "—" : (m.score > 0 ? "+" : "") + m.score.toFixed(2)}</span>
                  <span className="tm-conf">{m.confidence == null ? "—" : (m.confidence * 100).toFixed(0) + "%"}</span>
                </>
              ) : (
                <span className="tm-na">{m.status === "no_data" ? "NO DATA" : "UNAVAILABLE"}</span>
              )}
            </div>
          ))
        ) : (
          <div className="team-model"><span className="tm-na">NO MODELS AVAILABLE</span></div>
        )}
      </div>
      <h3>MODEL PROBABILITY — LSTM DETAIL</h3>
      <div className="dossier-rows">
        {[
          ["CHANCE OF PRICE INCREASE", prob != null ? <span key="p" style={{ color: isUp ? "var(--bull)" : "var(--bear)" }}>{(num(prob) * 100).toFixed(1)}%</span> : "N/A"],
          ["PREDICTED RETURN", lstm.predicted_return != null ? <span key="r" className={isUp ? "up" : "down"}>{num(lstm.predicted_return) > 0 ? "+" : ""}{(num(lstm.predicted_return) * 100).toFixed(2)}%</span> : "N/A"],
          ["MODEL CLASS CONFIDENCE", lstm.model_confidence != null ? num(lstm.model_confidence).toFixed(3) : "N/A"],
          ["LSTM SCORE", num(lstm.score).toFixed(3)],
          ["MODEL VERSION", lstm.model_version || "N/A"],
          ...Object.entries(metrics).map(([k, v2]) => [k.toUpperCase().replace(/_/g, " "), v2]),
        ].map(([k, val]) => (
          <div className="row" key={k}>
            <span className="label">{k}</span>
            <span className="value">{val}</span>
          </div>
        ))}
      </div>
      <h3>MESH RELIABILITY</h3>
      <div className="dossier-rows">
        {[
          ["MESH CONFIDENCE", mesh.confidence == null ? "N/A" : `${(num(mesh.confidence) * 100).toFixed(0)}%`],
          ["CALIBRATION STATUS", reliability.calibration_status || "NO DATA"],
          ["SIMILAR CONFIDENCE / REGIME", reliability.status === "INSUFFICIENT_DATA" ? `INSUFFICIENT DATA (N=${reliability.sample_count}/${reliability.minimum_samples || 30})` : reliability.historical_hit_rate == null ? "NOT YET EVALUATED" : `${(num(reliability.historical_hit_rate) * 100).toFixed(1)}% HIT RATE (N=${reliability.sample_count})`],
          ["LAST EVALUATION", reliability.last_evaluated_at || "N/A"],
        ].map(([k, val]) => (
          <div className="row" key={k}><span className="label">{k}</span><span className="value">{val}</span></div>
        ))}
      </div>
      <div className="model-disclaimer">
        MODEL PROBABILITY IS NOT MESH CONFIDENCE. PROBABILISTIC MODELS — NOT FINANCIAL ADVICE.
        PREDICTIONS ARE ESTIMATES AND CAN BE WRONG. MODELS ARE WEIGHTED BY AVAILABILITY.
      </div>
    </div>
  );
}

/* ---------------- News ---------------- */

function NewsSection({ news, committee }) {
  const newsRow = (committee?.signals || []).find((s) => s.key === "news");
  const hasNews = newsRow?.available && news?.length;
  return (
    <>
      {newsRow?.available && (
        <div className="news-evidence">
          <div className="news-evidence-head">
            <span>NEWS</span>
            <StateBadge state={newsRow.state} />
          </div>
          <div className="quote-grid">
            {newsRow.score != null && (
              <div className="quote-cell"><span>SCORE</span><strong>{newsRow.score > 0 ? "+" : ""}{newsRow.score.toFixed(2)}</strong></div>
            )}
            {newsRow.confidence != null && (
              <div className="quote-cell"><span>CONFIDENCE</span><strong>{(newsRow.confidence * 100).toFixed(0)}%</strong></div>
            )}
            {newsRow.article_count != null && (
              <div className="quote-cell"><span>ARTICLES</span><strong>{newsRow.article_count}</strong></div>
            )}
          </div>
        </div>
      )}
      <h3>CONTRIBUTING ARTICLES</h3>
      {hasNews ? (
        <div className="news-list">
          {news.slice(0, 40).map((n, i) => (
            <div className="news-item" key={i}>
              <div className="sender">{n.title}</div>
              <div className="src">
                {n.source} · {n.published_at}{" "}
                <span className={`sent ${n.sentiment_label === "positive" ? "up" : n.sentiment_label === "negative" ? "down" : "neutral"}`}>
                  {n.sentiment_label || "N/A"}
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty">NO HEADLINES FOR THIS TICKER.</div>
      )}
    </>
  );
}

/* ---------------- Risk ---------------- */

function annVol(closes) {
  const rets = [];
  for (let i = 1; i < closes.length; i++) {
    const prev = closes[i - 1];
    if (prev) rets.push(closes[i] / prev - 1);
  }
  if (rets.length < 3) return null;
  const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
  const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / rets.length;
  return Math.sqrt(variance) * Math.sqrt(252);
}

function RiskSection({ verdict, symbol, market, ticker }) {
  const price = verdict.price || {};
  const [vol, setVol] = useState(null);
  const [volErr, setVolErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const q = symbol ? `&symbol=${encodeURIComponent(symbol)}` : "";
    fetchJSON(`/api/chart/${encodeURIComponent(market)}/${encodeURIComponent(ticker)}?range=1y${q}`)
      .then((d) => {
        if (cancelled) return;
        const closes = (d.data || []).map((r) => r.close).filter((c) => c != null);
        setVol(annVol(closes));
      })
      .catch(() => setVolErr(true));
    return () => {
      cancelled = true;
    };
  }, [symbol, market, ticker]);

  const metrics = [
    ["ANNUALIZED VOL (1Y)", vol != null ? (vol * 100).toFixed(1) + "%" : volErr ? "N/A" : "…"],
    ["20D MOMENTUM", num(price.momentum_20).toFixed(2)],
    ["RSI 14", num(price.rsi_14).toFixed(0)],
    ["SMA 50", num(price.sma_50).toFixed(2)],
    ["TREND 50/200", num(price.trend_50_200).toFixed(3)],
    ["LAST CLOSE", num(price.close).toFixed(2)],
  ];

  return (
    <div className="risk-wrap">
      <h3>PRICE RISK</h3>
      <div className="risk-metrics">
        {metrics.map(([k, vval]) => (
          <div className="risk-metric" key={k}>
            <div className="k">{k}</div>
            <div className="v">{vval}</div>
          </div>
        ))}
      </div>
      <h3>MODEL RISK</h3>
      <div className="dossier-rows">
        <div className="row">
          <span className="label">FORECAST HORIZON</span>
          <span className="value">{verdict.forecast_horizon || "1 trading day"}</span>
        </div>
        <div className="row">
          <span className="label">SUPPORTING SIGNALS</span>
          <span className="value">{String(verdict.signal_agreement || "unknown").toUpperCase()}</span>
        </div>
      </div>
      <div className="model-disclaimer">
        RISK METRICS DERIVED FROM PRICE HISTORY AND MODEL OUTPUT. NOT A COMPLETE RISK ASSESSMENT —
        NO DRAWDOWN, BETA, OR FUNDAMENTAL DATA AVAILABLE.
      </div>
    </div>
  );
}

/* ---------------- Stock Dossier ---------------- */

const DOSSIER_TABS = [
  { key: "overview", label: "OVERVIEW" },
  { key: "committee", label: "COMMITTEE" },
  { key: "bullbear", label: "BULL / BEAR" },
  { key: "model", label: "MODEL" },
  { key: "news", label: "NEWS" },
  { key: "risk", label: "RISK" },
];

function StockDossier({ v, onClose }) {
  const { markets, refreshToken } = useApp();
  const [tab, setTab] = useState("overview");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [analysisState, setAnalysisState] = useState("IDLE");
  const [analysisError, setAnalysisError] = useState("");

  const symbol = useMemo(() => {
    if (v.symbol) return v.symbol;
    const m = (markets || []).find((mm) => mm.code === v.market);
    return v.ticker + (m?.yahoo_suffix || "");
  }, [v, markets]);

  const reqRef = useRef(0);
  const abortRef = useRef(null);
  const load = useCallback(() => {
    const req = ++reqRef.current;
    setError("");
    dossier({ symbol, market: v.market, ticker: v.ticker })
      .then((d) => {
        if (req !== reqRef.current) return;
        setData(d);
        const state = d.mesh_analysis?.status || "IDLE";
        if (state === "READY" || state === "PARTIAL") setAnalysisState(state);
      })
      .catch((e) => {
        if (req === reqRef.current) setError(e.message);
      });
  }, [symbol, v.market, v.ticker]);

  const runAnalysis = useCallback(() => {
    const req = ++reqRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setAnalysisState("ANALYZING");
    setAnalysisError("");
    meshAnalysis(
      { symbol, market: v.market, ticker: v.ticker, timeframe: "1d" },
      { signal: controller.signal },
    )
      .then((d) => {
        if (req !== reqRef.current) return;
        setData(d);
        setAnalysisState(d.mesh_analysis?.status || "READY");
      })
      .catch((e) => {
        if (req !== reqRef.current || e.name === "AbortError") return;
        setAnalysisState("ERROR");
        setAnalysisError(e.message);
      });
  }, [symbol, v.market, v.ticker]);

  useEffect(() => {
    setData(null);
    setTab("overview");
    setAnalysisState("IDLE");
    setAnalysisError("");
    load();
    return () => {
      reqRef.current += 1;
      abortRef.current?.abort();
    };
  }, [load]);

  useEffect(() => {
    if (refreshToken && analysisState !== "ANALYZING") load();
  }, [refreshToken, analysisState, load]);

  const inst = data?.instrument || {};

  return (
    <>
      <button className="close" onClick={onClose}>✕</button>
      {!data && !error && <div className="empty">LOADING DOSSIER…</div>}
      {!data && error && (
        <div className="error">
          <div style={{ marginBottom: 12 }}>ERROR: {error}</div>
          <button className="primary" onClick={load}>⟳ RETRY</button>
        </div>
      )}
      {data && (
        <>
          {error && (
            <div className="scan-warning">⚠ REFRESH FAILED · SHOWING LAST-KNOWN DATA — {error}</div>
          )}
          <DossierHeader dossierData={data} v={v} analysisState={analysisState} onAnalyze={runAnalysis} />
        </>
      )}
      {/* The chart pane mounts immediately so the price chart loads in parallel
          with the (slower) dossier analysis call — not after it. */}
      <div className="dossier-workspace">
        <ChartSection v={v} symbol={symbol} dossierData={data} />
        {data && (
          <section className="dossier-info-pane">
            <div className="dossier-tabs">
              {DOSSIER_TABS.map((t) => (
                <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
                  {t.label}
                </button>
              ))}
            </div>
            <div className="dossier-info-scroll">
              {tab === "overview" && <QuoteSection dossierData={{ ...data, mesh_analysis: { ...(data.mesh_analysis || {}), status: analysisState } }} analysisError={analysisError} />}
              {tab === "committee" && <CommitteeSection committee={data.committee} />}
              {tab === "bullbear" && <FactorList factors={data.factors} />}
              {tab === "model" && <ModelSection verdict={data.verdict} />}
              {tab === "news" && <NewsSection news={data.news} committee={data.committee} />}
              {tab === "risk" && (
                <RiskSection verdict={data.verdict} symbol={inst.symbol} market={inst.market || v.market} ticker={inst.ticker || v.ticker} />
              )}
            </div>
            </section>
          )}
        </div>
      </>
  );
}

/* ---------------- Fund detail (unchanged) ---------------- */

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
          <div className="a-sub">PERIOD OF REPORT: {detail.period_of_report || "N/A"} · FUND ID {detail.fund_id || detail.cik || "N/A"}</div>

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
                {c.ticker || c.issuer} <span className={`badge ${sigCls(c.action)}`}>{c.action}</span>
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
        {item?.type === "stock" && <StockDossier v={item.v} onClose={onClose} />}
        {item?.type === "fund" && <FundDetail s={item.s} onClose={onClose} />}
      </aside>
    </>
  );
}
