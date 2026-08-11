import { useEffect, useMemo, useState } from "react";
import { fetchJSON, CHART_RANGES, rangeLabel, dossier } from "../api.js";
import { useApp } from "../App.jsx";
import PriceChart from "./PriceChart.jsx";
import { verdictBadge, reasonText } from "./ui.jsx";

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

function DossierHeader({ dossierData, v, onRefresh, refreshing }) {
  const verdict = dossierData.verdict || {};
  const inst = dossierData.instrument || {};
  const conf = verdict.confidence == null ? "N/A" : `${(num(verdict.confidence) * 100).toFixed(0)}%`;
  const score = verdict.combined_score == null ? "N/A" : `${num(verdict.combined_score) > 0 ? "+" : ""}${num(verdict.combined_score).toFixed(3)}`;
  return (
    <div className="dossier-header">
      <div>
        <div className="symbol-lg">{inst.ticker || v.ticker}</div>
        <div className="dossier-company">{inst.company || ""} · {inst.exchange || inst.market || v.market}</div>
      </div>
      <div className="dossier-header-right">
        <span className="dossier-mkt">{inst.market || v.market} · {inst.quote_type || "EQUITY"}</span>
        {verdictBadge(verdict)}
        <button className="live" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? "⟳ ANALYZING…" : "⟳ LIVE VERDICT"}
        </button>
      </div>
      <div className="dossier-header-meta">
        CONFIDENCE <strong>{conf}</strong> · SCORE <strong>{score}</strong> · AGREEMENT <strong>{String(verdict.signal_agreement || "unknown").toUpperCase()}</strong>
      </div>
      {!dossierData.fresh && dossierData.computed_at && (
        <div className="dossier-stale">STORED SNAPSHOT · {String(dossierData.computed_at).slice(0, 19).replace("T", " ")}</div>
      )}
      {dossierData.fresh && dossierData.computed_at && (
        <div className="dossier-stale fresh">LIVE · {String(dossierData.computed_at).slice(0, 19).replace("T", " ")}</div>
      )}
    </div>
  );
}

function ChartSection({ dossierData }) {
  const inst = dossierData.instrument || {};
  const price = dossierData.verdict?.price || {};
  const [range, setRange] = useState("1mo");
  const [chartType, setChartType] = useState("candlestick");
  const [showVolume, setShowVolume] = useState(true);
  const [showSma50, setShowSma50] = useState(true);
  const [showSma200, setShowSma200] = useState(false);
  const rangeHost = vRange(inst);
  return (
    <section className="dossier-chart-pane">
      <div className="chart-pane-head">
        <div>
          <span>PRICE / OHLCV</span>
          <strong className="chart-current">{price.close == null ? "N/A" : num(price.close).toFixed(2)}</strong>
        </div>
        <div className="chart-controls">
          <select className="chart-type-select" value={chartType} onChange={(event) => setChartType(event.target.value)} aria-label="Chart type">
            <option value="candlestick">CANDLES</option>
            <option value="ohlc">OHLC</option>
            <option value="line">LINE</option>
            <option value="area">AREA</option>
          </select>
          <div className="chart-range-bar">
            {CHART_RANGES.map((r) => (
              <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>
                {rangeLabel(r)}
              </button>
            ))}
          </div>
          <div className="indicator-bar">
            <button className={showVolume ? "active" : ""} onClick={() => setShowVolume((v) => !v)}>VOL</button>
            <button className={showSma50 ? "active" : ""} onClick={() => setShowSma50((v) => !v)}>SMA 50</button>
            <button className={showSma200 ? "active" : ""} onClick={() => setShowSma200((v) => !v)}>SMA 200</button>
          </div>
        </div>
      </div>
      <div className="chart-workspace">
        <PriceChart url={rangeHost(range)} height={560} chartType={chartType} showVolume={showVolume} showSma50={showSma50} showSma200={showSma200} showMomentum refreshKey={dossierData.computed_at} />
      </div>
      <div className="chart-legend"><span className="legend-price">PRICE</span>{showSma50 && <span className="legend-sma50">SMA 50</span>}{showSma200 && <span className="legend-sma200">SMA 200</span>}{showVolume && <span className="legend-volume">VOLUME</span>}</div>
    </section>
  );
}

function QuoteSection({ dossierData }) {
  const verdict = dossierData.verdict || {};
  const price = verdict.price || {};
  const rows = [
    ["CLOSE", num(price.close).toFixed(2)], ["OPEN", num(price.open).toFixed(2)],
    ["HIGH", num(price.high).toFixed(2)], ["LOW", num(price.low).toFixed(2)],
    ["VOLUME", num(price.volume, 0).toLocaleString()], ["MOMENTUM 20D", num(price.momentum_20).toFixed(2)],
    ["RSI 14", num(price.rsi_14).toFixed(0)], ["SMA 50", num(price.sma_50).toFixed(2)],
    ["SMA 200", num(price.sma_200).toFixed(2)],
  ];
  return (
    <div className="quote-section">
      <h3>STOCK INFORMATION</h3>
      <div className="quote-grid">
        {rows.map(([key, value]) => <div className="quote-cell" key={key}><span>{key}</span><strong>{value}</strong></div>)}
      </div>
      <h3>VERDICT</h3>
      <div className="dossier-summary">{reasonText(verdict.reason) || "No additional explanation available."}</div>
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

function CommitteeSection({ committee }) {
  const confidence = committee.confidence == null ? "N/A" : `${(Number(committee.confidence) * 100).toFixed(0)}%`;
  const score = committee.score == null ? "N/A" : `${committee.score > 0 ? "+" : ""}${Number(committee.score).toFixed(2)}`;
  return (
    <div className="team">
      <div className="team-head">
        <span>SIGNAL</span><span>DIRECTION</span><span>SCORE</span><span>CONF.</span><span>WEIGHT</span><span>CONTRIB.</span>
      </div>
      {(committee.signals || []).map((s) => (
        <div className={`team-row ${s.available ? "" : "na"}`} key={s.key}>
          <span className="team-label">{s.label}</span>
          <StateBadge state={s.state} />
          <span className="team-value">{s.score == null ? "N/A" : `${s.score > 0 ? "+" : ""}${s.score.toFixed(2)}`}</span>
          <span className="team-value">{s.confidence == null ? "N/A" : `${(s.confidence * 100).toFixed(0)}%`}</span>
          <span className="team-value">{(s.weight * 100).toFixed(0)}%</span>
          <span className="team-value">{s.contribution == null ? "N/A" : `${s.contribution > 0 ? "+" : ""}${s.contribution.toFixed(2)}`}</span>
        </div>
      ))}
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
        MISSING SIGNALS ARE EXCLUDED · INSTITUTIONAL = AGGREGATED 13F ACTIVITY WHEN AVAILABLE
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
  const lstm = verdict.lstm || {};
  const prob = lstm.probability_up;
  const isUp = num(prob, 0.5) >= 0.5;
  const metrics = lstm.metrics || {};
  const rows = [
    ["P(UP)", prob != null ? <span key="p" style={{ color: isUp ? "var(--bull)" : "var(--bear)" }}>{(num(prob) * 100).toFixed(1)}%</span> : "N/A"],
    ["PREDICTED RETURN", lstm.predicted_return != null ? <span key="r" className={isUp ? "up" : "down"}>{num(lstm.predicted_return) > 0 ? "+" : ""}{(num(lstm.predicted_return) * 100).toFixed(2)}%</span> : "N/A"],
    ["MODEL CONFIDENCE", lstm.model_confidence != null ? num(lstm.model_confidence).toFixed(3) : "N/A"],
    ["LSTM SCORE", num(lstm.score).toFixed(3)],
    ["FORECAST HORIZON", verdict.forecast_horizon || "1 trading day"],
    ["MODEL VERSION", lstm.model_version || "N/A"],
    ...Object.entries(metrics).map(([k, v2]) => [k.toUpperCase().replace(/_/g, " "), v2]),
  ];
  return (
    <div className="model">
      <h3>LSTM PRICE MODEL — OUTPUT</h3>
      <div className="dossier-rows">
        {rows.map(([k, val]) => (
          <div className="row" key={k}>
            <span className="label">{k}</span>
            <span className="value">{val}</span>
          </div>
        ))}
      </div>
      <div className="conf-bar" style={{ margin: "8px 0" }}>
        <span style={{ width: Math.round(num(prob, 0) * 100) + "%", background: isUp ? "var(--bull)" : "var(--bear)" }} />
      </div>
      <div className="model-disclaimer">
        PROBABILISTIC MODEL — NOT FINANCIAL ADVICE. MODEL CONFIDENCE ≠ PROBABILITY OF PROFIT.
        PREDICTIONS ARE ESTIMATES BASED ON HISTORICAL PRICE PATTERNS AND CAN BE WRONG.
      </div>
    </div>
  );
}

/* ---------------- News ---------------- */

function NewsSection({ news }) {
  if (!news || !news.length) return <div className="empty">NO HEADLINES FOR THIS TICKER.</div>;
  return (
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
  const { markets } = useApp();
  const [tab, setTab] = useState("overview");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const symbol = useMemo(() => {
    if (v.symbol) return v.symbol;
    const m = (markets || []).find((mm) => mm.code === v.market);
    return v.ticker + (m?.yahoo_suffix || "");
  }, [v, markets]);

  const load = (fresh) => {
    setError("");
    if (fresh) setRefreshing(true);
    dossier({ symbol, fresh })
      .then((d) => {
        setData(d);
        setRefreshing(false);
      })
      .catch((e) => {
        setError(e.message);
        setRefreshing(false);
      });
  };

  useEffect(() => {
    setData(null);
    setTab("overview");
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  const inst = data?.instrument || {};

  return (
    <>
      <button className="close" onClick={onClose}>✕</button>
      {error ? (
        <div className="error">
          <div style={{ marginBottom: 12 }}>ERROR: {error}</div>
          <button className="primary" onClick={() => load(false)}>⟳ RETRY</button>
        </div>
      ) : !data ? (
        <div className="empty">LOADING DOSSIER…</div>
      ) : (
        <>
          <DossierHeader dossierData={data} v={v} onRefresh={() => load(true)} refreshing={refreshing} />
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
                {tab === "news" && <NewsSection news={data.news} />}
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
