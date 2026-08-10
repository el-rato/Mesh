import { useState } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { Row } from "./ui.jsx";

function riskColor(signal) {
  return signal === "BULLISH" ? "var(--bull)" : signal === "BEARISH" ? "var(--bear)" : "var(--neutral)";
}

function ReturnBars({ results }) {
  const max = Math.max(...results.map((r) => Math.abs(r.lstm?.predicted_return || 0)), 0.0001);
  return (
    <div className="panel" style={{ cursor: "default" }}>
      <div className="row" style={{ margin: 0, marginBottom: 8 }}>
        <span className="label">LSTM PREDICTED RETURNS</span>
      </div>
      {results.map((r) => {
        const val = r.lstm?.predicted_return || 0;
        const pct = (Math.abs(val) / max) * 50;
        return (
          <div className="risk-bar-row" key={r.ticker}>
            <span className="ticker">{r.ticker}</span>
            <div className="bar">
              <div
                className="fill"
                style={{
                  left: val >= 0 ? "50%" : `${50 - pct}%`,
                  width: `${pct}%`,
                  background: val >= 0 ? "var(--bull)" : "var(--bear)",
                }}
              />
            </div>
            <span className="val" style={{ color: val >= 0 ? "var(--bull)" : "var(--bear)" }}>
              {(val * 100).toFixed(2)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

function BlBars({ results }) {
  const maxW = Math.max(...results.map((r) => Math.abs(r.black_litterman?.weight || 0)), 0.0001);
  return (
    <div className="panel" style={{ cursor: "default" }}>
      <div className="row" style={{ margin: 0, marginBottom: 8 }}>
        <span className="label">BLACK-LITTERMAN WEIGHTS</span>
      </div>
      {results.map((r) => {
        const w = r.black_litterman?.weight || 0;
        const pct = (Math.abs(w) / maxW) * 50;
        return (
          <div className="risk-bar-row" key={r.ticker}>
            <span className="ticker">{r.ticker}</span>
            <div className="bar">
              <div
                className="fill"
                style={{
                  left: w >= 0 ? "50%" : `${50 - pct}%`,
                  width: `${pct}%`,
                  background: "var(--amber)",
                }}
              />
            </div>
            <span className="val">{(w * 100).toFixed(1)}%</span>
          </div>
        );
      })}
    </div>
  );
}

export default function RiskTab() {
  const { market } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tickers, setTickers] = useState("");
  const [aversion, setAversion] = useState(3);
  const [period, setPeriod] = useState("2y");

  const run = async () => {
    setLoading(true);
    setError("");
    setData(null);
    try {
      const t = tickers.trim();
      if (!t) throw new Error("Enter at least one ticker");
      const q = `?tickers=${encodeURIComponent(t)}&risk_aversion=${aversion}&period=${encodeURIComponent(period)}`;
      const d = await fetchJSON("/api/risk" + q);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const results = data?.results || [];
  const port = results[0]?.portfolio_metrics;

  return (
    <>
      <div className="controls">
        <div className="field">
          <label>Tickers (comma-separated)</label>
          <input
            type="text"
            value={tickers}
            placeholder="AAPL, MSFT, TSLA, NVDA"
            onChange={(e) => setTickers(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Risk Aversion λ</label>
          <input
            type="number"
            value={aversion}
            step="0.5"
            min="0.5"
            max="10"
            onChange={(e) => setAversion(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Period</label>
          <input type="text" value={period} onChange={(e) => setPeriod(e.target.value)} />
        </div>
        <button className="primary" onClick={run} disabled={loading}>
          {loading ? "COMPUTING…" : "⟳ RUN RISK ANALYSIS"}
        </button>
      </div>

      {error && <div className="error">ERROR: {error}</div>}
      {loading && <div className="empty">COMPUTING LSTM + BLACK-LITTERMAN… <span className="spinner" /></div>}

      {data && !results.length && (
        <div className="empty">NO RISK DATA RETURNED FOR THOSE TICKERS.</div>
      )}

      {results.length > 0 && (
        <>
          <div className="risk-metrics">
            {port && port.sharpe !== null && (
              <>
                <div className="risk-metric">
                  <div className="k">Portfolio Sharpe</div>
                  <div className="v" style={{ color: port.sharpe > 1 ? "var(--bull)" : port.sharpe > 0.5 ? "var(--neutral)" : "var(--bear)" }}>
                    {port.sharpe.toFixed(3)}
                  </div>
                </div>
                <div className="risk-metric">
                  <div className="k">Portfolio Volatility</div>
                  <div className="v">{(port.volatility * 100).toFixed(2)}%</div>
                </div>
                <div className="risk-metric">
                  <div className="k">VaR 95%</div>
                  <div className="v">{(port.var_95 * 100).toFixed(2)}%</div>
                </div>
              </>
            )}
            <div className="risk-metric">
              <div className="k">Assets</div>
              <div className="v">{results.length}</div>
            </div>
          </div>

          <div className="grid" style={{ marginBottom: 12 }}>
            <ReturnBars results={results} />
            <BlBars results={results} />
          </div>

          <div className="grid">
            {results.map((r) => {
              const lstm = r.lstm || {};
              const bl = r.black_litterman || {};
              return (
                <div className="panel" key={r.ticker} style={{ cursor: "default" }}>
                  <div className="panel-head">
                    <div>
                      <div className="symbol">{r.ticker}</div>
                      <div className="name">RISK: {r.risk_level}</div>
                    </div>
                    <span className="badge neutral">{r.risk_level}</span>
                  </div>
                  <Row
                    k="LSTM SIGNAL"
                    v={<span style={{ color: riskColor(lstm.signal) }}>{lstm.signal || "N/A"}</span>}
                  />
                  <Row k="PRED. RETURN" v={lstm.predicted_return ? lstm.predicted_return.toFixed(4) : "N/A"} />
                  <Row k="BL WEIGHT" v={bl.weight ? (bl.weight * 100).toFixed(2) + "%" : "N/A"} />
                  <Row k="BL EXP. RETURN" v={bl.expected_return ? bl.expected_return.toFixed(4) : "N/A"} />
                  {r.portfolio_metrics && r.portfolio_metrics.sharpe !== null && (
                    <>
                      <Row k="PORTFOLIO SHARPE" v={r.portfolio_metrics.sharpe.toFixed(3)} />
                      <Row k="PORTFOLIO VOL" v={r.portfolio_metrics.volatility.toFixed(4)} />
                      <Row k="VAR 95%" v={r.portfolio_metrics.var_95.toFixed(4)} />
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
