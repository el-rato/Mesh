import { useEffect, useState } from "react";

export function verdictBadge(v) {
  const b = (v.verdict || v.label || "").toLowerCase();
  const cls = b === "bull" || b === "bullish" ? "bull" : b === "bear" || b === "bearish" ? "bear" : "neutral";
  return <span className={`badge ${cls}`}>{(v.verdict || "NEUTRAL").toUpperCase()}</span>;
}

export function verdictClass(v) {
  const b = (v || "").toLowerCase();
  if (b === "bull" || b === "bullish") return "bull";
  if (b === "bear" || b === "bearish") return "bear";
  return "neutral";
}

export function fmtNum(n, digits = 2) {
  return Number(n || 0).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function reasonText(r) {
  if (!r) return "";
  if (Array.isArray(r)) return r.join(" ");
  if (typeof r === "string") return r;
  return String(r);
}

export function Row({ k, v, cls }) {
  return (
    <div className="row">
      <span className="label">{k}</span>
      <span className={`value ${cls || ""}`}>{v}</span>
    </div>
  );
}

export function SectionHeader({ title, sub = "", right = null, style, className = "" }) {
  return (
    <div className={`section-head ${className}`} style={style}>
      <span>
        {title}
        {sub ? <span className="sub"> · {sub}</span> : null}
      </span>
      {right}
    </div>
  );
}

export function Metric({ label, value, tone = "", context = "" }) {
  return (
    <div className="metric">
      <span className="k">{label}</span>
      <span className={`v ${tone}`}>{value}</span>
      {context ? <span className="c">{context}</span> : null}
    </div>
  );
}

export function StatusIndicator({ state = "nodata", label = "", children }) {
  return (
    <span className={`status ${state}`}>
      {children || label}
    </span>
  );
}

export function SignalBadge({ verdict, label }) {
  return verdictBadge({ verdict, label });
}

export function RefreshStatus({ status }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const s = status || {};
  if (s.running) {
    return <span className="refresh-status running">↻ UPDATING…</span>;
  }
  if (s.error) {
    return (
      <span className="refresh-status error" title={s.error}>
        ⚠ UPDATE FAILED · SHOWING LAST KNOWN DATA
      </span>
    );
  }
  const last = s.last_fast_at ? new Date(s.last_fast_at).getTime() : null;
  const ago = last ? Math.max(0, Math.floor((now - last) / 1000)) : null;
  const nextMin = Math.max(0, Math.round((s.next_fast_in || 0) / 60));
  return (
    <span className="refresh-status live">
      ● LIVE{ago != null ? ` · UPDATED ${ago}s AGO` : ""} · NEXT UPDATE {nextMin}M
    </span>
  );
}
