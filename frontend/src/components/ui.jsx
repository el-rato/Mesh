export function verdictBadge(v) {
  const b = (v.verdict || v.label || "").toLowerCase();
  const cls = b === "bull" || b === "bullish" ? "bull" : b === "bear" || b === "bearish" ? "bear" : "neutral";
  return <span className={`badge ${cls}`}>{(v.verdict || "NEUTRAL").toUpperCase()}</span>;
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

export function reasonText(r) {
  if (!r) return "";
  if (Array.isArray(r)) return r.join(" ");
  if (typeof r === "string") return r;
  return String(r);
}

export function Row({ k, v, cls }) {
  return (
    <div className="row">
      <span className="label">{k}</span>
      <span className={`value ${cls || ""}`}>{v}</span>
    </div>
  );
}
