import { useEffect, useState } from "react";
import { fetchJSON } from "../api.js";

const FEATURES = [
  {
    t: "LSTM PRICE MODELS",
    d: "Sequential deep-learning forecasts for every tracked ticker — predicted return, probability of an up-move, and directional accuracy from real price history.",
  },
  {
    t: "MULTI-SIGNAL VERDICTS",
    d: "A single decision engine weighs LSTM, technical indicators, and news sentiment into one BULL / BEAR / NEUTRAL call with an explainable reason trail.",
  },
  {
    t: "NEWS SENTIMENT",
    d: "Headlines are scored per-article (FinBERT, lexicon, or LSTM) and aggregated with source-reliability and recency weighting.",
  },
  {
    t: "TECHNICAL INDICATORS",
    d: "Momentum, RSI, and 50/200-day moving-average structure contribute a discrete technical score alongside the model signal.",
  },
  {
    t: "INSTITUTIONAL 13F",
    d: "Hedge fund holdings and quarterly changes pulled from SEC EDGAR, showing where the largest funds are positioning.",
  },
  {
    t: "QUANT RISK ANALYTICS",
    d: "Black-Litterman portfolio optimization and risk metrics (Sharpe, volatility, VaR) computed against LSTM-predicted returns.",
  },
];

const CAPABILITIES = [
  "Instant search across every tracked ticker and company",
  "Live benchmark index tape and charting",
  "Watchlist with on-demand verdict computation",
  "Feed-driven discovery of new bullish tickers",
  "LLM-powered deep-dive analysis and agent recommendations",
  "Reddit sentiment scanning for retail crowd positioning",
];

function Stat({ k, v }) {
  return (
    <div className="lp-stat">
      <div className="k">{k}</div>
      <div className="v">{v ?? "—"}</div>
    </div>
  );
}

export default function Landing({ onEnter }) {
  const [markets, setMarkets] = useState(null);
  const [verdicts, setVerdicts] = useState(null);

  useEffect(() => {
    fetchJSON("/api/markets")
      .then(setMarkets)
      .catch(() => setMarkets([]));
    fetchJSON("/api/verdicts")
      .then((d) => setVerdicts(Object.values(d)))
      .catch(() => setVerdicts([]));
  }, []);

  const tickerCount =
    markets && markets.length
      ? markets.reduce((s, m) => s + (m.tickers || []).length, 0)
      : null;
  const verdictCount = verdicts ? verdicts.length : null;
  const avgConf =
    verdicts && verdicts.length
      ? (
          (verdicts.reduce((s, v) => s + (v.confidence || 0), 0) /
            verdicts.length) *
          100
        ).toFixed(1)
      : null;

  return (
    <div className="lp">
      <header className="lp-top">
        <div className="lp-brand">
          SV<span> | STOCK VERDICT</span>
        </div>
        <div className="lp-ver">QUANTITATIVE MARKET ANALYSIS TERMINAL</div>
      </header>

      <section className="lp-hero">
        <h1>STOCK VERDICT TERMINAL</h1>
        <p className="lp-tagline">
          Deep-learning price models, technical indicators, and news sentiment —
          combined into a single explainable BULL / BEAR / NEUTRAL verdict for
          every stock you track.
        </p>
        <button className="primary lp-cta" onClick={onEnter}>
          ENTER TERMINAL ⟶
        </button>
        <div className="lp-hero-sub">
          Free tier · No account · Real market data
        </div>
      </section>

      <section className="lp-stats">
        <Stat k="MARKETS TRACKED" v={markets ? markets.length : null} />
        <Stat k="TICKERS WATCHED" v={tickerCount} />
        <Stat k="STOCKS SCORED" v={verdictCount} />
        <Stat k="AVG VERDICT CONFIDENCE" v={avgConf ? `${avgConf}%` : null} />
      </section>

      <section className="lp-section">
        <div className="lp-h">WHAT THE TERMINAL DOES</div>
        <div className="lp-grid">
          {FEATURES.map((f) => (
            <div className="lp-card" key={f.t}>
              <div className="lp-card-t">{f.t}</div>
              <p>{f.d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-h">KEY CAPABILITIES</div>
        <ul className="lp-list">
          {CAPABILITIES.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </section>

      <footer className="lp-foot">
        <button className="primary lp-cta" onClick={onEnter}>
          ENTER TERMINAL ⟶
        </button>
        <div className="lp-sub">
          SV · STOCK VERDICT — quantitative analysis, not financial advice.
        </div>
      </footer>
    </div>
  );
}
