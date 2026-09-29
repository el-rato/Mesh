export function buildTickerItems(tickers, random = Math.random) {
  void random;
  return [...(tickers || [])].slice(0, 60);
}
