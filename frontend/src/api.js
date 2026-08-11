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

export const CHART_RANGES = ["1d", "1w", "1mo", "3mo", "6mo", "1y"];

export function rangeLabel(r) {
  if (r === "all") return "ALL";
  return r.toUpperCase();
}

export async function lstmBatchPredict(symbols, period = "2y", window = 30) {
  return fetchJSON(`/api/lstm/batch-predict?symbols=${encodeURIComponent(symbols.join(","))}&period=${period}&window=${window}`);
}

export async function lstmTrain(symbol, period = "2y", window = 30, epochs = 25, batch_size = 32, lr = 1e-3) {
  return fetchJSON(`/api/lstm/train?symbol=${encodeURIComponent(symbol)}&period=${period}&window=${window}&epochs=${epochs}&batch_size=${batch_size}&lr=${lr}`);
}

export async function dossier(params) {
  const qs = new URLSearchParams();
  if (params.symbol) qs.set("symbol", params.symbol);
  if (params.market) qs.set("market", params.market);
  if (params.ticker) qs.set("ticker", params.ticker);
  if (params.fresh) qs.set("fresh", "true");
  return fetchJSON(`/api/dossier?${qs.toString()}`);
}

export async function scanner(params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") qs.set(k, v);
  });
  return fetchJSON(`/api/scanner?${qs.toString()}`);
}
