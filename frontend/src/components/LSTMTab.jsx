import { useEffect, useState, useCallback } from "react";
import { fetchJSON, lstmBatchPredict, lstmTrain } from "../api.js";
import { useApp } from "../App.jsx";
import PriceChart from "./PriceChart.jsx";
import { Row } from "./ui.jsx";

function signalClass(signal) {
  if (signal === "BULL") return "bull";
  if (signal === "BEAR") return "bear";
  return "neutral";
}

function signalBadge(signal) {
  return <span className={`badge ${signalClass(signal)}`}>{signal || "—"}</span>;
}

function probBar(prob) {
  const pct = ((prob || 0) * 100).toFixed(0);
  const up = prob >= 0.5;
  return (
    <div className="conf-bar" title={`P(Up): ${pct}%`}>
      <span
        style={{
          width: pct + "%",
          background: up ? "var(--bull)" : "var(--bear)",
        }}
      />
    </div>
  );
}

export default function LSTMTab() {
  const { market, markets, refreshToken, openDrawer } = useApp();
  const [predictions, setPredictions] = useState(null);
  const [loading, setLoading] = useState(false);
  const [training, setTraining] = useState(false);
  const [error, setError] = useState("");
  const [selectedSymbols, setSelectedSymbols] = useState([]);
  const [windowSize, setWindowSize] = useState(30);
  const [period, setPeriod] = useState("2y");
  const [batchSize, setBatchSize] = useState(32);
  const [epochs, setEpochs] = useState(25);
  const [lr, setLr] = useState(0.001);

  const symbolsForMarket = useCallback(() => {
    const all = [];
    (markets || []).forEach((m) => {
      const suffix = m.yahoo_suffix || "";
      (m.tickers || []).forEach((sym) => {
        all.push({ symbol: sym, suffix, market: m.code });
      });
    });
    if (!market) return all;
    return all.filter((s) => s.market === market);
  }, [market, markets]);

  const fetchPredictions = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const syms = symbolsForMarket();
      const symbols = syms.map((s) => s.symbol + s.suffix);
      if (!symbols.length) {
        setPredictions({});
        setLoading(false);
        return;
      }
      const data = await lstmBatchPredict(symbols, period, windowSize);
      const mapped = {};
      Object.entries(data).forEach(([sym, pred]) => {
        const meta = syms.find((s) => (s.symbol + s.suffix) === sym);
        if (meta) {
          mapped[sym] = { ...pred, ticker: meta.symbol, market: meta.market };
        }
      });
      setPredictions(mapped);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, [symbolsForMarket, period, windowSize]);

  useEffect(() => {
    fetchPredictions();
  }, [fetchPredictions, refreshToken]);

  const handleTrain = async (symbol, marketCode) => {
    setTraining(true);
    setError("");
    try {
      const m = markets.find((mm) => mm.code === marketCode);
      const fullSymbol = symbol + (m?.yahoo_suffix || "");
      await lstmTrain(fullSymbol, period, windowSize, epochs, batchSize, lr);
      await fetchPredictions();
    } catch (e) {
      setError(e.message);
    }
    setTraining(false);
  };

  const handleTrainAll = async () => {
    setTraining(true);
    setError("");
    try {
      const syms = symbolsForMarket();
      for (const s of syms) {
        const fullSymbol = s.symbol + s.suffix;
        try {
          await lstmTrain(fullSymbol, period, windowSize, epochs, batchSize, lr);
        } catch (e) {
          console.error(`Failed to train ${fullSymbol}: ${e.message}`);
        }
      }
      await fetchPredictions();
    } catch (e) {
      setError(e.message);
    }
    setTraining(false);
  };

  const list = predictions ? Object.values(predictions) : [];
  const sorted = [...list].sort((a, b) => (b.probability_up || 0) - (a.probability_up || 0));
  const bullCount = list.filter((p) => p.signal === "BULL").length;
  const bearCount = list.filter((p) => p.signal === "BEAR").length;
  const neutralCount = list.filter((p) => p.signal === "NEUTRAL").length;

  return (
    <div>
      <div className="controls" style={{ marginBottom: 14 }}>
        <div className="field">
          <label>Lookback Window</label>
          <select value={windowSize} onChange={(e) => setWindowSize(Number(e.target.value))}>
            <option value={20}>20 days</option>
            <option value={30}>30 days</option>
            <option value={60}>60 days</option>
            <option value={90}>90 days</option>
          </select>
        </div>
        <div className="field">
          <label>Period</label>
          <select value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option value="1y">1 year</option>
            <option value="2y">2 years</option>
            <option value="5y">5 years</option>
          </select>
        </div>
        <div className="field">
          <label>Epochs</label>
          <input
            type="number"
            value={epochs}
            onChange={(e) => setEpochs(Number(e.target.value))}
            min={1}
            max={100}
          />
        </div>
        <div className="field">
          <label>Batch Size</label>
          <input
            type="number"
            value={batchSize}
            onChange={(e) => setBatchSize(Number(e.target.value))}
            min={8}
            max={128}
          />
        </div>
        <div className="field">
          <label>Learning Rate</label>
          <input
            type="number"
            value={lr}
            onChange={(e) => setLr(Number(e.target.value))}
            step={0.0001}
            min={0.0001}
            max={0.01}
          />
        </div>
        <button className="primary" onClick={fetchPredictions} disabled={loading}>
          {loading ? "LOADING…" : "⟳ PREDICT"}
        </button>
        <button className="ghost" onClick={handleTrainAll} disabled={training}>
          {training ? "TRAINING…" : "⚙ TRAIN ALL"}
        </button>
      </div>

      {error && (
        <div className="error">
          <div style={{ marginBottom: 12 }}>ERROR: {error}</div>
          <button className="primary" onClick={fetchPredictions}>⟳ RETRY</button>
        </div>
      )}

      <div className="landing-stats" style={{ marginBottom: 14 }}>
        <div className="landing-stat">
          <div className="k">BULL PREDICTIONS</div>
          <div className="v" style={{ color: "var(--bull)" }}>{bullCount}</div>
        </div>
        <div className="landing-stat">
          <div className="k">BEAR PREDICTIONS</div>
          <div className="v" style={{ color: "var(--bear)" }}>{bearCount}</div>
        </div>
        <div className="landing-stat">
          <div className="k">NEUTRAL</div>
          <div className="v" style={{ color: "var(--neutral)" }}>{neutralCount}</div>
        </div>
        <div className="landing-stat">
          <div className="k">TOTAL</div>
          <div className="v">{list.length}</div>
        </div>
      </div>

      {loading && !predictions ? (
        <div className="empty">LOADING LSTM PREDICTIONS…</div>
      ) : !sorted.length ? (
        <div className="empty">
          NO LSTM PREDICTIONS YET. CLICK "TRAIN ALL" TO BUILD THE MODEL SET.
        </div>
      ) : (
        <div className="grid">
          {sorted.map((p) => {
            const isUp = (p.probability_up || 0) >= 0.5;
            const retColor = isUp ? "var(--bull)" : "var(--bear)";
            const key = `${p.market}:${p.ticker}`;
            return (
              <div
                key={key}
                className={`panel ${signalClass(p.signal)}`}
                onClick={() =>
                  openDrawer({
                    type: "stock",
                    v: {
                      market: p.market,
                      ticker: p.ticker,
                      verdict: p.signal,
                      confidence: p.confidence,
                      combined_score: p.predicted_return || 0,
                      reason: [
                        `LSTM Model — Pred Ret: ${(p.predicted_return * 100).toFixed(2)}%, P(Up): ${((p.probability_up || 0) * 100).toFixed(1)}%`,
                      ],
                    },
                  })
                }
              >
                <div className="panel-head">
                  <div>
                    <div className="symbol">{p.ticker}</div>
                    <div className="name">{p.market}</div>
                  </div>
                  {signalBadge(p.signal)}
                </div>

                <Row k="PRED RET" v={
                  <span style={{ color: retColor, fontWeight: 600 }}>
                    {((p.predicted_return || 0) * 100).toFixed(2)}%
                  </span>
                } />

                <div style={{ marginTop: 4 }}>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 2 }}>
                    P(UP) {((p.probability_up || 0) * 100).toFixed(1)}%
                  </div>
                  {probBar(p.probability_up)}
                </div>

                <Row k="CONFIDENCE" v={`${((p.confidence || 0) * 100).toFixed(0)}%`} />

                <div style={{ marginTop: 8, height: 60 }}>
                  <PriceChart
                    url={`/api/chart/${encodeURIComponent(p.market)}/${encodeURIComponent(p.ticker)}?range=1mo`}
                    height={60}
                    hideAxes
                  />
                </div>

                <button
                  className="ghost"
                  style={{ width: "100%", marginTop: 8, fontSize: 11 }}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleTrain(p.ticker, p.market);
                  }}
                  disabled={training}
                >
                  ⚙ RETRAIN
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
