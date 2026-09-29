import { useEffect, useState } from "react";
import { paperQuote, paperPortfolio, paperPortfolios, paperPlaceOrder, paperDecisions } from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";
import NumberInput from "./NumberInput.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

const ORDER_TYPES = [
  { key: "market", label: "MARKET" },
  { key: "limit", label: "LIMIT" },
  { key: "stop", label: "STOP" },
  { key: "stop_limit", label: "STOP-LIMIT" },
];

function sideFromAction(action, position) {
  const a = String(action || "").toUpperCase();
  if (a === "BUY" || a === "COVER") return "buy";
  if (a === "SELL" || a === "SHORT") return "sell";
  if (a === "CLOSE" && position) return position.side === "long" ? "sell" : "buy";
  return "buy";
}

export default function PaperOrderPanel({ ticket, onClose }) {
  const { refreshAll } = useApp();
  const [quote, setQuote] = useState(null);
  const [pf, setPf] = useState(null);
  const [portfolios, setPortfolios] = useState([]);
  const [portfolioId, setPortfolioId] = useState("");
  const [side, setSide] = useState("buy");
  const [orderType, setOrderType] = useState("market");
  const [qty, setQty] = useState("");
  const [price, setPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [reduceOnly, setReduceOnly] = useState(false);
  const [product, setProduct] = useState("CNC");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [decision, setDecision] = useState(ticket?.decision || null);

  const market = ticket?.market;
  const ticker = ticket?.ticker;
  const company = ticket?.company || ticker || "";
  useEffect(() => {
    if (!market || !ticker) return;
    setError("");
    setQuote(null);
    setPf(null);
    setQty("");
    setReduceOnly(false);
    setProduct("CNC");
    setPortfolioId("");
    setSide(sideFromAction(ticket?.action, null));
    paperQuote(market, ticker).then((q) => { setQuote(q); if (q.price == null) setOrderType("limit"); }).catch((e) => setError(e.message));
    paperPortfolios().then(async (list) => {
      const accounts = list?.length ? list : [await paperPortfolio()].map((p) => ({ id: p.portfolio_id, name: p.name, currency: p.currency }));
      setPortfolios(accounts);
      const preferred = ticket?.portfolio_id || window.localStorage.getItem("paperPortfolioId");
      setPortfolioId(accounts.some((p) => p.id === preferred) ? preferred : accounts[0].id);
    }).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, ticker, ticket?.portfolio_id]);

  useEffect(() => {
    if (!market || !ticker || !portfolioId) return;
    let active = true;
    setPf(null);
    paperPortfolio(portfolioId).then((p) => {
      if (!active) return;
      setPf(p);
      window.localStorage.setItem("paperPortfolioId", p.portfolio_id);
      const pos = (p.positions || []).find((x) => x.market === market && x.ticker === ticker);
      const nextSide = sideFromAction(ticket?.action, pos);
      setSide(nextSide);
      const closing = pos && ((nextSide === "sell" && pos.side === "long") || (nextSide === "buy" && pos.side === "short"));
      setReduceOnly(Boolean(closing));
      setProduct(pos?.product || (nextSide === "sell" ? "MIS" : "CNC"));
      setQty(String(ticket?.action || "").toUpperCase() === "CLOSE" && pos ? String(pos.quantity) : "");
    }).catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, ticker, portfolioId]);

  useEffect(() => {
    if (!ticket?.decision || !market || !ticker) return;
    paperDecisions(market, ticker)
      .then((ds) => {
        if (ds && ds[0]) {
          try {
            const j = JSON.parse(ds[0].decision_json || "{}");
            setDecision({ decision_id: ds[0].decision_id, ...j });
          } catch (e) {
            /* ignore */
          }
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, ticker]);

  if (!ticket) return null;

  const position = (pf?.positions || []).find((x) => x.market === market && x.ticker === ticker);
  const priceNum = quote?.price;
  const q = num(qty, 0);
  const refPrice = orderType === "limit" || orderType === "stop_limit" ? num(price, 0) : orderType === "stop" ? num(stopPrice, 0) : num(priceNum, 0);
  const estValue = refPrice > 0 ? q * refPrice : null;
  const closing = position && ((side === "sell" && position.side === "long") || (side === "buy" && position.side === "short"));

  function confirm() {
    if (!Number.isInteger(q) || q <= 0) {
      setError("Enter a whole number of shares.");
      return;
    }
    if (reduceOnly && (!closing || q > num(position.quantity))) {
      setError("Closing quantity exceeds your open position.");
      return;
    }
    if (side === "sell" && product === "CNC" && (!position || position.side !== "long" || q > num(position.quantity))) {
      setError("Delivery sells need owned shares. Choose Intraday to short sell.");
      return;
    }
    if (orderType === "limit" && num(price, 0) <= 0) {
      setError("limit order requires a price");
      return;
    }
    if ((orderType === "stop" || orderType === "stop_limit") && num(stopPrice, 0) <= 0) {
      setError("stop order requires a stop price");
      return;
    }
    if (orderType === "stop_limit" && num(price, 0) <= 0) {
      setError("Stop-limit order requires a limit price.");
      return;
    }
    setBusy(true);
    setError("");
    paperPlaceOrder({
      portfolio_id: portfolioId,
      market,
      ticker,
      side,
      order_type: orderType,
      quantity: q,
      price: orderType === "limit" || orderType === "stop_limit" ? num(price, 0) : null,
      stop_price: orderType === "stop" || orderType === "stop_limit" ? num(stopPrice, 0) : null,
      reduce_only: reduceOnly,
      product,
      exchange: market,
      decision_id: decision?.decision_id || "",
      reason: decision ? `Committee ${decision.verdict}` : "",
    })
      .then(() => {
        refreshAll();
        onClose();
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }

  const isBuy = side === "buy";

  return (
    <>
      <div className="overlay open" onClick={onClose} />
      <aside className={`paper-order-panel ${isBuy ? "is-buy" : "is-sell"}`} role="dialog" aria-label="Paper order entry">
        <div className="paper-order-head">
          <span className="paper-order-title">PAPER TRADE <small>· SIMULATED</small></span>
          <button className="close" onClick={onClose} title="Close" aria-label="Close order ticket">✕</button>
        </div>
        <div className="paper-order-sec">
          <SecurityLink market={market} ticker={ticker} className="symbol-lg">{ticker}</SecurityLink>
          <div className="dossier-company">{company} · {market}</div>
        </div>

        <div className="paper-side-tabs">
          <button className={`side-tab ${isBuy ? "active buy" : ""}`} onClick={() => { setSide("buy"); setReduceOnly(position?.side === "short"); setProduct(position?.product || "CNC"); }}>Buy</button>
          <button className={`side-tab ${!isBuy ? "active sell" : ""}`} onClick={() => { setSide("sell"); setReduceOnly(position?.side === "long"); setProduct(position?.product || "MIS"); }}>Sell</button>
        </div>

        {error && <div className="scan-warning">⚠ {error}</div>}
        {!quote ? (
          <div className="empty" style={{ padding: 24 }}>LOADING QUOTE…</div>
        ) : (
          <>
            <div className="paper-quote"><span>Reference price</span><strong>{priceNum != null ? num(priceNum).toFixed(2) : "Unavailable"}</strong><small>Simulated quote · execution may differ</small></div>
            {priceNum == null && <div className="team-note">A market order needs a price. You can still place a limit order.</div>}
            <div className="field"><label htmlFor="paper-account">Trading portfolio</label>
              <select id="paper-account" value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}>
                {portfolios.length ? portfolios.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.currency}</option>) : <option value={portfolioId}>{pf?.name || "Main"}</option>}
              </select>
            </div>
            <div className="paper-info-row"><span>Available cash</span><strong>{num(pf?.cash).toFixed(2)}</strong></div>
            {position && <div className="paper-info-row"><span>Open position</span><strong>{position.side} · {position.quantity} shares</strong></div>}

            <div className="field"><label htmlFor="paper-product">Trade type</label>
              <select id="paper-product" value={product} onChange={(e) => setProduct(e.target.value)} disabled={Boolean(closing && reduceOnly)}>
                <option value="CNC">Delivery · hold shares</option>
                <option value="MIS">Intraday · session position</option>
              </select>
            </div>
            {side === "sell" && !position && product === "CNC" && <div className="team-note">To open a short position, choose Intraday.</div>}

            <div className="field">
              <label htmlFor="paper-order-type">Order type</label>
              <select id="paper-order-type" value={orderType} onChange={(e) => setOrderType(e.target.value)}>
                {ORDER_TYPES.map((t) => <option key={t.key} value={t.key} disabled={t.key === "market" && priceNum == null}>{t.label}</option>)}
              </select>
            </div>

            <div className="field">
              <label htmlFor="paper-qty">Quantity · shares</label>
              <div className="paper-qty"><NumberInput id="paper-qty" stepperLabel="quantity" min="1" step="1" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="Enter quantity" />{closing && <button className="ghost" onClick={() => { setQty(String(position.quantity)); setReduceOnly(true); }}>Exit all</button>}</div>
            </div>

            {(orderType === "limit" || orderType === "stop_limit") && (
              <div className="field">
                <label>LIMIT PRICE</label>
                <NumberInput stepperLabel="limit price" stepperStep={1} min="0" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="0.00" />
              </div>
            )}
            {(orderType === "stop" || orderType === "stop_limit") && (
              <div className="field">
                <label>STOP PRICE</label>
                <NumberInput stepperLabel="stop price" stepperStep={1} min="0" step="0.01" value={stopPrice} onChange={(e) => setStopPrice(e.target.value)} placeholder="0.00" />
              </div>
            )}

            {closing && <label className="paper-check" title="Only reduce the existing position"><input type="checkbox" checked={reduceOnly} onChange={(e) => setReduceOnly(e.target.checked)} /> Close existing position only</label>}

            <div className="paper-info-row paper-estimate"><span>Estimated order value</span><strong>{estValue != null ? estValue.toFixed(2) : "—"}</strong></div>
            <div className="team-note">Estimate excludes simulated fees and margin. Market orders use the price available when submitted.</div>

            {decision && (
              <div className="paper-committee">
                <span className={`badge ${decision.verdict === "BULL" ? "bull" : decision.verdict === "BEAR" ? "bear" : "neutral"}`}>{decision.verdict}</span>
                <span>CONVICTION {decision.conviction != null ? Math.round(decision.conviction * 100) : "—"}%</span>
                {decision.thesis && <span className="paper-thesis">{decision.thesis}</span>}
              </div>
            )}

            <button className={`paper-submit ${isBuy ? "buy" : "sell"}`} disabled={busy || !pf || !q || (orderType === "market" && priceNum == null)} onClick={confirm}>
              {busy ? "Submitting…" : `${closing && reduceOnly ? "Close" : isBuy ? "Buy" : "Sell"} ${q > 0 ? `${q} shares` : "shares"}`}
            </button>
            <div className="team-note">SIMULATION ONLY — NO REAL ORDERS, NO REAL MONEY.</div>
          </>
        )}
      </aside>
    </>
  );
}
