// Canonical security navigation.
//
// One Dossier route:  #/dossier/{security_id}   where security_id = MARKET:TICKER
// (e.g. ULVR:LSE, TATAMOTORS:BSE, 068270:KRX).
//
// The security_id is URL-encoded so colons, market codes, exchange suffixes and
// leading zeroes survive navigation unchanged. This module is dependency-free so
// it can be unit tested with node:test.

export const DOSSIER_PREFIX = "/dossier/";

/** Normalize a raw security id; returns null when there is no usable id. */
export function normalizeSecurityId(input) {
  if (input == null) return null;
  const s = String(input).trim();
  if (!s) return null;
  if (s === ":" || s.toLowerCase().includes("undefined") || s.toLowerCase().includes("null")) return null;
  return s;
}

/** Canonical security_id from market + ticker. Returns null if either is missing. */
export function securityIdOf(market, ticker) {
  const m = normalizeSecurityId(market);
  const t = normalizeSecurityId(ticker);
  if (!m || !t) return null;
  return `${m}:${t.toUpperCase()}`;
}

/** Split a security_id into { market, ticker }.
 *
 * The app's own ids are `MARKET:TICKER` (e.g. BSE:TECHM, NYSE:AAPL). The
 * canonical Dossier form `TICKER:MARKET` (e.g. ULVR:LSE, 068270:KRX) is also
 * accepted: when `knownMarkets` is provided and exactly one side is a known
 * market code, that side is the market. Falls back to the app convention.
 */
export function splitSecurityId(securityId, knownMarkets = null) {
  const s = normalizeSecurityId(securityId);
  if (!s) return { market: "", ticker: "" };
  const idx = s.lastIndexOf(":");
  if (idx <= 0 || idx === s.length - 1) return { market: "", ticker: s };
  const left = s.slice(0, idx);
  const right = s.slice(idx + 1);
  if (knownMarkets && knownMarkets.length) {
    const codes = new Set(knownMarkets.map((c) => String(c).toUpperCase()));
    const leftKnown = codes.has(left.toUpperCase());
    const rightKnown = codes.has(right.toUpperCase());
    if (rightKnown && !leftKnown) {
      return { market: right, ticker: left }; // TICKER:MARKET form (ULVR:LSE)
    }
  }
  return { market: left, ticker: right }; // MARKET:TICKER form (BSE:TECHM)
}

/** Canonical hash path for a security's Dossier, or null if invalid. */
export function getDossierPath(securityId) {
  const id = normalizeSecurityId(securityId);
  if (!id) return null;
  return `#${DOSSIER_PREFIX}${encodeURIComponent(id)}`;
}

/** Parse the current location hash into a security_id (or null). */
export function parseDossierHash(hash) {
  const h = String(hash || "").replace(/^#/, "");
  if (!h.startsWith(DOSSIER_PREFIX)) return null;
  const raw = h.slice(DOSSIER_PREFIX.length);
  if (!raw) return null;
  try {
    return normalizeSecurityId(decodeURIComponent(raw));
  } catch (e) {
    return normalizeSecurityId(raw);
  }
}

/** True when a security display has enough information to be clickable. */
export function hasSecurityId(market, ticker) {
  return securityIdOf(market, ticker) != null;
}
