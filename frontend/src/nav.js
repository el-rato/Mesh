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

/** Split a security_id into { market, ticker } (splits on the LAST colon so a
 * ticker can never steal a colon from the market code). */
export function splitSecurityId(securityId) {
  const s = normalizeSecurityId(securityId);
  if (!s) return { market: "", ticker: "" };
  const idx = s.lastIndexOf(":");
  if (idx <= 0 || idx === s.length - 1) return { market: "", ticker: s };
  return { market: s.slice(0, idx), ticker: s.slice(idx + 1) };
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
