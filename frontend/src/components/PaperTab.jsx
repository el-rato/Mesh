import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  paperPortfolios,
  paperCreatePortfolio,
  paperConfigurePortfolio,
  paperDeletePortfolio,
  paperResetPortfolio,
  paperPortfolio,
  paperOrders,
  paperPositions,
  paperCancelOrder,
  paperTrades,
  paperStats,
  paperRisk,
  paperLeaderboard,
  paperEquity,
  paperEndSession,
  paperSettle,
  paperDecisions,
  paperPerformance,
  paperEvaluate,
} from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";
import NumberInput from "./NumberInput.jsx";

function money(n, digits = 2) {
  const v = Number(n || 0);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function amount(n, currency = "USD") {
  return num(n).toLocaleString(currency === "INR" ? "en-IN" : "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
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

const STATUS_CLASS = {
  pending: "neutral", partial: "neutral", filled: "up", cancelled: "dim", rejected: "down",
};

export default function PaperTab({ marketRows = [] }) {
  const { refreshToken, openPaperTicket, market, markets } = useApp();
  const symbolInput = useRef(null);
  const activeIdRef = useRef("");
  const [portfolios, setPortfolios] = useState([]);
  const [activeId, setActiveId] = useState("");
  const [pf, setPf] = useState(null);
  const [orders, setOrders] = useState([]);
  const [trades, setTrades] = useState([]);
  const [stats, setStats] = useState(null);
  const [risk, setRisk] = useState(null);
  const [board, setBoard] = useState(null);
  const [equity, setEquity] = useState([]);
  const [decisions, setDecisions] = useState([]);
  const [perf, setPerf] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBalance, setNewBalance] = useState("");
  const [newCurrency, setNewCurrency] = useState("");
  const [showNewPortfolio, setShowNewPortfolio] = useState(false);
  const [startingBalance, setStartingBalance] = useState("");
  const [startingCurrency, setStartingCurrency] = useState("");
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState("holdings");
  const [tradeMarket, setTradeMarket] = useState("");
  const [tradeTicker, setTradeTicker] = useState("");

  const load = useCallback(() => {
    setError("");
    paperPortfolios()
      .then(async (ps) => {
        const list = ps?.length ? ps : [await paperPortfolio()].map((p) => ({ id: p.portfolio_id, name: p.name, currency: p.currency }));
        setPortfolios(list);
        const active = list[0]?.id || "";
        setActiveId((cur) => {
          const preferred = cur || window.localStorage.getItem("paperPortfolioId");
          return list.some((p) => p.id === preferred) ? preferred : active;
        });
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (activeId) {
      window.localStorage.setItem("paperPortfolioId", activeId);
      setPf(null);
      setOrders([]);
      setTrades([]);
    }
  }, [activeId]);

  const reloadPortfolio = useCallback(() => {
    if (!activeId) return;
    Promise.all([
      paperPortfolio(activeId),
      paperOrders(activeId),
      paperPositions(activeId),
      paperTrades(activeId),
      paperStats(activeId),
      paperRisk(activeId),
      paperLeaderboard(activeId),
      paperEquity(activeId),
      paperDecisions(),
      paperPerformance(),
    ])
      .then(([p, o, pos, tr, st, rk, bd, eq, dec, perf2]) => {
        if (activeIdRef.current !== activeId) return;
        setPf(p); setOrders(o || []); setTrades(tr || []); setStats(st);
        setRisk(rk); setBoard(bd); setEquity(eq || []); setDecisions(dec || []); setPerf(perf2);
      })
      .catch((e) => setError(e.message));
  }, [activeId]);

  useEffect(() => {
    reloadPortfolio();
    const t = setInterval(reloadPortfolio, 30000);
    return () => clearInterval(t);
  }, [reloadPortfolio]);

  useEffect(() => {
    if (refreshToken) reloadPortfolio();
  }, [refreshToken, reloadPortfolio]);

  useEffect(() => {
    if (pf?.portfolio_id !== activeId) return;
    setStartingBalance(String(pf.starting_cash));
    setStartingCurrency(pf.currency);
    // Refreshes for the same account should not overwrite an amount being edited.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, pf?.portfolio_id]);

  const decById = useMemo(() => {
    const m = {};
    for (const d of decisions || []) m[d.decision_id] = d;
    return m;
  }, [decisions]);

  const entryConviction = useMemo(() => {
    const map = {};
    for (const t of trades || []) {
      const key = `${t.market}:${t.ticker}`;
      if (!t.decision_id || map[key] != null) continue;
      const snap = decById[t.decision_id];
      if (snap) map[key] = snap.conviction;
    }
    return map;
  }, [trades, decById]);

  function createPortfolio() {
    const name = newName.trim();
    const balance = Number(newBalance);
    if (!name || !Number.isFinite(balance) || balance <= 0) {
      setError("Enter a portfolio name and a positive starting balance.");
      return;
    }
    setBusy(true);
    paperCreatePortfolio({ name, balance, currency: newCurrency || defaultCurrency })
      .then((p) => {
        setNewName("");
        setNewBalance("");
        setShowNewPortfolio(false);
        load();
        setActiveId(p.id);
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }

  function removePortfolio() {
    if (!activeId) return;
    setBusy(true);
    paperDeletePortfolio(activeId)
      .then(() => {
        setActiveId("");
        load();
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }

  function resetPortfolio() {
    if (!activeId) return;
    setBusy(true);
    paperResetPortfolio(activeId)
      .then(() => reloadPortfolio())
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }

  function endSession() {
    if (!activeId) return;
    setBusy(true);
    paperEndSession(activeId)
      .then(() => reloadPortfolio())
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }

  function settleIntraday() {
    setBusy(true);
    paperSettle(activeId)
      .then(() => reloadPortfolio())
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }

  function cancelOrder(orderId) {
    paperCancelOrder(orderId).then(reloadPortfolio).catch((e) => setError(e.message));
  }

  function configurePortfolio(e) {
    e.preventDefault();
    const balance = Number(startingBalance);
    if (!Number.isFinite(balance) || balance <= 0) {
      setError("Enter a positive starting balance.");
      return;
    }
    setBusy(true);
    setError("");
    paperConfigurePortfolio(activeId, balance, startingCurrency || pf.currency)
      .then(() => { load(); reloadPortfolio(); })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }

  function startTrade(e) {
    e.preventDefault();
    const ticker = tradeTicker.trim().toUpperCase();
    const exchange = tradeMarket || market || markets?.[0]?.code;
    if (!ticker || !exchange) return;
    openPaperTicket({ market: exchange, ticker, company: ticker, action: "BUY", portfolio_id: activeId });
    setTradeTicker("");
  }

  const selectedMarket = tradeMarket || market || markets?.[0]?.code || "";
  activeIdRef.current = activeId;
  const defaultCurrency = markets?.find((m) => m.code === selectedMarket)?.currency || "USD";
  const currencyOptions = [...new Set([pf?.currency, defaultCurrency, ...(markets || []).map((m) => m.currency)].filter(Boolean))];
  const canConfigure = Boolean(pf && !orders.length && !trades.length && !pf.positions?.length);
  const hasCapitalChange = pf && (Number(startingBalance) !== num(pf.starting_cash) || startingCurrency !== pf.currency);
  const marketPicks = marketRows.filter((row) => row.market === selectedMarket && row.ticker && Number(row.close) > 0).slice(0, 4);

  if (loading) return <div className="empty">Loading paper portfolio…</div>;
  if (error && !pf && !portfolios.length) return <div className="error">ERROR: {error}</div>;

  const openT = (p, side) =>
    openPaperTicket({ market: p.market, ticker: p.ticker, company: p.ticker, action: side, portfolio_id: activeId });


  return (
    <>
      {error && <div className="scan-warning">⚠ {error}</div>}

      <div className="paper-dashboard-head">
        <div><div className="paper-eyebrow">PRACTICE ACCOUNT</div><h2>Paper trading</h2><p>Try a trade, follow your positions, and learn as you go.</p></div>
        <div className="paper-account-switch">
          <label htmlFor="paper-portfolio-select">Portfolio</label>
          <select id="paper-portfolio-select" value={activeId} onChange={(e) => setActiveId(e.target.value)}>
            {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.currency}</option>)}
          </select>
          <button type="button" className="ghost" onClick={() => setShowNewPortfolio((open) => !open)}>{showNewPortfolio ? "Close" : "+ New portfolio"}</button>
        </div>
      </div>

      {showNewPortfolio && <form className="paper-new-portfolio" onSubmit={(e) => { e.preventDefault(); createPortfolio(); }}>
        <div><strong>Create a portfolio</strong><span>Choose your own starting amount and currency.</span></div>
        <label>Portfolio name<input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. My practice account" autoFocus /></label>
        <div className="paper-number-field"><label htmlFor="paper-new-balance">Starting balance</label><NumberInput id="paper-new-balance" stepperLabel="starting balance" stepperStep={1000} min="0.01" step="0.01" value={newBalance} onChange={(e) => setNewBalance(e.target.value)} placeholder="Enter amount" /></div>
        <label>Currency<select value={newCurrency || defaultCurrency} onChange={(e) => setNewCurrency(e.target.value)}>{currencyOptions.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <button className="primary" disabled={busy || !newName.trim() || !(Number(newBalance) > 0)}>Create</button>
      </form>}

      <div className="paper-workspace-hero">
        <section className="paper-account-hero" aria-label="Portfolio balance">
          <div className="paper-hero-label"><span>PORTFOLIO VALUE</span><span className="paper-sim-badge">SIMULATED</span></div>
          <div className="paper-hero-balance">{pf ? amount(pf.equity, pf.currency) : "—"}<small>{pf?.currency || ""}</small></div>
          <div className="paper-hero-metrics">
            <div><span>Available cash</span><strong>{pf ? amount(pf.cash, pf.currency) : "—"}</strong></div>
            <div><span>Total return</span><strong className={num(pf?.total_pnl) >= 0 ? "up" : "down"}>{pf ? money(pf.total_pnl) : "—"}</strong></div>
            <div><span>Positions</span><strong>{pf?.open_positions ?? "—"}</strong></div>
          </div>
          {canConfigure ? (
            <form className="paper-capital-form" onSubmit={configurePortfolio}>
              <div><strong>Make it your account</strong><span>Set the starting amount before your first order.</span></div>
              <div className="paper-number-field"><label htmlFor="paper-starting-balance">Starting balance</label><NumberInput id="paper-starting-balance" stepperLabel="starting balance" stepperStep={1000} min="0.01" step="0.01" value={startingBalance} onChange={(e) => setStartingBalance(e.target.value)} /></div>
              <label>Currency<select value={startingCurrency || pf.currency} onChange={(e) => setStartingCurrency(e.target.value)}>{currencyOptions.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
              <button type="submit" disabled={busy || !hasCapitalChange || !(Number(startingBalance) > 0)}>Save balance</button>
            </form>
          ) : <div className="paper-capital-locked">Starting balance is locked after the first order. Use <button type="button" onClick={() => setShowNewPortfolio(true)}>New portfolio</button> for a fresh account.</div>}
        </section>

        <section className="paper-trade-card" aria-label="Find a stock to trade">
          <div className="paper-trade-card-head"><span className="paper-eyebrow">YOUR NEXT MOVE</span><h3>{pf?.open_positions ? "Find your next trade" : "Start with a stock"}</h3><p>Choose a market and enter a symbol. Review the price, quantity, and order type before placing a simulated order.</p></div>
          <form className="paper-trade-search" onSubmit={startTrade}>
            <label>Market<select aria-label="Market" value={selectedMarket} onChange={(e) => setTradeMarket(e.target.value)}>{markets?.map((m) => <option key={m.code} value={m.code}>{m.code} · {m.currency}</option>)}</select></label>
            <label>Stock symbol<input ref={symbolInput} aria-label="Stock symbol" value={tradeTicker} onChange={(e) => setTradeTicker(e.target.value)} placeholder="Type a ticker" autoComplete="off" /></label>
            <button className="primary" disabled={!activeId || !tradeTicker.trim() || !selectedMarket}>Review trade <span aria-hidden="true">→</span></button>
          </form>
          {marketPicks.length > 0 && <div className="paper-market-picks"><div className="paper-market-picks-title">From {selectedMarket} market</div>{marketPicks.map((row) => <button type="button" key={`${row.market}:${row.ticker}`} onClick={() => openPaperTicket({ market: row.market, ticker: row.ticker, company: row.company || row.ticker, action: "BUY", portfolio_id: activeId })}><strong>{row.ticker}</strong><span>{amount(row.close, defaultCurrency)}</span><small className={num(row.change_pct) >= 0 ? "up" : "down"}>{num(row.change_pct) > 0 ? "+" : ""}{(num(row.change_pct) * 100).toFixed(2)}%</small><span aria-hidden="true">→</span></button>)}</div>}
        </section>
      </div>

      <nav className="paper-view-tabs" aria-label="Paper trading views">
        {[['holdings', 'Holdings'], ['orders', 'Orders'], ['history', 'History'], ['insights', 'Insights']].map(([key, label]) =>
          <button key={key} type="button" className={view === key ? "active" : ""} aria-current={view === key ? "page" : undefined} onClick={() => setView(key)}>{label}{key === "holdings" ? ` (${pf?.positions?.length || 0})` : key === "orders" ? ` (${orders.length})` : ""}</button>
        )}
      </nav>

      {view === "holdings" && <>

      <div className="paper-section-heading"><div><span className="paper-eyebrow">YOUR PORTFOLIO</span><h3>Holdings & positions</h3></div>{pf?.positions?.length > 0 && <span>{pf.positions.length} open</span>}</div>
      {!pf?.positions?.length ? (
        <div className="paper-empty-positions"><div className="paper-empty-mark">↗</div><div><strong>Nothing here yet. Your first trade changes that.</strong><p>Choose a stock, set a quantity, and see how your idea plays out without using real money.</p></div><button type="button" onClick={() => symbolInput.current?.focus()}>Find a stock <span aria-hidden="true">→</span></button></div>
      ) : (
        <div className="paper-table paper-holdings-table">
          <div className="paper-row paper-row-head">
            <span>Stock</span><span>Shares</span><span>Avg. price</span><span>Current</span><span>Value</span><span>Returns</span><span>Type</span><span>Action</span>
          </div>
          {pf.positions.map((p) => {
            const conv = entryConviction[`${p.market}:${p.ticker}`];
            return (
              <div className="paper-row" key={`${p.market}:${p.ticker}:${p.side}`}>
                <span className="sym"><SecurityLink market={p.market} ticker={p.ticker}>{p.ticker}</SecurityLink><small>{p.market} · {p.side}</small></span>
                <span>{p.quantity}</span>
                <span>{num(p.entry_price).toFixed(4)}</span>
                <span>{num(p.current_price).toFixed(4)}</span>
                <span>{amount(p.value)}</span>
                <span style={{ color: p.unrealized_pnl >= 0 ? "var(--bull)" : "var(--bear)" }}>{money(p.unrealized_pnl)}</span>
                <span className="dim">{p.product === "CNC" ? "Delivery" : "Intraday"}{conv != null ? ` · ${Math.round(conv * 100)}%` : ""}</span>
                <span className="paper-actions">
                  <button className="paper-buy" onClick={() => openT(p, p.side === "short" ? "COVER" : "BUY")}>{p.side === "short" ? "Cover" : "Buy"}</button>
                  <button className="paper-short" onClick={() => openT(p, "CLOSE")}>Exit</button>
                </span>
              </div>
            );
          })}
        </div>
      )}
      </>}

      {view === "orders" && <>
      <div className="landing-h" style={{ marginTop: 14 }}>Orders</div>
      {!orders.length ? (
        <div className="empty">No orders yet. Place a practice trade to get started.</div>
      ) : (
        <div className="paper-table paper-orders-table">
          <div className="paper-row paper-row-head"><span>TIME</span><span>SIDE</span><span>TYPE</span><span>SECURITY</span><span>QTY</span><span>FILLED</span><span>AVG</span><span>STATUS</span><span></span></div>
          {orders.slice(0, 40).map((o) => (
            <div className="paper-row" key={o.id}>
              <span>{String(o.created_at).slice(11, 19)}</span>
              <span className={o.side === "buy" ? "up" : "down"}>{o.side.toUpperCase()}</span>
              <span className="dim">{o.order_type.toUpperCase()}</span>
              <span><SecurityLink market={o.market} ticker={o.ticker}>{o.ticker}</SecurityLink></span>
              <span>{o.quantity}</span>
              <span>{num(o.filled_qty)}</span>
              <span>{o.avg_price != null ? num(o.avg_price).toFixed(4) : "—"}</span>
              <span className={STATUS_CLASS[o.status] || "dim"}>{o.status.toUpperCase()}{o.reduce_only ? " · RO" : ""}</span>
              <span>{o.status === "pending" || o.status === "partial" ? <button className="ghost" style={{ padding: "2px 6px", fontSize: 10 }} onClick={() => cancelOrder(o.id)}>CANCEL</button> : ""}</span>
            </div>
          ))}
        </div>
      )}
      </>}

      {view === "history" && <>
      <div className="landing-h" style={{ marginTop: 14 }}>Portfolio value over time</div>
      <Sparkline points={equity} />

      <div className="landing-h" style={{ marginTop: 14 }}>Completed trades</div>
      {!trades.length ? (
        <div className="empty">NO SIMULATED TRADES YET.</div>
      ) : (
        <div className="paper-table paper-trades-table">
          <div className="paper-row paper-row-head"><span>TIME</span><span>SIDE</span><span>SECURITY</span><span>QTY @ PRICE</span><span>FEE</span><span>P&L</span></div>
          {trades.slice(0, 40).map((t) => (
            <div className="paper-row" key={t.id}>
              <span>{String(t.timestamp).slice(11, 19)}</span>
              <span className={t.side === "buy" ? "up" : "down"}>{t.side.toUpperCase()}</span>
              <span><SecurityLink market={t.market} ticker={t.ticker}>{t.ticker}</SecurityLink></span>
              <span>{t.quantity} @ {num(t.price).toFixed(4)}</span>
              <span className="dim">{num(t.fee).toFixed(2)}</span>
              <span style={{ color: t.pnl >= 0 ? "var(--bull)" : "var(--bear)" }}>{money(t.pnl)}</span>
            </div>
          ))}
        </div>
      )}
      </>}

      {view === "insights" && <>
      <div className="landing-h" style={{ marginTop: 14 }}>TRADE STATS (REALIZED)</div>
      {!stats || stats.total_trades < 3 ? (
        <div className="empty" style={{ padding: 20 }}>TOO FEW CLOSED TRADES ({stats?.total_trades || 0}) — STATS SHOWN AFTER 3+.</div>
      ) : (
        <div className="landing-stats">
          <div className="landing-stat"><div className="k">TRADES</div><div className="v">{stats.total_trades}</div></div>
          <div className="landing-stat"><div className="k">WIN RATE</div><div className="v">{stats.win_rate != null ? (stats.win_rate * 100).toFixed(1) + "%" : "—"}</div></div>
          <div className="landing-stat"><div className="k">PROFIT FACTOR</div><div className="v">{stats.profit_factor != null && stats.profit_factor !== Infinity ? stats.profit_factor.toFixed(2) : "—"}</div></div>
          <div className="landing-stat"><div className="k">GROSS P/L</div><div className="v">{money(stats.gross_profit)} / {money(stats.gross_loss)}</div></div>
          <div className="landing-stat"><div className="k">AVG WIN / LOSS</div><div className="v">{money(stats.avg_win)} / {money(stats.avg_loss)}</div></div>
          <div className="landing-stat"><div className="k">LARGEST W/L</div><div className="v">{money(stats.largest_win)} / {money(stats.largest_loss)}</div></div>
          <div className="landing-stat"><div className="k">TOTAL FEES</div><div className="v">{money(stats.total_fees)}</div></div>
          <div className="landing-stat"><div className="k">TODAY P&L</div><div className="v" style={{ color: stats.today_pnl >= 0 ? "var(--bull)" : "var(--bear)" }}>{money(stats.today_pnl)}</div></div>
        </div>
      )}

      <div className="landing-h" style={{ marginTop: 14 }}>PORTFOLIO RISK</div>
      {risk?.warnings?.map((w, i) => <div className="scan-warning" key={i}>⚠ {w}</div>)}
      <div className="landing-stats">
        <div className="landing-stat"><div className="k">GROSS</div><div className="v">{money(risk?.gross_exposure)}</div></div>
        <div className="landing-stat"><div className="k">NET</div><div className="v">{money(risk?.net_exposure)}</div></div>
        <div className="landing-stat"><div className="k">LONG / SHORT</div><div className="v">{money(risk?.long_exposure)} / {money(risk?.short_exposure)}</div></div>
        <div className="landing-stat"><div className="k">CONCENTRATION</div><div className="v">{risk?.concentration != null ? (risk.concentration * 100).toFixed(0) + "%" : "—"}</div></div>
      </div>

      <div className="landing-h" style={{ marginTop: 14 }}>DECISION PERFORMANCE
        <button className="ghost" style={{ marginLeft: 12, padding: "2px 8px", fontSize: 10 }} onClick={() => paperEvaluate().then(reloadPortfolio)}>⟳ EVALUATE</button>
      </div>
      {perf && (
        <div className="landing-stats">
          <div className="landing-stat"><div className="k">DECISIONS</div><div className="v">{perf.decisions}</div></div>
          <div className="landing-stat"><div className="k">EVALUATED</div><div className="v">{perf.evaluated}</div></div>
          <div className="landing-stat"><div className="k">DIR ACC</div><div className="v">{perf.directional_accuracy != null ? (perf.directional_accuracy * 100).toFixed(1) + "%" : "N/A"}</div></div>
          <div className="landing-stat"><div className="k">RESEARCH CONF</div><div className="v">{perf.research_confidence_avg != null ? (perf.research_confidence_avg * 100).toFixed(0) + "%" : "N/A"}</div></div>
        </div>
      )}

      <div className="landing-h" style={{ marginTop: 14 }}>PAPER TRADING LEADERBOARD</div>
      <div className="paper-table paper-leaderboard-table">
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
      </>}

      <details className="paper-settings">
        <summary>Session controls</summary>
        <div className="controls">
          <button className="ghost" disabled={busy || !activeId} onClick={settleIntraday}>Settle intraday</button>
          <button className="ghost" disabled={busy || !activeId} onClick={endSession}>End session & liquidate</button>
          <button className="ghost" disabled={busy || !activeId} onClick={resetPortfolio}>Reset portfolio</button>
          <button className="ghost" disabled={busy || !activeId} onClick={removePortfolio}>Delete portfolio</button>
        </div>
      </details>
      <div className="team-note" style={{ marginTop: 8 }}>Paper trading is a simulation. No real orders or money are used.</div>
    </>
  );
}
