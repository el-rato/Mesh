import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { transformSync } from "esbuild";

const source = readFileSync(new URL("../src/components/PriceChart.jsx", import.meta.url), "utf8");
const compiled = transformSync(source, { loader: "jsx", format: "cjs", jsx: "automatic" }).code;

function renderWithEffects(refreshKey, state, recorded) {
  const effects = [];
  let stateIndex = 0;
  const react = {
    useRef: () => ({ current: null }),
    useState(initial) {
      const index = stateIndex++;
      const value = state[index] === undefined ? initial : state[index];
      return [value, (next) => {
        recorded.state.push({ index, next });
        state[index] = next;
      }];
    },
    useEffect(callback, deps) { effects.push({ callback, deps }); },
  };
  const module = { exports: {} };
  const require = (name) => {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx: () => null, jsxs: () => null };
    if (name === "chart.js") return { Chart: { register() {} }, registerables: [] };
    if (name === "chartjs-chart-financial") return {};
    if (name === "../api.js") return { fetchJSON: () => { recorded.fetches++; return new Promise(() => {}); } };
    if (name.endsWith(".css") || name === "chartjs-adapter-date-fns") return {};
    throw new Error(`Unexpected import: ${name}`);
  };
  vm.runInNewContext("(function (module, exports, require) {" + compiled + "\n})")(module, module.exports, require);
  module.exports.default({ url: "/api/chart/US/%5EGSPC?range=1mo", refreshKey });
  return effects;
}

test("live quote refresh does not clear an already loaded chart", () => {
  const state = [true, [{ close: 100 }], false, false, null];
  const recorded = { state: [], fetches: 0 };
  const previous = renderWithEffects(1, state, recorded);
  const next = renderWithEffects(2, state, recorded);
  for (let i = 0; i < next.length; i++) {
    if (next[i].deps.some((value, n) => value !== previous[i].deps[n])) next[i].callback();
  }
  assert.equal(recorded.fetches, 0);
  assert.equal(recorded.state.some(({ index, next: value }) => index === 1 && value === null), false);
});
