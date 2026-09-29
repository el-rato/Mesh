import test from "node:test";
import assert from "node:assert/strict";

import { buildTickerItems } from "../src/tickerTape.js";

test("ticker tape keeps its order when live data rerenders", () => {
  const tickers = [
    { market: "NYSE", ticker: "AAPL" },
    { market: "NYSE", ticker: "MSFT" },
    { market: "NASDAQ", ticker: "NVDA" },
  ];

  const firstRender = buildTickerItems(tickers, () => 0);
  const liveUpdate = buildTickerItems(tickers, () => 0.99);

  assert.deepEqual(liveUpdate, firstRender);
});
