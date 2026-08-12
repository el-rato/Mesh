import { useEffect, useState } from "react";
import { paperQuote, paperPortfolio, paperOrder, paperDecisions } from "../api.js";
import { useApp } from "../App.jsx";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

export default function PaperOrderTicket({ ticket, onClose }) {
  const { refreshAll } = useApp();
  const [quote, setQuote] = useState(null);
  const [pf, setPf] = useState(null);
  const [qty, setQty] = useState("");
  const [side, setSide] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const market = ticket?.market;
  const ticker = ticket?.ticker;
  const [decision, setDecision] = useState(ticket?.decision || null);

  const company = ticket?.company || ticker || "";

  useEffect(() => {
    if (!market || !ticker) return;
    setError("");
    Promise.all([paperQuote(market, ticker), paperPortfolio()])
      .then(([q, p]) => {
        setQuote(q);
        setPf(p);
        if (!side) {
          const pos = (p.positions || []).find((x) => x.market === market && x.ticker === ticker);
          const valid = validActions(pos);
          setSide(ticket.action && valid.includes(ticket.action) ? ticket.action : valid[0]);
        }
      })
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, ticker]);

  // When opened from a Dossier/Committee view, attach the latest decision
  // snapshot (decision_id + thesis captured at trade time) without running any
  // research — a lightweight DB read only.
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

  const position = (pf?.positions || []).find((x) => x.market === market && x.ticker === ticker);

  function validActions(pos) {
    if (!pos || pos.qty <= 0) return ["BUY", "SHORT"];
    if (pos.direction === "LONG") return ["BUY", "SELL", "CLOSE"];
    return ["SHORT", "COVER", "CLOSE"];
  }

  const actions = validActions(position);
  const price = quote?.price;
  const q = num(qty, 0);
  const estValue = price != null ? q * price : null;
  // Approximate immediate unrealized impact of this fill (labelled estimate).
  const estImpact =
    price != null && q > 0
      ? side === "SHORT" || side === "COVER"
        ? (side === "COVER" ? q * (position?.price != null ? price - position.price : 0) : q * (price - (position?.price ?? price)))
        : q * ((position?.price ?? price) - price)
      : null;

  function confirm() {
    if (q <= 0) {
      setError("enter a valid quantity");
      return;
    }
    setBusy(true);
    setError("");
    paperOrder({
      market,
      ticker,
      side,
      quantity: q,
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

  if (!ticket) return null;
  const reversalNote =
    position && position.qty > 0 &&
    ((position.direction === "LONG" && (side === "SHORT" || side === "COVER")) ||
      (position.direction === "SHORT" && (side === "BUY" || side === "SELL")));

  return (
    <>
      <div className="overlay open" onClick={onClose} />
      <div className="paper-ticket">
        <div className="paper-ticket-head">
          <span>PAPER TRADE · SIMULATION ONLY</span>
          <button className="close" onClick={onClose}>✕</button>
        </div>
        {error && <div className="scan-warning">⚠ {error}</div>}
        {!quote ? (
          <div className="empty" style={{ padding: 30 }}>LOADING QUOTE…</div>
        ) : quote.status === "no_data" ? (
          <div className="empty" style={{ padding: 30 }}>NO_DATA — no valid market price for {market}:{ticker}.</div>
        ) : (
          <>
            <div className="paper-ticket-sec">
              <div className="symbol-lg">{ticker}</div>
              <div className="dossier-company">{company} · {market}</div>
            </div>
            <div className="row"><span className="label">CURRENT PRICE</span><span className="value">{num(price).toFixed(4)}</span></div>
            <div className="row"><span className="label">CURRENT POSITION</span><span className="value">{position ? `${position.direction} ${position.qty} SH @ ${num(position.entry).toFixed(4)}` : "NONE"}</span></div>
            <div className="row"><span className="label">AVAILABLE CASH</span><span className="value">{num(pf?.cash).toFixed(2)}</span></div>

            <div className="controls" style={{ marginTop: 10 }}>
              <div className="field"><label>ACTION</label>
                <select value={side} onChange={(e) => setSide(e.target.value)}>
                  {actions.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
              <div className="field"><label>QUANTITY</label>
                <input type="number" min="1" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="0" />
              </div>
            </div>
            {reversalNote && (
              <div className="scan-warning">⚠ {position.direction} {position.qty} open — close it first to reverse direction.</div>
            )}
            <div className="row"><span className="label">EST. VALUE</span><span className="value">{estValue != null ? estValue.toFixed(2) : "—"}</span></div>
            <div className="row"><span className="label">EST. P&L IMPACT</span><span className="value">{estImpact != null ? (estImpact >= 0 ? "+" : "") + estImpact.toFixed(2) : "—"}</span></div>

            {decision && (
              <div className="paper-committee">
                <span className={`badge ${decision.verdict === "BULL" ? "bull" : decision.verdict === "BEAR" ? "bear" : "neutral"}`}>{decision.verdict}</span>
                <span>CONVICTION {decision.conviction != null ? Math.round(decision.conviction * 100) : "—"}%</span>
                {decision.research_confidence != null && <span>RESEARCH {Math.round(decision.research_confidence * 100)}%</span>}
                {decision.thesis && <span className="paper-thesis">{decision.thesis}</span>}
              </div>
            )}

            <div className="controls" style={{ marginTop: 12, justifyContent: "flex-end" }}>
              <button className="ghost" onClick={onClose}>CANCEL</button>
              <button className="primary" disabled={busy || reversalNote} onClick={confirm}>CONFIRM PAPER TRADE</button>
            </div>
            <div className="team-note">SIMULATION ONLY — NO REAL ORDERS, NO REAL MONEY. Slippage/commission are assumptions.</div>
          </>
        )}
      </div>
    </>
  );
}