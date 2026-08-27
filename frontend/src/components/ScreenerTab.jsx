import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJSON, screener } from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";

const PRESETS = [
  { key: "strong_bullish", label: "STRONG BULLISH" },
  { key: "bearish", label: "BEARISH SETUPS" },
  { key: "unusual_activity", label: "UNUSUAL ACTIVITY" },
  { key: "high_conviction", label: "HIGH CONVICTION" },
  { key: "signal_conflict", label: "SIGNAL CONFLICT" },
  { key: "reversals", label: "REVERSALS" },
  { key: "needs_research", label: "NO_DATA / NEEDS RESEARCH" },
];

const SORTS = [
  { key: "combined", label: "COMBINED" },
  { key: "conviction", label: "CONVICTION" },
  { key: "momentum", label: "MOMENTUM" },
  { key: "move", label: "PRICE MOVE" },
  { key: "volume", label: "VOLUME" },
  { key: "agreement", label: "AGREEMENT" },
];

const SIG_KEYS = [
  { key: "quant", label: "QUANT" },
  { key: "technical", label: "TECHNICAL" },
  { key: "news", label: "NEWS" },
  { key: "regime", label: "REGIME" },
];

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function pct(v) {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

function fmtTime(iso) {
  if (!iso) return "";
  return String(iso).slice(11, 19);
}

function Dir({ d }) {
  if (d === "BULL") return <span className="up">BULL</span>;
  if (d === "BEAR") return <span className="down">BEAR</span>;
  return <span className="dim">NEUTRAL</span>;
}

export default function ScreenerTab() {
  const { market, markets, openDrawer, screenerPrefill, setScreenerPrefill, refreshToken } = useApp();
  const [filters, setFilters] = useState({
    market: market || "",
    verdict: "",
    min_conviction: "",
    min_momentum: "",
    min_move: "",
    min_volume_ratio: "",
    signal: "",
    signal_key: "quant",
    min_agreement: "",
    regime: "",
    above_sma: "",
    research: "",
    sort: "combined",
  });
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [activePreset, setActivePreset] = useState("");
  const prefillApplied = useRef(false);

  const set = (k, v) => setFilters((f) => ({ ...f, [k]: v }));

  const buildParams = useCallback((overrides = {}) => {
    const p = { ...overrides };
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== "" && v != null) p[k] = v;
    });
    return p;
  }, [filters]);

  const load = useCallback((overrides = {}) => {
    screener(buildParams(overrides))
      .then(setRows)
      .catch((e) => setError(e.message));
  }, [buildParams]);

  // Apply a prefill (e.g. "SCREEN SIMILAR" from a notification) once.
  useEffect(() => {
    if (!screenerPrefill || prefillApplied.current) return;
    prefillApplied.current = true;
    const next = { ...filters };
    Object.entries(screenerPrefill).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") next[k] = v;
    });
    setFilters(next);
    setActivePreset("");
    load(screenerPrefill);
    setScreenerPrefill(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenerPrefill]);

  useEffect(() => {
    load();
  }, [load]);

  // Re-screen when the background refresh completes (reuses the terminal's
  // existing refresh loop — no competing polling).
  useEffect(() => {
    if (refreshToken) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  useEffect(() => {
    if (market) set("market", market);
    else set("market", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market]);

  const applyPreset = (key) => {
    setActivePreset(key === activePreset ? "" : key);
    if (key === activePreset) {
      load({ preset: "" });
      return;
    }
    load({ preset: key });
  };

  const runSearch = () => {
    const query = q.trim();
    if (!query) {
      setSearchResults(null);
      return;
    }
    fetchJSON(`/api/search?q=${encodeURIComponent(query)}&limit=8`)
      .then(setSearchResults)
      .catch(() => setSearchResults([]));
  };

  const openDossier = (r) =>
    openDrawer({
      type: "stock",
      v: {
        market: r.market,
        ticker: r.ticker,
        symbol: r.symbol || "",
        company: r.company || "",
        verdict: r.verdict,
        confidence: num(r.confidence),
        combined_score: num(r.combined_score),
        reason: ["SCREENER RESULT"],
      },
    });

  return (
    <div>
      <div className="controls" style={{ flexWrap: "wrap" }}>
        <div className="field"><label>Market</label>
          <select value={filters.market} onChange={(e) => set("market", e.target.value)}>
            <option value="">ALL</option>
            {(markets || []).map((m) => <option key={m.code} value={m.code}>{m.code}</option>)}
          </select>
        </div>
        <div className="field"><label>Verdict</label>
          <select value={filters.verdict} onChange={(e) => set("verdict", e.target.value)}>
            <option value="">ANY</option>
            <option value="BULL">BULL</option>
            <option value="BEAR">BEAR</option>
            <option value="NEUTRAL">NEUTRAL</option>
          </select>
        </div>
        <div className="field"><label>Conviction ≥</label><input type="number" step="0.05" min="0" max="1" value={filters.min_conviction} placeholder="0.60" onChange={(e) => set("min_conviction", e.target.value)} /></div>
        <div className="field"><label>Momentum ≥</label><input type="number" step="0.01" value={filters.min_momentum} placeholder="0.02" onChange={(e) => set("min_momentum", e.target.value)} /></div>
        <div className="field"><label>Price move ≥</label><input type="number" step="0.01" value={filters.min_move} placeholder="0.03" onChange={(e) => set("min_move", e.target.value)} /></div>
        <div className="field"><label>Volume ≥ (x)</label><input type="number" step="0.1" value={filters.min_volume_ratio} placeholder="1.5" onChange={(e) => set("min_volume_ratio", e.target.value)} /></div>
        <div className="field"><label>Signal</label>
          <select value={filters.signal_key} onChange={(e) => set("signal_key", e.target.value)}>
            {SIG_KEYS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </div>
        <div className="field"><label>Signal dir.</label>
          <select value={filters.signal} onChange={(e) => set("signal", e.target.value)}>
            <option value="">ANY</option>
            <option value="BULL">BULL</option>
            <option value="BEAR">BEAR</option>
          </select>
        </div>
        <div className="field"><label>Regime</label>
          <select value={filters.regime} onChange={(e) => set("regime", e.target.value)}>
            <option value="">ANY</option>
            <option value="BULL">BULL</option>
            <option value="BEAR">BEAR</option>
          </select>
        </div>
        <div className="field"><label>Above SMA</label>
          <select value={filters.above_sma} onChange={(e) => set("above_sma", e.target.value)}>
            <option value="">ANY</option>
            <option value="true">YES</option>
            <option value="false">NO</option>
          </select>
        </div>
        <div className="field"><label>Research</label>
          <select value={filters.research} onChange={(e) => set("research", e.target.value)}>
            <option value="">ANY</option>
            <option value="true">AVAILABLE</option>
            <option value="false">NONE</option>
          </select>
        </div>
        <div className="field"><label>Sort</label>
          <select value={filters.sort} onChange={(e) => set("sort", e.target.value)}>
            {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </div>
        <button className="primary" onClick={() => { setActivePreset(""); load(); }}>⟳ SCREEN</button>
      </div>

      <div className="screener-presets">
        <span className="dim">PRESETS</span>
        {PRESETS.map((p) => (
          <button key={p.key} className={`preset-btn ${activePreset === p.key ? "active" : ""}`} onClick={() => applyPreset(p.key)}>
            {p.label}
          </button>
        ))}
      </div>

      <div className="screener-search">
        <input value={q} placeholder="Search ticker / company / market — e.g. unilever" onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runSearch()} />
        <button className="ghost" onClick={runSearch}>SEARCH</button>
        {searchResults && (
          <div className="screener-search-results">
            {searchResults.length === 0 ? (
              <div className="empty" style={{ padding: 8 }}>NO MATCHES IN THE UNIVERSE.</div>
            ) : (
              searchResults.map((r) => (
                <button key={`${r.market}:${r.ticker}`} className="search-result" onClick={() => { openDossier(r); setSearchResults(null); }}>
                  {r.market}:{r.ticker} <span className="dim">{r.company || r.symbol}</span> <span className="dim">OPEN DOSSIER →</span>
                </button>
              ))
            )}
          </div>
        )}
      </div>

      {error && <div className="scan-warning">⚠ {error}</div>}
      {!rows ? (
        <div className="empty">LOADING SCREENER…</div>
      ) : rows.length === 0 ? (
        <div className="empty">NO MATCHES — ADJUST FILTERS. RUN A REFRESH / SEARCH TO EXPAND THE UNIVERSE.</div>
      ) : (
        <div className="screener-table">
          <div className="screener-row screener-head">
            <span>SECURITY</span><span>VERDICT</span><span>CONF.</span><span>MOMENTUM</span><span>MOVE</span><span>VOL</span><span>NEWS</span><span>REGIME</span><span>AGR</span>
          </div>
          {rows.map((r) => (
            <div className="screener-row" key={`${r.market}:${r.ticker}`} onClick={() => openDossier(r)} title={`Open Dossier ${r.market}:${r.ticker}`}>
              <span className="sec">
                <SecurityLink market={r.market} ticker={r.ticker}><strong>{r.ticker}</strong></SecurityLink>
                <span className="dim">{r.market} · {r.company || ""}</span>
              </span>
              <span className={r.verdict === "BULL" ? "up" : r.verdict === "BEAR" ? "down" : "dim"}>
                {r.data_status === "no_data" ? (r.warming ? "ANALYZING…" : "NO DATA") : (r.verdict || "—")}
              </span>
              <span>{r.confidence != null ? (r.confidence * 100).toFixed(0) : "—"}</span>
              <span className={num(r.momentum_20) >= 0 ? "up" : "down"}>{pct(r.momentum_20)}</span>
              <span className={num(r.price_move) >= 0 ? "up" : "down"}>{pct(r.price_move)}</span>
              <span>{r.volume_ratio != null ? r.volume_ratio.toFixed(1) + "x" : "—"}</span>
              <span><Dir d={r.signal_dir?.news} /></span>
              <span><Dir d={r.regime_direction} /></span>
              <span>{r.agreement != null ? (r.agreement * 100).toFixed(0) + "%" : "—"}</span>
            </div>
          ))}
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="team-note" style={{ marginTop: 8 }}>
          CLICK A ROW TO OPEN ITS DOSSIER. {rows.length} SECURITIES FROM THE DYNAMIC UNIVERSE — NOT A FIXED LIST.
          {rows[0].scanner_updated_at && <span style={{ marginLeft: 12 }}>UPDATED {fmtTime(rows[0].scanner_updated_at)}</span>}
        </div>
      )}
    </div>
  );
}
