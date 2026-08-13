import test from "node:test";
import assert from "node:assert/strict";

import {
  DOSSIER_PREFIX,
  getDossierPath,
  hasSecurityId,
  normalizeSecurityId,
  parseDossierHash,
  securityIdOf,
  splitSecurityId,
} from "../src/nav.js";

const KNOWN_MARKETS = ["BSE", "NYSE", "LSE", "KRX", "TSE", "HKEX", "ASX", "XETRA", "TSX", "SGX"];

test("normalizeSecurityId rejects unusable ids", () => {
  assert.equal(normalizeSecurityId(null), null);
  assert.equal(normalizeSecurityId(undefined), null);
  assert.equal(normalizeSecurityId(""), null);
  assert.equal(normalizeSecurityId("   "), null);
  assert.equal(normalizeSecurityId(":"), null);
  assert.equal(normalizeSecurityId("undefined"), null);
  assert.equal(normalizeSecurityId("ULVR:LSE"), "ULVR:LSE");
});

test("securityIdOf builds canonical MARKET:TICKER and uppercases ticker", () => {
  assert.equal(securityIdOf("LSE", "ulvr"), "LSE:ULVR");
  assert.equal(securityIdOf("BSE", "TATAMOTORS"), "BSE:TATAMOTORS");
  assert.equal(securityIdOf("KRX", "068270"), "KRX:068270"); // leading zero survives
  assert.equal(securityIdOf(null, "ULVR"), null);
  assert.equal(securityIdOf("LSE", ""), null);
});

test("splitSecurityId resolves the market side for both id forms", () => {
  // App convention MARKET:TICKER
  assert.deepEqual(splitSecurityId("BSE:TECHM", KNOWN_MARKETS), { market: "BSE", ticker: "TECHM" });
  assert.deepEqual(splitSecurityId("NYSE:AAPL", KNOWN_MARKETS), { market: "NYSE", ticker: "AAPL" });
  // Canonical Dossier form TICKER:MARKET
  assert.deepEqual(splitSecurityId("ULVR:LSE", KNOWN_MARKETS), { market: "LSE", ticker: "ULVR" });
  assert.deepEqual(splitSecurityId("068270:KRX", KNOWN_MARKETS), { market: "KRX", ticker: "068270" });
  // No known markets -> app convention (market on the left).
  assert.deepEqual(splitSecurityId("BSE:TECHM"), { market: "BSE", ticker: "TECHM" });
  // No colon -> no market (callers must not create a dossier route from this).
  assert.deepEqual(splitSecurityId("AAPL"), { market: "", ticker: "AAPL" });
  assert.deepEqual(splitSecurityId(null), { market: "", ticker: "" });
});

test("getDossierPath produces one canonical route", () => {
  assert.equal(getDossierPath("ULVR:LSE"), `#${DOSSIER_PREFIX}ULVR%3ALSE`);
  assert.equal(getDossierPath("068270:KRX"), `#${DOSSIER_PREFIX}068270%3AKRX`);
  assert.equal(getDossierPath(undefined), null);
});

test("parseDossierHash round-trips the original security id", () => {
  for (const id of ["ULVR:LSE", "TATAMOTORS:BSE", "068270:KRX"]) {
    const path = getDossierPath(id);
    assert.equal(parseDossierHash(path), id);
    assert.equal(parseDossierHash(path.slice(1)), id); // leading # tolerated
  }
});

test("parseDossierHash rejects non-dossier / empty hashes", () => {
  assert.equal(parseDossierHash(""), null);
  assert.equal(parseDossierHash("#/other/ULVR:LSE"), null);
  assert.equal(parseDossierHash("#/dossier/"), null);
  assert.equal(parseDossierHash("#/dossier/undefined"), null);
});

test("security_id survives encode/decode navigation unchanged", () => {
  const id = "068270:KRX";
  const encoded = encodeURIComponent(id);
  assert.equal(encoded, "068270%3AKRX");
  assert.equal(decodeURIComponent(encoded), id);
});

test("hasSecurityId reports only complete refs", () => {
  assert.equal(hasSecurityId("LSE", "ULVR"), true);
  assert.equal(hasSecurityId("LSE", ""), false);
  assert.equal(hasSecurityId("", "ULVR"), false);
});
