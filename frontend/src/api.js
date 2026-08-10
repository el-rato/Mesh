export async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = "HTTP " + r.status;
    try {
      const j = await r.json();
      if (j.detail) detail = j.detail;
    } catch (e) {
      /* ignore */
    }
    throw new Error(detail);
  }
  return r.json();
}

export const CHART_RANGES = ["1d", "1w", "1mo", "1y", "all"];

export function rangeLabel(r) {
  if (r === "all") return "ALL";
  return r.toUpperCase();
}
