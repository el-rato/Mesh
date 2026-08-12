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

export async function paperPortfolio() {
  return fetchJSON("/api/paper/portfolio");
}

export async function paperQuote(market, ticker) {
  return fetchJSON(`/api/paper/quote?market=${encodeURIComponent(market)}&ticker=${encodeURIComponent(ticker)}`);
}

export async function paperOrder({ market, ticker, side, quantity, decision_id = "", reason = "" }) {
  return fetchJSON("/api/paper/order", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ market, ticker, side, quantity, decision_id, reason }),
  });
}

export async function paperTrades() {
  return fetchJSON("/api/paper/trades");
}

export async function paperDecisions(market = "", ticker = "") {
  const qs = new URLSearchParams();
  if (market) qs.set("market", market);
  if (ticker) qs.set("ticker", ticker);
  return fetchJSON(`/api/paper/decisions?${qs.toString()}`);
}

export async function paperPerformance() {
  return fetchJSON("/api/paper/performance");
}

export async function paperEvaluate() {
  return fetchJSON("/api/paper/evaluate", { method: "POST" });
}

export async function paperStats() {
  return fetchJSON("/api/paper/stats");
}

export async function paperRisk() {
  return fetchJSON("/api/paper/risk");
}

export async function paperLeaderboard() {
  return fetchJSON("/api/paper/leaderboard");
}

export async function paperEquity() {
  return fetchJSON("/api/paper/equity");
}

export async function paperEndSession() {
  return fetchJSON("/api/paper/end-session", { method: "POST" });
}
