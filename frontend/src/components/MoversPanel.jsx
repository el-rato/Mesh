import { useEffect, useState, useCallback, useMemo } from "react";
import { screener } from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function pct(v) {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

const CRITERIA = [
  { key: "price_move", label: "PRICE MOVE", sort: "move" },
  { key: "momentum_20", label: "MOMENTUM 20D", sort: "momentum" },
  { key: "volume_ratio", label: "VOLUME", sort: "volume" },
  { key: "confidence", label: "CONVICTION", sort: "conviction" },
  { key: "combined_score", label: "COMBINED", sort: "combined" },
];

const TOP_N_OPTIONS = [5, 10, 15];

export default function MoversPanel({ title = "TOP MOVERS" }) {
  const { market, refreshToken, openDrawer } = useApp();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [criterion, setCriterion] = useState("price_move");
  const [topN, setTopN] = useState(10);

  const load = useCallback(() => {
    setError("");
    screener({ market: market || "", sort: "combined", limit: 200 })
      .then((data) => setRows(data || []))
      .catch((e) => setError(e.message));
  }, [market]);

  useEffect(() => {
    load();
    const t = setInterval(load, 45000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken, load]);

  const { gainers, losers } = useMemo(() => {
    if (!rows) return { gainers: [], losers: [] };
    const getVal = (r) => {
      if (criterion === "confidence") return num(r.confidence);
      if (criterion === "combined_score") return num(r.combined_score);
      return num(r[criterion]);
    };
    const sorted = [...rows].sort((a, b) => getVal(b) - getVal(a));
    return {
      gainers: sorted.slice(0, topN),
      losers: sorted.reverse().slice(0, topN),
    };
  }, [rows, criterion, topN]);

  const openDossier = (r) =>
    openDrawer({
      type: "stock",
      v: {
        market: r.market, ticker: r.ticker, symbol: r.symbol || "", company: r.company || "",
        verdict: r.verdict, confidence: num(r.confidence), combined_score: num(r.combined_score),
        reason: ["MOVER"],
      },
    });

  const MoverRow = ({ r, positive }) => {
    const val = criterion === "confidence" ? num(r.confidence) : criterion === "combined_score" ? num(r.combined_score) : num(r[criterion]);
    const display = criterion === "volume_ratio" ? (val > 0 ? `${val.toFixed(1)}x` : "—") : pct(val);
    return (
      <div className="mover-row" onClick={() => openDossier(r)} title={`Open Dossier ${r.market}:${r.ticker}`}>
        <SecurityLink market={r.market} ticker={r.ticker} className="mover-sym">{r.ticker}</SecurityLink>
        <span className="mover-mkt dim">{r.market}</span>
        <span className={`mover-val ${positive ? "up" : "down"}`}>{display}</span>
        <span className={`badge ${r.verdict === "BULL" ? "bull" : r.verdict === "BEAR" ? "bear" : "neutral"}`}>{r.verdict || "NO_DATA"}</span>
      </div>
    );
  };

  return (
    <div className="movers-panel">
      <div className="movers-head">
        <span className="movers-title">{title}</span>
        <div className="movers-toggles">
          {CRITERIA.map((c) => (
            <button
              key={c.key}
              className={`criterion-btn ${criterion === c.key ? "active" : ""}`}
              onClick={() => setCriterion(c.key)}
              title={`Rank by ${c.label}`}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="movers-topn">
          {TOP_N_OPTIONS.map((n) => (
            <button key={n} className={`topn-btn ${topN === n ? "active" : ""}`} onClick={() => setTopN(n)}>
              {n}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="scan-warning">⚠ {error}</div>}
      {!rows ? (
        <div className="empty">LOADING MOVERS…</div>
      ) : rows.length === 0 ? (
        <div className="empty">NO DATA — RUN A REFRESH TO POPULATE THE UNIVERSE.</div>
      ) : (
        <div className="movers-cols">
          <div className="movers-col">
            <div className="movers-col-head bull">▲ GAINERS</div>
            {gainers.map((r) => <MoverRow key={`${r.market}:${r.ticker}`} r={r} positive />)}
          </div>
          <div className="movers-col">
            <div className="movers-col-head bear">▼ LOSERS</div>
            {losers.map((r) => <MoverRow key={`${r.market}:${r.ticker}`} r={r} positive={false} />)}
          </div>
        </div>
      )}
    </div>
  );
}
