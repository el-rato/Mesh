import { getDossierPath, normalizeSecurityId, securityIdOf } from "../nav.js";

/**
 * The one canonical security -> Dossier link.
 *
 * Accepts either an explicit canonical `securityId` ("ULVR:LSE") or a
 * `market` + `ticker` pair. Renders a compact terminal link to the canonical
 * Dossier route. When no valid security id can be formed the reference is
 * rendered as non-clickable text (never a broken /dossier/undefined route).
 */
export default function SecurityLink({
  securityId,
  market,
  ticker,
  children,
  className = "",
  title,
  ...rest
}) {
  const id = normalizeSecurityId(securityId) || securityIdOf(market, ticker);
  if (!id) {
    return (
      <span className={`security-link muted ${className}`} {...rest}>
        {children}
      </span>
    );
  }
  return (
    <a
      className={`security-link ${className}`}
      href={getDossierPath(id)}
      title={title || `Open Dossier ${id}`}
      onClick={(e) => e.stopPropagation()}
      {...rest}
    >
      {children}
    </a>
  );
}

/**
 * Render text with every occurrence of the security_id token wrapped in a
 * SecurityLink (used for notification messages that mention a security inline).
 * Falls back to plain text when no valid security id is present.
 */
export function SecurityText({ text, securityId, market, ticker, className = "" }) {
  const id = normalizeSecurityId(securityId) || securityIdOf(market, ticker);
  const t = String(text || "");
  if (!id || !t.includes(id)) {
    return <span className={className}>{t}</span>;
  }
  const parts = t.split(id);
  return (
    <span className={className}>
      {parts.map((p, i) => (
        <span key={i}>
          {p}
          {i < parts.length - 1 && <SecurityLink securityId={id}>{id}</SecurityLink>}
        </span>
      ))}
    </span>
  );
}
