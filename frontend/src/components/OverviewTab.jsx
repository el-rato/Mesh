import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { verdictBadge, verdictClass } from "./ui.jsx";

const FUNCTIONS = [
  { key: "watchlist", fn: "F2", label: "WATCHLIST", desc: "Your saved tickers and their dossiers" },
  { key: "funds", fn: "F3", label: "HEDGE FUNDS", desc: "13F buy/sell moves from top funds" },
  { key: "indexes", fn: "F4", label: "INDEXES", desc: "Benchmark indices with live charts" },
  { key: "lstm", fn: "F5", label: "LSTM", desc: "Train & review LSTM price predictions" },
  { key: "scanner", fn: "F6", label: "SCANNER", desc: "Screen the analyzed universe by signal" },
];

function Stat({ k, v, cls }) {
  return (
    <div className="landing-stat">
      <div className="k">{k}</div>
      <div className="v" style={cls ? { color: cls } : undefined}>{v}</div>
    </div>
  );
}

export default function OverviewTab() {
  const { market, markets, indexes, refreshToken, setTab } = useApp();
  const [verdicts, setVerdicts] = useState([]);
  const [error, setError] = useState("");

  const loadVerdicts = useCallback(() => {
    setError("");
    fetchJSON("/api/verdicts")
      .then((d) => setVerdicts(Object.values(d)))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    loadVerdicts();
    const t = setInterval(loadVerdicts, 30000);
    return () => clearInterval(t);
  }, [loadVerdicts]);

  useEffect(() => {
    if (refreshToken) loadVerdicts();
  }, [refreshToken]);

  const bulls = verdicts.filter((v) => verdictClass(v.verdict) === "bull").length;
  const bears = verdicts.filter((v) => verdictClass(v.verdict) === "bear").length;
  const neut = verdicts.length - bulls - bears;
  const topMovers = [...(indexes || [])]
    .sort((a, b) => (b.change_pct || 0) - (a.change_pct || 0))
    .slice(0, 6);
  const avgConf = verdicts.length
    ? ((verdicts.reduce((s, v) => s + (v.confidence || 0), 0) / verdicts.length) * 100).toFixed(1)
    : "--";

  return (
    <div className="landing">
      <div className="landing-h" style={{ fontSize: 15 }}>
        OVERVIEW <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>· {market || "ALL MARKETS"}</span>
      </div>

      {error && (
        <div className="error">
          <div style={{ marginBottom: 10 }}>ERROR: {error}</div>
          <button className="primary" onClick={loadVerdicts}>⟳ RETRY</button>
        </div>
      )}

      <div className="landing-stats">
        <Stat k="STOCKS SCORED" v={verdicts.length || "—"} cls="var(--amber)" />
        <Stat k="BULL" v={bulls} cls="var(--bull)" />
        <Stat k="BEAR" v={bears} cls="var(--bear)" />
        <Stat k="NEUTRAL" v={neut} />
        <Stat k="AVG CONFIDENCE" v={verdicts.length ? `${avgConf}%` : "--"} cls="var(--blue)" />
      </div>

      <div className="landing-cols">
        <div className="landing-col">
          <div className="landing-h">MARKET SNAPSHOT</div>
          <div className="grid landing-index-grid">
            {topMovers.map((s) => {
              const up = (s.change_pct || 0) >= 0;
              return (
                <div key={s.symbol} className={`panel ${up ? "bull" : "bear"}`} onClick={() => setTab("indexes")}>
                  <div className="panel-head">
                    <div>
                      <div className="symbol" style={{ fontSize: 13 }}>{s.name}</div>
                      <div className="name">{s.market} · {s.symbol}</div>
                    </div>
                    <span className={`badge ${up ? "bull" : "bear"}`}>
                      {up ? "+" : ""}{(s.change_pct * 100).toFixed(2)}%
                    </span>
                  </div>
                  <div className="row">
                    <span className="label">CLOSE</span>
                    <span className="value">{Number(s.close).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                </div>
              );
            })}
          </div>
          {!topMovers.length && <div className="empty">RUN <code>indexes</code> OR CLICK REFRESH TO LOAD THE INDEX TAPE.</div>}
        </div>

        <div className="landing-col">
          <div className="landing-h">TOP BULL / BEAR</div>
          {verdicts.length ? (
            <div className="grid landing-verdict-grid">
              {[...verdicts]
                .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
                .slice(0, 8)
                .map((v) => (
                  <div key={`${v.market}:${v.ticker}`} className={`panel ${verdictClass(v.verdict)}`}>
                    <div className="panel-head">
                      <div>
                        <div className="symbol" style={{ fontSize: 13 }}>{v.ticker}</div>
                        <div className="name">{v.market}</div>
                      </div>
                      {verdictBadge(v)}
                    </div>
                    <div className="row">
                      <span className="label">CONFIDENCE</span>
                      <span className="value">{(v.confidence * 100).toFixed(0)}%</span>
                    </div>
                    <div className="conf-bar">
                      <span
                        style={{
                          width: (v.confidence * 100).toFixed(0) + "%",
                          background:
                            v.verdict === "BULL"
                              ? "var(--bull)"
                              : v.verdict === "BEAR"
                              ? "var(--bear)"
                              : "var(--neutral)",
                        }}
                      />
                    </div>
                  </div>
                ))}
            </div>
          ) : (
            <div className="empty">NO VERDICTS YET — REFRESH DATA OR OPEN SCANNER.</div>
          )}
        </div>
      </div>

      <div className="landing-h">QUICK NAVIGATION</div>
      <div className="landing-funcs">
        {FUNCTIONS.map((f) => (
          <button key={f.key} className="landing-func" onClick={() => setTab(f.key)}>
            <span className="fn">{f.fn}</span>
            <span className="lbl">{f.label}</span>
            <span className="desc">{f.desc}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
