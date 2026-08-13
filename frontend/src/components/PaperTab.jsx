import { useCallback, useEffect, useMemo, useState } from "react";
import {
  paperPortfolio,
  paperTrades,
  paperDecisions,
  paperPerformance,
  paperEvaluate,
  paperStats,
  paperRisk,
  paperLeaderboard,
  paperEquity,
  paperEndSession,
} from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";

function money(n, digits = 2) {
  const v = Number(n || 0);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function sig(v) {
  return v === "BULL" ? "bull" : v === "BEAR" ? "bear" : "neutral";
}

function Sparkline({ points }) {
  const vals = points.map((p) => p.equity);
  if (vals.length < 2) return <div className="empty" style={{ padding: 16 }}>NO EQUITY DATA YET.</div>;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const w = 600;
  const h = 90;
  const coords = vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / range) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = vals[vals.length - 1];
  return (
    <div className="paper-equity">
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 90 }}>
        <polyline points={coords} fill="none" stroke={last >= vals[0] ? "var(--bull)" : "var(--bear)"} strokeWidth="1.5" />
      </svg>
      <div className="row"><span className="label">LATEST</span><span className="value">{money(last)}</span></div>
    </div>
  );
}

export default function PaperTab() {
  const { refreshToken, openPaperTicket } = useApp();
  const [pf, setPf] = useState(null);
  const [trades, setTrades] = useState([]);
  const [decisions, setDecisions] = useState([]);
  const [perf, setPerf] = useState(null);
  const [stats, setStats] = useState(null);
  const [risk, setRisk] = useState(null);
  const [board, setBoard] = useState(null);
  const [equity, setEquity] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError("");
    Promise.all([
      paperPortfolio(),
      paperTrades(),
      paperDecisions(),
      paperPerformance(),
      paperStats(),
      paperRisk(),
      paperLeaderboard(),
      paperEquity(),
    ])
      .then(([p, t, d, perf, st, rk, bd, eq]) => {
        setPf(p); setTrades(t); setDecisions(d); setPerf(perf);
        setStats(st); setRisk(rk); setBoard(bd); setEquity(eq);
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken, load]);

  const decById = useMemo(() => {
    const m = {};
    for (const d of decisions || []) m[d.decision_id] = d;
    return m;
  }, [decisions]);

  // Conviction at entry for each position: from the earliest order's decision.
  const entryConviction = useMemo(() => {
    const map = {};
    for (const o of trades || []) {
      const key = `${o.market}:${o.ticker}`;
      if (!o.decision_id || map[key] != null) continue;
      const snap = decById[o.decision_id];
      if (snap) map[key] = snap.conviction;
    }
    return map;
  }, [trades, decById]);

  const endSession = () => {
    setBusy(true);
    paperEndSession()
      .then(load)
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  };

  if (error && !pf) return <div className="error">ERROR: {error}</div>;
  if (!pf) return <div className="empty">LOADING PAPER PORTFOLIO…</div>;

  const openT = (p, action) => openPaperTicket({ market: p.market, ticker: p.ticker, company: p.ticker, action });

  return (
    <>
      {error && <div className="scan-warning">⚠ {error}</div>}

      <div className="landing-stats">
        <div className="landing-stat"><div className="k">PORTFOLIO VALUE</div><div className="v" style={{ color: "var(--amber)" }}>{money(pf.equity)}</div></div>
        <div className="landing-stat"><div className="k">DAY P&L</div><div className="v" style={{ color: pf.total_pnl >= 0 ? "var(--bull)" : "var(--bear)" }}>{money(pf.total_pnl)} ({num(pf.day_pct).toFixed(2)}%)</div></div>
        <div className="landing-stat"><div className="k">CASH</div><div className="v">{money(pf.cash)}</div></div>
        <div className="landing-stat"><div className="k">GROSS EXPOSURE</div><div className="v">{money(pf.gross_exposure)}</div></div>
        <div className="landing-stat"><div className="k">NET EXPOSURE</div><div className="v">{money(pf.net_exposure)}</div></div>
        <div className="landing-stat"><div className="k">OPEN POSITIONS</div><div className="v">{pf.open_positions}</div></div>
        <div className="landing-stat"><div className="k">TRADES TODAY</div><div className="v">{pf.trades_today}</div></div>
        <div className="landing-stat"><div className="k">SESSION</div><div className="v">{String(pf.session_id).slice(5)}</div></div>
      </div>

      <div className="landing-h" style={{ marginTop: 14 }}>POSITIONS
        <button className="ghost" style={{ marginLeft: 12, padding: "2px 8px", fontSize: 10 }} disabled={busy} onClick={endSession}>END SESSION (LIQUIDATE)</button>
      </div>
      {!pf.positions?.length ? (
        <div className="empty">NO OPEN POSITIONS — USE BUY/SHORT ON ANY STOCK VIEW TO OPEN A SIMULATED TRADE.</div>
      ) : (
        <div className="paper-table">
          <div className="paper-row paper-row-head">
            <span>SECURITY</span><span>DIR</span><span>QTY</span><span>ENTRY</span><span>CURRENT</span><span>MV</span><span>UNREALIZED</span><span>P&L%</span><span>CONV@ENTRY</span><span>ACTIONS</span>
          </div>
          {pf.positions.map((p) => {
            const conv = entryConviction[`${p.market}:${p.ticker}`];
            return (
              <div className="paper-row" key={`${p.market}:${p.ticker}`}>
                <span className="sym"><SecurityLink market={p.market} ticker={p.ticker}>{p.ticker}</SecurityLink></span>
                <span className={p.direction === "LONG" ? "up" : "down"}>{p.direction}</span>
                <span>{p.qty}</span>
                <span>{num(p.entry).toFixed(4)}</span>
                <span>{num(p.price).toFixed(4)}</span>
                <span>{money(p.value)}</span>
                <span style={{ color: p.unrealized >= 0 ? "var(--bull)" : "var(--bear)" }}>{money(p.unrealized)}</span>
                <span style={{ color: p.pnl_pct >= 0 ? "var(--bull)" : "var(--bear)" }}>{num(p.pnl_pct).toFixed(2)}%</span>
                <span>{conv != null ? Math.round(conv * 100) : "—"}</span>
                <span className="paper-actions">
                  <button className="paper-buy" onClick={() => openT(p, p.direction === "LONG" ? "BUY" : "CLOSE")}>{p.direction === "LONG" ? "BUY" : "CLOSE"}</button>
                  <button className="paper-short" onClick={() => openT(p, p.direction === "SHORT" ? "SHORT" : "CLOSE")}>{p.direction === "SHORT" ? "SHORT" : "CLOSE"}</button>
                  <button className="ghost" onClick={() => openT(p, p.direction === "LONG" ? "SELL" : "COVER")}>{p.direction === "LONG" ? "SELL" : "COVER"}</button>
                </span>
              </div>
            );
          })}
        </div>
      )}

      <div className="landing-h" style={{ marginTop: 14 }}>EQUITY</div>
      <Sparkline points={equity} />

      <div className="landing-h" style={{ marginTop: 14 }}>TRADE HISTORY</div>
      {!trades.length ? (
        <div className="empty">NO SIMULATED TRADES YET.</div>
      ) : (
        <div className="paper-table">
          <div className="paper-row paper-row-head"><span>TIME</span><span>ACTION</span><span>SECURITY</span><span>QTY @ PRICE</span><span>REASON</span></div>
          {[...trades].reverse().slice(0, 30).map((t) => (
            <div className="paper-row" key={t.order_id}>
              <span>{String(t.executed_at).slice(11, 19)}</span>
              <span className={t.side === "BUY" || t.side === "COVER" ? "up" : "down"}>{t.side} {t.direction || ""}</span>
              <span><SecurityLink market={t.market} ticker={t.ticker}>{t.ticker}</SecurityLink></span>
              <span>{t.quantity} @ {num(t.price).toFixed(4)}</span>
              <span className="dim">{t.reason || (t.decision_id ? `DEC ${t.decision_id}` : "manual")}</span>
            </div>
          ))}
        </div>
      )}

      <div className="landing-h" style={{ marginTop: 14 }}>DECISION PERFORMANCE
        <button className="ghost" style={{ marginLeft: 12, padding: "2px 8px", fontSize: 10 }} onClick={() => paperEvaluate().then(load)}>⟳ EVALUATE</button>
      </div>
      {perf && (
        <div className="landing-stats">
          <div className="landing-stat"><div className="k">DECISIONS</div><div className="v">{perf.decisions}</div></div>
          <div className="landing-stat"><div className="k">EVALUATED</div><div className="v">{perf.evaluated}</div></div>
          <div className="landing-stat"><div className="k">DIR ACC</div><div className="v">{perf.directional_accuracy != null ? (perf.directional_accuracy * 100).toFixed(1) + "%" : "N/A"}</div></div>
          <div className="landing-stat"><div className="k">RESEARCH CONF</div><div className="v">{perf.research_confidence_avg != null ? (perf.research_confidence_avg * 100).toFixed(0) + "%" : "N/A"}</div></div>
        </div>
      )}

      <div className="landing-h" style={{ marginTop: 14 }}>TRADE STATS (REALIZED)</div>
      {stats && stats.trades < 3 ? (
        <div className="empty" style={{ padding: 20 }}>TOO FEW CLOSED TRADES ({stats.trades}) — STATS SHOWN AFTER 3+.</div>
      ) : (
        <div className="landing-stats">
          <div className="landing-stat"><div className="k">TRADES</div><div className="v">{stats?.trades}</div></div>
          <div className="landing-stat"><div className="k">BEST</div><div className="v">{stats?.best ?? "—"}</div></div>
          <div className="landing-stat"><div className="k">WORST</div><div className="v">{stats?.worst ?? "—"}</div></div>
          <div className="landing-stat"><div className="k">WIN RATE</div><div className="v">{stats?.win_rate != null ? (stats.win_rate * 100).toFixed(1) + "%" : "—"}</div></div>
          <div className="landing-stat"><div className="k">AVG WIN / LOSS</div><div className="v">{stats?.avg_win ?? "—"} / {stats?.avg_loss ?? "—"}</div></div>
          <div className="landing-stat"><div className="k">PROFIT FACTOR</div><div className="v">{stats?.profit_factor != null && stats.profit_factor !== Infinity ? stats.profit_factor.toFixed(2) : "—"}</div></div>
          <div className="landing-stat"><div className="k">LONG / SHORT P&L</div><div className="v">{money(stats?.long_pnl)} / {money(stats?.short_pnl)}</div></div>
        </div>
      )}

      <div className="landing-h" style={{ marginTop: 14 }}>PORTFOLIO RISK</div>
      {risk?.warnings?.map((w, i) => <div className="scan-warning" key={i}>⚠ {w}</div>)}
      <div className="landing-stats">
        <div className="landing-stat"><div className="k">GROSS</div><div className="v">{money(risk?.gross_exposure)}</div></div>
        <div className="landing-stat"><div className="k">NET</div><div className="v">{money(risk?.net_exposure)}</div></div>
        <div className="landing-stat"><div className="k">LONG</div><div className="v">{money(risk?.long_exposure)}</div></div>
        <div className="landing-stat"><div className="k">SHORT</div><div className="v">{money(risk?.short_exposure)}</div></div>
        <div className="landing-stat"><div className="k">LARGEST</div><div className="v">{risk?.largest_position ? <><SecurityLink market={risk.largest_position.market} ticker={risk.largest_position.ticker}>{risk.largest_position.ticker}</SecurityLink>{risk.largest_position_pct != null ? ` ${(risk.largest_position_pct * 100).toFixed(0)}%` : ""}</> : "—"}</div></div>
        <div className="landing-stat"><div className="k">CONCENTRATION</div><div className="v">{risk?.concentration != null ? (risk.concentration * 100).toFixed(0) + "%" : "—"}</div></div>
      </div>

      <div className="landing-h" style={{ marginTop: 14 }}>PAPER TRADING LEADERBOARD</div>
      <div className="paper-table">
        <div className="paper-row paper-row-head"><span>RANK</span><span>TRADER</span><span>EQUITY</span><span>RETURN</span><span>POS</span><span>TRADES</span></div>
        {(board?.rows || []).map((r) => (
          <div className="paper-row" key={r.name}>
            <span>{r.rank}</span>
            <span>{r.name}{r.is_demo ? <span className="badge neutral" style={{ marginLeft: 6, fontSize: 8 }}>DEMO</span> : ""}</span>
            <span>{money(r.equity)}</span>
            <span style={{ color: r.return >= 0 ? "var(--bull)" : "var(--bear)" }}>{r.return > 0 ? "+" : ""}{r.return}%</span>
            <span>{r.positions ?? "—"}</span>
            <span>{r.trades ?? "—"}</span>
          </div>
        ))}
      </div>
      {board?.demo_label && <div className="team-note">{board.demo_label}</div>}
      <div className="team-note" style={{ marginTop: 8 }}>PAPER TRADING — SIMULATION ONLY. NO REAL ORDERS, NO REAL MONEY. Slippage/commission/margin are configurable simulation assumptions.</div>
    </>
  );
}