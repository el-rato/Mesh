import { useEffect, useState, useCallback, useMemo } from "react";
import { scanner } from "../api.js";
import { useApp } from "../App.jsx";
import AddToPortfolioButton from "./AddToPortfolioButton.jsx";
import SecurityLink from "./SecurityLink.jsx";
import { verdictBadge, verdictClass, RefreshStatus } from "./ui.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function dirToken(v) {
  let d = null;
  if (v && typeof v === "object") d = v.direction;
  else if (typeof v === "number") d = v > 0.05 ? "BULL" : v < -0.05 ? "BEAR" : "NEUTRAL";
  if (d === "BULL") return <span className="up">↑</span>;
  if (d === "BEAR") return <span className="down">↓</span>;
  return <span className="dim">–</span>;
}

function agreement(r) {
  const sigs = (r.committee && r.committee.signals) || [];
  return `${sigs.filter((s) => s.available).length}/5`;
}

function freshness(r) {
  if (!r.updated_at) return "—";
  const s = Math.max(0, Math.round((Date.now() - new Date(r.updated_at).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

function status(r) {
  if (r.data_status === "no_data") return { label: "NO DATA", cls: "down" };
  if (r.data_status === "stale") return { label: "STALE", cls: "stale" };
  return { label: "OK", cls: "dim" };
}

function priceText(r) {
  const c = r.close;
  if (c == null || !Number.isFinite(Number(c))) return "NO DATA";
  return Number(c).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

const VERDICT_PILLS = [
  { key: "", label: "ALL" },
  { key: "BULL", label: "BULL" },
  { key: "BEAR", label: "BEAR" },
  { key: "NEUTRAL", label: "NEUTRAL" },
];

const SORTS = [
  { key: "combined", label: "COMBINED" },
  { key: "confidence", label: "CONFIDENCE" },
  { key: "momentum", label: "MOMENTUM" },
  { key: "prop_up", label: "P(UP)" },
];

export default function ScannerTab() {
  const { market, refreshToken, refreshStatus, openDrawer, openPaperTicket } = useApp();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [verdict, setVerdict] = useState("");
  const [sort, setSort] = useState("combined");
  const [query, setQuery] = useState("");

  const load = useCallback(() => {
    const params = { market: market || "", limit: 1000, verdict, sort };
    scanner(params)
      .then((next) => {
        setRows(next);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, [market, verdict, sort]);

  useEffect(() => {
    load();
    const t = setInterval(load, 45000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        (r.ticker || "").toLowerCase().includes(q) ||
        (r.company || "").toLowerCase().includes(q) ||
        (r.market || "").toLowerCase().includes(q) ||
        (r.symbol || "").toLowerCase().includes(q)
    );
  }, [rows, query]);

  if (!rows && error)
    return (
      <div className="error">
        <div style={{ marginBottom: 12 }}>ERROR: {error}</div>
        <button className="primary" onClick={load}>⟳ RETRY</button>
      </div>
    );
  if (!rows) return <div className="empty">LOADING SCANNER…</div>;

  return (
    <div className="scanner-tab">
      {/* Clean command bar (Fincept screener style) */}
      <div className="scanner-commandbar">
        <div className="scanner-titlebox">
          <div className="scanner-title">SCANNER</div>
          <div className="scanner-subtitle dim">{market || "ALL MARKETS"} · {filtered.length} RESULTS</div>
        </div>
        <input
          className="scanner-search"
          placeholder="Search ticker, company…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="scanner-pills">
          {VERDICT_PILLS.map((p) => (
            <button
              key={p.key}
              className={`scanner-pill ${verdict === p.key ? "active" : ""}`}
              onClick={() => setVerdict(p.key)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <select className="scanner-sort" value={sort} onChange={(e) => setSort(e.target.value)} title="Sort by">
          {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <RefreshStatus status={refreshStatus} />
        <button className="primary scanner-refresh" onClick={load}>⟳</button>
      </div>

      {error && rows && (
        <div className="scan-warning">⚠ SCAN FAILED · SHOWING LAST KNOWN DATA — {error}</div>
      )}

      {!filtered.length ? (
        <div className="empty">NO MATCHES — ADJUST SEARCH OR RUN A PRICE FETCH TO EXPAND THE UNIVERSE.</div>
      ) : (
        <div className="grid">
          {filtered.map((r) => (
            <div
              key={`${r.market}:${r.ticker}`}
              className={`panel ${verdictClass(r.verdict)}`}
              onClick={() =>
                openDrawer({
                  type: "stock",
                  v: {
                    market: r.market,
                    ticker: r.ticker,
                    symbol: r.symbol,
                    company: r.company || "",
                    verdict: r.verdict,
                    confidence: num(r.confidence),
                    combined_score: num(r.combined_score),
                    reason: ["SCANNER RESULT"],
                  },
                })
              }
            >
              <div className="panel-head">
                <div>
                  <SecurityLink market={r.market} ticker={r.ticker} className="symbol">{r.ticker}</SecurityLink>
                  <div className="name">{r.market} · <SecurityLink market={r.market} ticker={r.ticker} className="link-inline">{r.company || r.symbol}</SecurityLink></div>
                </div>
                <div>
                  {verdictBadge(r)}
                  <span className="conv-pct">{(num(r.confidence) * 100).toFixed(0)}%</span>
                </div>
              </div>
              <div className="scanner-primary">
                <span className="lbl">PRICE</span><span className="val">{priceText(r)}</span>
                <span className="lbl">QUANT</span><span className="val">{dirToken(r.quantitative)}</span>
                <span className="lbl">TECH</span><span className="val">{dirToken(r.technical && num(r.technical.score))}</span>
                <span className="lbl">NEWS</span><span className="val">{dirToken(r.news_available ? num(r.news_score) : null)}</span>
              </div>
              <div className="scanner-meta">
                <span>AGREEMENT {agreement(r)}</span>
                <span>FRESH {freshness(r)}</span>
                {(() => { const s = status(r); return <span className={s.cls}>{s.label}</span>; })()}
                {r.data_status === "stale" && r.price_as_of && (
                  <span className="stale-flag" title={`last valid data ${r.price_as_of}`}>AS_OF {r.price_as_of.slice(0, 10)}</span>
                )}
              </div>
              <div className="row paper-actions" onClick={(e) => e.stopPropagation()}>
                <AddToPortfolioButton market={r.market} ticker={r.ticker} company={r.company} />
                <button className="paper-buy" onClick={() => openPaperTicket({ market: r.market, ticker: r.ticker, symbol: r.symbol, company: r.company, action: "buy" })}>BUY</button>
                <button className="paper-short" onClick={() => openPaperTicket({ market: r.market, ticker: r.ticker, symbol: r.symbol, company: r.company, action: "sell" })}>SELL</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
