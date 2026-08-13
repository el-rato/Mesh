import { Row, StatusIndicator } from "./ui.jsx";

const ILLUSTRATIVE = <span className="lp-illus">ILLUSTRATIVE</span>;

function Candles({ bars, height = 132 }) {
  const min = Math.min(...bars.map((b) => b.l));
  const max = Math.max(...bars.map((b) => b.h));
  const pad = 6;
  const span = max - min || 1;
  const width = 100;
  const y = (v) => pad + ((max - v) / span) * (height - pad * 2);
  const bw = width / bars.length;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="lp-candles"
      preserveAspectRatio="none"
      role="img"
      aria-label="Illustrative candlestick chart"
    >
      {bars.map((b, i) => {
        const up = b.c >= b.o;
        const color = up ? "var(--bull)" : "var(--bear)";
        const cx = i * bw + bw / 2;
        const top = y(Math.max(b.o, b.c));
        const h = Math.max(1, Math.abs(y(b.o) - y(b.c)));
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={y(b.h)} y2={y(b.l)} stroke={color} strokeWidth="0.6" />
            <rect x={cx - bw * 0.3} y={top} width={bw * 0.6} height={h} fill={color} />
          </g>
        );
      })}
    </svg>
  );
}

function SignalRow({ l, v }) {
  return (
    <div className="lp-signal">
      <span className="l">{l}</span>
      <span className={v === "BEAR" ? "bear" : v === "BULL" ? "bull" : "dim"}>{v}</span>
    </div>
  );
}

function TerminalPreview() {
  return (
    <div className="lp-terminal" aria-hidden="true">
      <div className="lp-terminal-head">
        <span className="sym">ULVR:LSE</span>
        <span className="co">Unilever PLC</span>
        {ILLUSTRATIVE}
      </div>
      <div className="lp-terminal-price">
        <span>£42.18</span>
        <span className="chg bear">-1.24%</span>
      </div>
      <Candles
        bars={[
          { o: 44, h: 45, l: 42.5, c: 43 },
          { o: 43, h: 43.5, l: 41.8, c: 42 },
          { o: 42, h: 43, l: 41.5, c: 42.6 },
          { o: 42.6, h: 43.4, l: 41.9, c: 42.1 },
          { o: 42.1, h: 42.8, l: 41.2, c: 41.6 },
          { o: 41.6, h: 42.4, l: 40.8, c: 42 },
          { o: 42, h: 43, l: 41.5, c: 42.9 },
          { o: 42.9, h: 43.2, l: 41.8, c: 42.2 },
          { o: 42.2, h: 42.9, l: 41.4, c: 41.7 },
          { o: 41.7, h: 42.5, l: 41, c: 42.2 },
          { o: 42.2, h: 43, l: 41.6, c: 42.8 },
          { o: 42.8, h: 43.1, l: 42, c: 42.3 },
          { o: 42.3, h: 42.6, l: 41.4, c: 41.6 },
          { o: 41.6, h: 42, l: 40.9, c: 41.4 },
        ]}
      />
      <div className="lp-signals">
        <SignalRow l="TECHNICAL" v="BEAR" />
        <SignalRow l="REGIME" v="BEAR" />
        <SignalRow l="NEWS" v="NO_DATA" />
        <SignalRow l="RESEARCH" v="BEAR" />
      </div>
      <Row k="COMMITTEE" v={<span className="badge bear">BEAR</span>} />
      <Row k="CONVICTION" v="74%" />
      <Row k="DECISION" v={<span className="value down">SELL</span>} />
    </div>
  );
}

const SCREENER_ROWS = [
  { t: "ULVR:LSE", p: "£42.18", c: "-1.24%", v: "bear", badge: "BEAR" },
  { t: "SHEL:LSE", p: "£28.10", c: "+0.72%", v: "bull", badge: "BULL" },
  { t: "068270:KRX", p: "₩181,000", c: "+2.10%", v: "bull", badge: "BULL" },
  { t: "NVDA:NYSE", p: "$132.40", c: "-0.44%", v: "bear", badge: "BEAR" },
];

function ScreenerPreview() {
  return (
    <div className="lp-stage-card">
      <div className="lp-stage-title">SCREENER {ILLUSTRATIVE}</div>
      {SCREENER_ROWS.map((r) => (
        <div className="row" key={r.t}>
          <span className="label">
            <span className="t" style={{ color: "var(--amber)" }}>{r.t}</span>
          </span>
          <span className="value" style={{ fontVariantNumeric: "tabular-nums" }}>
            {r.p} <span className={r.v === "bull" ? "up" : "down"}>{r.c}</span>{" "}
            <span className={`badge ${r.v}`}>{r.badge}</span>
          </span>
        </div>
      ))}
      <div className="row" style={{ marginTop: 10, borderTop: "1px dashed var(--border)", paddingTop: 8 }}>
        <span className="label">SELECT → DOSSIER</span>
      </div>
    </div>
  );
}

function DossierPreview() {
  return (
    <div className="lp-stage-card">
      <div className="lp-stage-title">DOSSIER {ILLUSTRATIVE}</div>
      <div className="lp-terminal-head" style={{ marginBottom: 2 }}>
        <span className="sym">ULVR:LSE</span>
        <span className="co">Unilever PLC</span>
      </div>
      <div className="lp-terminal-price" style={{ fontSize: 18 }}>
        <span>£42.18</span>
        <span className="chg bear">-1.24%</span>
      </div>
      <Candles
        height={96}
        bars={[
          { o: 43, h: 43.4, l: 41.6, c: 42 },
          { o: 42, h: 42.6, l: 41.2, c: 41.5 },
          { o: 41.5, h: 42.2, l: 40.8, c: 41.9 },
          { o: 41.9, h: 42.5, l: 41.3, c: 42.2 },
          { o: 42.2, h: 43, l: 41.6, c: 42.7 },
          { o: 42.7, h: 43.1, l: 42, c: 42.4 },
          { o: 42.4, h: 42.7, l: 41.5, c: 41.8 },
        ]}
      />
      <div className="lp-signals">
        <SignalRow l="RESEARCH" v="BEAR" />
        <SignalRow l="TECHNICAL" v="BEAR" />
        <SignalRow l="COMMITTEE" v="BEAR" />
      </div>
      <Row k="CONVICTION" v="74%" />
      <Row k="DECISION" v={<span className="value down">SELL</span>} />
    </div>
  );
}

function PaperPreview() {
  return (
    <div className="lp-stage-card">
      <div className="lp-stage-title">PAPER PORTFOLIO {ILLUSTRATIVE}</div>
      <div className="lp-signals" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <SignalRow l="EQUITY" v="£108,420" />
        <SignalRow l="P&L" v="+2.1%" />
        <SignalRow l="CASH" v="£18,210" />
        <SignalRow l="EXPOSURE" v="£52,100" />
      </div>
      <div className="row" style={{ marginTop: 10, borderTop: "1px dashed var(--border)", paddingTop: 8 }}>
        <span className="label">POSITION · ULVR:LSE</span>
        <span className="value"><span className="badge bull">LONG</span> 100 SH</span>
      </div>
      <div className="row">
        <span className="label">P&L</span>
        <span className="value down">-£124.00</span>
      </div>
    </div>
  );
}

const STEPS = [
  { n: "01", t: "DISCOVER", d: "Something happens in the market — a move, an event, a signal." },
  { n: "02", t: "SCREEN", d: "Find the securities worth investigating." },
  { n: "03", t: "DOSSIER", d: "Understand the security — chart, research, ownership." },
  { n: "04", t: "COMMITTEE", d: "Independent signals become one structured verdict." },
  { n: "05", t: "PAPER TRADE", d: "Test the decision without risking real capital." },
];

export default function Landing({ onLogin, onRegister }) {
  return (
    <div className="lp">
      {/* Navigation */}
      <header className="lp-nav">
        <div className="lp-nav-brand">SV<span> · STOCK VERDICT</span></div>
        <nav className="lp-nav-links" aria-label="Landing">
          <a className="lp-nav-link" href="#how-it-works">HOW IT WORKS</a>
          <button className="lp-nav-link" onClick={onLogin}>LOGIN</button>
          <button className="lp-nav-link" onClick={onRegister}>GET STARTED</button>
        </nav>
        <button className="primary lp-nav-cta" onClick={onLogin}>OPEN TERMINAL</button>
      </header>

      {/* Hero */}
      <section className="lp-hero">
        <div className="lp-hero-copy">
          <div className="lp-eyebrow">Market intelligence terminal</div>
          <h1 className="lp-h1">
            Think like <em>the desk.</em>
          </h1>
          <p className="lp-lede">
            Research securities, combine independent signals, understand the
            Committee, and test decisions before risking real capital.
          </p>
          <div className="lp-hero-ctas">
            <button className="primary lp-cta" onClick={onLogin}>OPEN TERMINAL ⟶</button>
            <a className="lp-cta-ghost" href="#how-it-works">SEE HOW IT WORKS</a>
          </div>
          <div className="lp-hero-meta">Simulated trading · No broker · No real money</div>
        </div>
        <TerminalPreview />
      </section>

      {/* Product preview */}
      <section className="lp-section" id="product">
        <div className="lp-kicker">The terminal</div>
        <h2 className="lp-h2">From discovery to a testable decision.</h2>
        <p className="lp-lead">
          A single workflow — screen, open a Dossier, weigh the Committee, then
          act in a simulated portfolio. Every step uses the real terminal.
        </p>
        <div className="lp-product">
          <div className="lp-stage"><ScreenerPreview /><span className="lp-stage-arrow">→</span></div>
          <div className="lp-stage"><DossierPreview /><span className="lp-stage-arrow">→</span></div>
          <div className="lp-stage"><PaperPreview /></div>
        </div>
      </section>

      {/* How it works */}
      <section className="lp-section" id="how-it-works">
        <div className="lp-kicker">How it works</div>
        <h2 className="lp-h2">A clear path from event to action.</h2>
        <div className="lp-steps">
          {STEPS.map((s) => (
            <div className="lp-step" key={s.n}>
              <div className="lp-step-num">{s.n}</div>
              <div className="lp-step-t">{s.t}</div>
              <p>{s.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Researcher / Signals / Committee */}
      <section className="lp-section" id="architecture">
        <div className="lp-kicker">Decision architecture</div>
        <h2 className="lp-h2">Why the system believes what it believes.</h2>
        <div className="lp-pillars">
          <div className="lp-pillar">
            <div className="lp-pillar-t">RESEARCHER</div>
            <p>Investigates the security and gathers relevant evidence — news, 13F ownership, price and regime context — into a structured brief. Nothing is fabricated.</p>
          </div>
          <div className="lp-pillar">
            <div className="lp-pillar-t">SIGNALS</div>
            <p>Independent models and market signals — quantitative, technical, news and regime — provide additional evidence, each with its own confidence.</p>
          </div>
          <div className="lp-pillar">
            <div className="lp-pillar-t">COMMITTEE</div>
            <p>Combines the available evidence into a BULL, BEAR or NEUTRAL verdict with conviction and reasoning. Coverage gaps show as NO_DATA, never guessed.</p>
          </div>
        </div>
        <div className="lp-note">
          <strong>Inspect the reasoning.</strong> Every verdict links back to the evidence behind it — the thesis, the risks, and the conditions that would change the view. The terminal surfaces <strong>why</strong>, not just a score.
        </div>
      </section>

      {/* Paper trading */}
      <section className="lp-section" id="paper">
        <div className="lp-kicker">Paper trading</div>
        <h2 className="lp-h2">Test the decision, not just display it.</h2>
        <p className="lp-lead">
          Simulate the full trade lifecycle — BUY, SELL, SHORT, COVER — against
          live market prices, tracked in a local paper portfolio.
        </p>
        <div className="lp-actions">
          <span className="lp-action buy">BUY</span>
          <span className="lp-action sell">SELL</span>
          <span className="lp-action short">SHORT</span>
          <span className="lp-action cover">COVER</span>
        </div>
        <div className="lp-trade">
          <div className="lp-stage-card">
            <div className="lp-stage-title">PORTFOLIO {ILLUSTRATIVE}</div>
            <div className="lp-signals">
              <SignalRow l="EQUITY" v="£108,420" />
              <SignalRow l="P&L" v="+2.1%" />
              <SignalRow l="CASH" v="£18,210" />
              <SignalRow l="EXPOSURE" v="£52,100" />
            </div>
          </div>
          <div className="lp-stage-card">
            <div className="lp-stage-title">POSITION {ILLUSTRATIVE}</div>
            <Row k="ULVR:LSE" v={<span className="badge bull">LONG</span>} />
            <Row k="SHARES" v="100" />
            <Row k="ENTRY" v="£43.42" />
            <Row k="CURRENT" v="£42.18" />
            <Row k="P&L" v={<span className="value down">-£124.00</span>} />
          </div>
        </div>
      </section>

      {/* Decision example */}
      <section className="lp-section" id="example">
        <div className="lp-kicker">Illustrative example</div>
        <div className="lp-example">
          <div>
            <div className="lp-stage-title">ULVR:LSE · Unilever PLC {ILLUSTRATIVE}</div>
            <Row k="COMMITTEE" v={<span className="badge bear">BEAR</span>} />
            <Row k="CONVICTION" v="74%" />
            <Row k="TRADER ACTION" v={<span className="value down">SELL</span>} />
            <div className="lp-signals" style={{ marginTop: 10 }}>
              <SignalRow l="TECHNICAL" v="BEAR" />
              <SignalRow l="REGIME" v="BEAR" />
              <SignalRow l="NEWS" v="NO_DATA" />
              <SignalRow l="RESEARCH" v="BEAR" />
            </div>
          </div>
          <div>
            <div className="lp-stage-title">REASON</div>
            <p className="lp-lead" style={{ margin: 0, fontSize: 14 }}>
              Bearish Committee conviction combined with existing long exposure
              triggers an exit.
            </p>
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="lp-cta-band">
        <h2 className="lp-h2">Open the terminal.</h2>
        <p className="lp-lead">
          See the workflow, the evidence and the Committee for yourself — then
          paper-trade a decision before any real capital moves.
        </p>
        <button className="primary lp-cta" onClick={onLogin}>OPEN TERMINAL ⟶</button>
      </section>

      {/* Footer */}
      <footer className="lp-foot">
        <span className="lp-nav-brand">SV<span> · STOCK VERDICT</span></span>
        <span>Research and simulated trading — not financial advice.</span>
        <span><StatusIndicator state="nodata" label="NO BROKER · NO REAL MONEY" /></span>
      </footer>
    </div>
  );
}
