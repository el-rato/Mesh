import { useEffect, useState } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import { verdictBadge, verdictClass } from "./ui.jsx";

const FUNCTIONS = [
  { key: "verdicts", fn: "F1", label: "VERDICTS", desc: "Bull/Bear verdicts for every tracked stock" },
  { key: "watchlist", fn: "F2", label: "WATCHLIST", desc: "Your saved tickers and their verdicts" },
  { key: "discover", fn: "F3", label: "DISCOVER", desc: "Scan feeds for new bullish tickers" },
  { key: "risk", fn: "F4", label: "RISK", desc: "LSTM + Black-Litterman risk analysis" },
  { key: "funds", fn: "F5", label: "HEDGE FUNDS", desc: "13F buy/sell moves from top funds" },
  { key: "indexes", fn: "F6", label: "INDEXES", desc: "Benchmark indices with live charts" },
];

function Stat({ k, v, cls }) {
  return (
    <div className="landing-stat">
      <div className="k">{k}</div>
      <div className="v" style={cls ? { color: cls } : undefined}>{v}</div>
    </div>
  );
}

export default function LandingPage() {
  const { setTab } = useApp();
  const [indexes, setIndexes] = useState([]);
  const [verdicts, setVerdicts] = useState([]);
  const [markets, setMarkets] = useState([]);

  useEffect(() => {
    fetchJSON("/api/indexes").then(setIndexes).catch(() => {});
    fetchJSON("/api/verdicts").then((d) => setVerdicts(Object.values(d))).catch(() => {});
    fetchJSON("/api/markets").then(setMarkets).catch(() => {});
  }, []);

  const bulls = verdicts.filter((v) => verdictClass(v.verdict) === "bull").length;
  const bears = verdicts.filter((v) => verdictClass(v.verdict) === "bear").length;
  const neut = verdicts.length - bulls - bears;
  const topMovers = [...indexes].sort((a, b) => b.change_pct - a.change_pct).slice(0, 6);
  const avgConf = verdicts.length
    ? ((verdicts.reduce((s, v) => s + (v.confidence || 0), 0) / verdicts.length) * 100).toFixed(1)
    : "--";

  return (
    <div className="landing">
      <div className="landing-hero">
        <div className="landing-title">STOCK VERDICT TERMINAL</div>
        <div className="landing-sub">
          NEWS → SENTIMENT → VERDICT. {markets.length} MARKETS TRACKED · {verdicts.length} STOCKS SCORED
        </div>
      </div>

      <div className="landing-stats">
        <Stat k="STOCKS" v={verdicts.length} cls="var(--amber)" />
        <Stat k="BULL" v={bulls} cls="var(--bull)" />
        <Stat k="BEAR" v={bears} cls="var(--bear)" />
        <Stat k="NEUTRAL" v={neut} />
        <Stat k="AVG CONFIDENCE" v={`${avgConf}%`} cls="var(--blue)" />
      </div>

      <div className="landing-cols">
        <div className="landing-col">
          <div className="landing-h">MARKET SNAPSHOT</div>
          <div className="grid landing-index-grid">
            {topMovers.map((s) => {
              const up = s.change_pct >= 0;
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
          {indexes.length === 0 && <div className="empty">RUN <code>indexes</code> OR CLICK REFRESH TO LOAD INDEX TAPE.</div>}
        </div>

        <div className="landing-col">
          <div className="landing-h">TOP BULL / BEAR</div>
          {verdicts.length ? (
            <div className="grid landing-verdict-grid">
              {[...verdicts]
                .sort((a, b) => b.confidence - a.confidence)
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
            <div className="empty">NO VERDICTS YET — RUN <code>stock-alert-app verdict</code>.</div>
          )}
        </div>
      </div>

      <div className="landing-h">FUNCTIONS</div>
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
