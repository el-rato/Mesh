import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchJSON, CHART_RANGES, rangeLabel, dossier } from "../api.js";
import { useApp } from "../App.jsx";
import PriceChart from "./PriceChart.jsx";
import AddToPortfolioButton from "./AddToPortfolioButton.jsx";
import { verdictBadge, reasonText, RefreshStatus } from "./ui.jsx";

function sigCls(state) {
  const s = String(state || "").toLowerCase();
  if (s === "bull" || s === "bullish") return "bull";
  if (s === "bear" || s === "bearish") return "bear";
  return "neutral";
}

function StateBadge({ state }) {
  return <span className={`badge ${sigCls(state)}`}>{String(state || "N/A").toUpperCase()}</span>;
}

function num(v, def = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : def;
}

/* ---------------- Analysis workspace ---------------- */

function DossierHeader({ dossierData, v }) {
  const { refreshStatus, openPaperTicket } = useApp();
  const verdict = dossierData.verdict || {};
  const inst = dossierData.instrument || {};
  const conf = verdict.confidence == null ? "N/A" : `${(num(verdict.confidence) * 100).toFixed(0)}%`;
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
      <div>
        <div className="symbol-lg">{inst.ticker || v.ticker}</div>
        <div className="dossier-company">{inst.company || ""} · {inst.exchange || inst.market || v.market}</div>
      </div>
      <div className="dossier-header-right">
        <span className="dossier-mkt">{inst.market || v.market} · {inst.quote_type || "EQUITY"}</span>
        {verdictBadge(verdict)}
        <AddToPortfolioButton market={inst.market || v.market} ticker={inst.ticker || v.ticker} company={inst.company || v.company} />
        <button className="paper-trade" onClick={() => openTicket("")} title="Open paper order ticket (BUY / SELL / SHORT / COVER)">PAPER TRADE</button>
        <button className="paper-buy" onClick={() => openTicket("BUY")} title="Open paper order ticket">BUY</button>
        <button className="paper-short" onClick={() => openTicket("SHORT")} title="Open paper order ticket">SHORT</button>
        <RefreshStatus status={refreshStatus} />
      </div>
      <div className="dossier-header-meta">
        CONFIDENCE <strong>{conf}</strong>
        {dossierData.stale && <span className="stale-flag">STALE</span>}
        {dossierData.computed_at && <span className="dim"> · ANALYZED {String(dossierData.computed_at).slice(11, 19)}</span>}
      </div>
    </div>
  );
}

function ChartSection({ dossierData }) {
  const { theme } = useApp();
  const inst = dossierData.instrument || {};
  const price = dossierData.verdict?.price || {};
  const [range, setRange] = useState("1mo");
  const [chartType, setChartType] = useState("candlestick");
  const [showVolume, setShowVolume] = useState(true);
  const [showSma50, setShowSma50] = useState(false);
  const [showSma200, setShowSma200] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);

  const close = Number(price.close);
  const open = Number(price.open);
  const changePct = Number.isFinite(close) && Number.isFinite(open) && open !== 0 ? (close - open) / open : 0;
  const up = changePct >= 0;
  const rangeHost = vRange(inst);
  const toggle = (setter) => () => setter((v) => !v);
  return (
    <section className={`dossier-chart-pane${fullscreen ? " is-fullscreen" : ""}`}>
      <div className="chart-pane-head">
        <div className="chart-id">
          <div className="chart-symbol">
            <span className="chart-ticker">{inst.market}:{inst.ticker}</span>
            <span className="chart-company">{inst.company || ""}</span>
          </div>
          <div className="chart-price-row">
            <span className="chart-price">{Number.isFinite(close) ? close.toFixed(2) : "N/A"}</span>
            <span className={`chart-change ${up ? "bull" : "bear"}`}>
              {up ? "▲" : "▼"} {Math.abs(changePct * 100).toFixed(2)}%
            </span>
          </div>
        </div>
        <div className="chart-controls">
          <div className="chart-range-bar" role="group" aria-label="Timeframe">
            {CHART_RANGES.map((r) => (
              <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>
                {rangeLabel(r)}
              </button>
            ))}
          </div>
          <div className="indicator-bar">
            <button className={showVolume ? "active" : ""} onClick={toggle(setShowVolume)} title="Volume">VOL</button>
            <button className={showSma50 ? "active" : ""} onClick={toggle(setShowSma50)} title="50-period moving average">MA50</button>
            <button className={showSma200 ? "active" : ""} onClick={toggle(setShowSma200)} title="200-period moving average">MA200</button>
            <select className="chart-type-select" value={chartType} onChange={(event) => setChartType(event.target.value)} aria-label="Chart type" title="Chart type">
              <option value="candlestick">CANDLES</option>
              <option value="ohlc">OHLC</option>
              <option value="line">LINE</option>
              <option value="area">AREA</option>
            </select>
            <button className="icon-btn" onClick={toggle(setFullscreen)} title={fullscreen ? "Exit fullscreen" : "Fullscreen"}>
              {fullscreen ? "EXIT" : "⤢"}
            </button>
          </div>
        </div>
      </div>
      <div className="chart-workspace">
        <PriceChart url={rangeHost(range)} chartType={chartType} showVolume={showVolume} showSma50={showSma50} showSma200={showSma200} theme={theme} refreshKey={dossierData.computed_at} />
      </div>
      <div className="chart-legend">
        <span className="legend-price">PRICE</span>
        {showSma50 && <span className="legend-sma50">MA 50</span>}
        {showSma200 && <span className="legend-sma200">MA 200</span>}
        {showVolume && <span className="legend-volume">VOLUME</span>}
      </div>
    </section>
  );
}

function QuoteSection({ dossierData }) {
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
      {decision.status === "ok" ? (
        <>
          <div className="decision-primary">
            <div className="decision-verdict">
              <span className={`badge ${sigCls(decision.verdict)}`}>{decision.verdict}</span>
              <span className="decision-conviction">{decision.conviction != null ? Math.round(decision.conviction * 100) : "—"} CONVICTION</span>
            </div>
            <div className="decision-agreement">{aligned}/{availSignals.length} SIGNALS ALIGNED · DATA {coverage}</div>
          </div>
          {decision.thesis && <div className="decision-thesis">{decision.thesis}</div>}
          {decision.primary_risks?.[0] && (
            <div className="decision-line"><span className="lbl">KEY RISK</span>{decision.primary_risks[0]}</div>
          )}
          {decision.key_disagreement && (
            <div className="decision-line"><span className="lbl">DISAGREEMENT</span>{decision.key_disagreement}</div>
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

      <details className="paper-detail">
        <summary>QUOTE &amp; PRICE</summary>
        <div className="quote-grid">
          {rows.map(([key, value]) => <div className="quote-cell" key={key}><span>{key}</span><strong>{value}</strong></div>)}
        </div>
      </details>
      <details className="paper-detail">
        <summary>FULL REASONING</summary>
        <div className="dossier-summary">{reasonText(verdict.reason) || "No additional explanation available."}</div>
      </details>
    </div>
  );
}

function vRange(inst) {
  return function makeUrl(range) {
    if (inst.symbol) {
      return `/api/chart/${encodeURIComponent(inst.market)}/${encodeURIComponent(inst.ticker)}?range=${range}&symbol=${encodeURIComponent(inst.symbol)}`;
    }
    return `/api/chart/${encodeURIComponent(inst.market)}/${encodeURIComponent(inst.ticker)}?range=${range}`;
  };
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
      <h3>LSTM DETAIL</h3>
      <div className="dossier-rows">
        {[
          ["P(UP)", prob != null ? <span key="p" style={{ color: isUp ? "var(--bull)" : "var(--bear)" }}>{(num(prob) * 100).toFixed(1)}%</span> : "N/A"],
          ["PREDICTED RETURN", lstm.predicted_return != null ? <span key="r" className={isUp ? "up" : "down"}>{num(lstm.predicted_return) > 0 ? "+" : ""}{(num(lstm.predicted_return) * 100).toFixed(2)}%</span> : "N/A"],
          ["MODEL CONFIDENCE", lstm.model_confidence != null ? num(lstm.model_confidence).toFixed(3) : "N/A"],
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
      <div className="model-disclaimer">
        PROBABILISTIC MODELS — NOT FINANCIAL ADVICE. MODEL CONFIDENCE ≠ PROBABILITY OF PROFIT.
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
          <span className="label">SIGNAL AGREEMENT</span>
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

  const symbol = useMemo(() => {
    if (v.symbol) return v.symbol;
    const m = (markets || []).find((mm) => mm.code === v.market);
    return v.ticker + (m?.yahoo_suffix || "");
  }, [v, markets]);

  const load = useCallback((fresh = false) => {
    setError("");
    dossier({ symbol, fresh })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [symbol]);

  useEffect(() => {
    setData(null);
    setTab("overview");
    load();
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken, load]);

  // Prioritize the currently viewed stock: when its stored analysis is stale,
  // re-run it once so the dossier updates promptly. Data already on screen
  // stays visible while the fresh analysis completes.
  const staleRef = useRef(false);
  useEffect(() => {
    if (!data) return;
    if (data.stale && !staleRef.current) {
      staleRef.current = true;
      load(true);
    }
  }, [data, load]);

  const inst = data?.instrument || {};

  return (
    <>
      <button className="close" onClick={onClose}>✕</button>
      {error ? (
        <div className="error">
          <div style={{ marginBottom: 12 }}>ERROR: {error}</div>
          <button className="primary" onClick={load}>⟳ RETRY</button>
        </div>
      ) : !data ? (
        <div className="empty">LOADING DOSSIER…</div>
      ) : (
        <>
          <DossierHeader dossierData={data} v={v} />
          <div className="dossier-workspace">
            <ChartSection dossierData={data} />
            <section className="dossier-info-pane">
              <div className="dossier-tabs">
                {DOSSIER_TABS.map((t) => (
                  <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
                    {t.label}
                  </button>
                ))}
              </div>
              <div className="dossier-info-scroll">
                {tab === "overview" && <QuoteSection dossierData={data} />}
                {tab === "committee" && <CommitteeSection committee={data.committee} />}
                {tab === "bullbear" && <FactorList factors={data.factors} />}
                {tab === "model" && <ModelSection verdict={data.verdict} />}
                {tab === "news" && <NewsSection news={data.news} committee={data.committee} />}
                {tab === "risk" && (
                  <RiskSection verdict={data.verdict} symbol={inst.symbol} market={inst.market || v.market} ticker={inst.ticker || v.ticker} />
                )}
              </div>
            </section>
          </div>
        </>
      )}
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
