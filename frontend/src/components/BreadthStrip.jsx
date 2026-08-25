function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

export function computeBreadth(rows) {
  if (!rows || !rows.length) return { total: 0, adv: 0, dec: 0, flat: 0, byMarket: {} };
  let adv = 0, dec = 0, flat = 0;
  const byMarket = {};
  for (const r of rows) {
    const move = num(r.price_move ?? r.change_pct);
    const key = r.market || "—";
    if (!byMarket[key]) byMarket[key] = { adv: 0, dec: 0, flat: 0, total: 0 };
    byMarket[key].total++;
    if (move > 0.0001) { adv++; byMarket[key].adv++; }
    else if (move < -0.0001) { dec++; byMarket[key].dec++; }
    else { flat++; byMarket[key].flat++; }
  }
  return { total: rows.length, adv, dec, flat, byMarket };
}

export default function BreadthStrip({ rows }) {
  const b = computeBreadth(rows);
  if (!b.total) return <div className="empty" style={{ padding: 8 }}>NO BREADTH DATA.</div>;
  const advPct = (b.adv / b.total) * 100;
  const decPct = (b.dec / b.total) * 100;
  return (
    <div className="breadth-strip">
      <div className="breadth-bar">
        <div className="breadth-adv" style={{ width: `${advPct}%` }} />
        <div className="breadth-dec" style={{ width: `${decPct}%` }} />
      </div>
      <div className="breadth-labels">
        <span className="bull">{b.adv} ADV</span>
        <span className="dim">{b.flat} FLAT</span>
        <span className="bear">{b.dec} DEC</span>
      </div>
      <div className="breadth-markets">
        {Object.entries(b.byMarket).sort().map(([mkt, v]) => {
          const total = v.total || 1;
          return (
            <div className="breadth-market" key={mkt}>
              <span className="mkt-name">{mkt}</span>
              <div className="mkt-bar">
                <div className="mkt-adv" style={{ width: `${(v.adv / total) * 100}%` }} />
                <div className="mkt-dec" style={{ width: `${(v.dec / total) * 100}%` }} />
              </div>
              <span className="mkt-counts">{v.adv}/{v.total}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
