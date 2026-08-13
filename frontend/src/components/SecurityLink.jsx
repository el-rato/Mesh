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
