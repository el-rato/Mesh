const WORKFLOW = [
  { t: "RESEARCH", d: "Evidence is gathered — news, 13F ownership, price and regime context — into a structured brief. Nothing is fabricated." },
  { t: "SIGNALS", d: "Quantitative models (LSTM, tree, momentum), technicals, news and market regime produce confidence-weighted signals." },
  { t: "COMMITTEE", d: "Available signals are weighed into one BULL / BEAR / NEUTRAL decision with a thesis, risks and invalidation conditions." },
  { t: "PAPER", d: "Simulate BUY / SELL / SHORT / COVER trades against live market prices and track P&L — all in a local paper portfolio." },
];

const CAPABILITIES = [
  "Dynamic search across the full supported universe — no hardcoded lists",
  "One canonical analysis per security across Overview, Scanner and Dossier",
  "Explicit data coverage — NO_DATA is shown as NO_DATA, never guessed",
  "Simulated intraday paper trading with decision-linked trade journal",
  "Automatic background analysis — no manual refresh required",
];

export default function Landing({ onEnter }) {
  return (
    <div className="lp">
      <header className="lp-top">
        <div className="lp-brand">SV<span> | STOCK VERDICT</span></div>
        <div className="lp-ver">MARKET INTELLIGENCE TERMINAL</div>
      </header>

      <section className="lp-hero">
        <h1>STOCK VERDICT</h1>
        <p className="lp-tagline">
          A research terminal that turns market data into a decision. Researcher,
          signal engine and investment committee combine into one clear, auditable
          BULL / BEAR / NEUTRAL view — with simulated paper trading to act on it.
        </p>
        <button className="primary lp-cta" onClick={onEnter}>OPEN TERMINAL ⟶</button>
        <button className="ghost lp-cta" onClick={onEnter}>EXPLORE MARKETS</button>
        <div className="lp-hero-sub">Simulated trading · No broker · No real money</div>
      </section>

      <section className="lp-section">
        <div className="lp-h">FROM DATA TO DECISION</div>
        <div className="lp-grid">
          {WORKFLOW.map((f) => (
            <div className="lp-card" key={f.t}>
              <div className="lp-card-t">{f.t}</div>
              <p>{f.d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-h">WHAT THE TERMINAL DOES</div>
        <ul className="lp-list">
          {CAPABILITIES.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </section>

      <footer className="lp-foot">
        <button className="primary lp-cta" onClick={onEnter}>OPEN TERMINAL ⟶</button>
        <div className="lp-sub">STOCK VERDICT — research and simulated trading, not financial advice.</div>
      </footer>
    </div>
  );
}