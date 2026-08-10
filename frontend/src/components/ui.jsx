export function verdictBadge(v) {
  const b = (v.verdict || v.label || "").toLowerCase();
  const cls = b === "bull" || b === "bullish" ? "bull" : b === "bear" || b === "bearish" ? "bear" : "neutral";
  return <span className={`badge ${cls}`}>{(v.verdict || "NEUTRAL").toUpperCase()}</span>;
}

export function actionBadge(action) {
  const a = (action || "").toLowerCase();
  const cls = a === "buy" ? "buy" : a === "sell" ? "sell" : a === "hold" ? "hold" : "avoid";
  return <span className={`badge ${cls}`}>{(action || "AVOID").toUpperCase()}</span>;
}

export function scoreBadge(score) {
  const cls = score >= 0.3 ? "bull" : score >= 0.1 ? "neutral" : "bear";
  return <span className={`badge ${cls}`}>{(score * 100).toFixed(0)}%</span>;
}

export function verdictClass(v) {
  const b = (v || "").toLowerCase();
  if (b === "bull" || b === "bullish") return "bull";
  if (b === "bear" || b === "bearish") return "bear";
  return "neutral";
}

export function fmtNum(n, digits = 2) {
  return Number(n || 0).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function Row({ k, v, cls }) {
  return (
    <div className="row">
      <span className="label">{k}</span>
      <span className={`value ${cls || ""}`}>{v}</span>
    </div>
  );
}

export function MiniStat({ k, v }) {
  return (
    <div className="mini-stat">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}
