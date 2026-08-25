function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

const LABELS = [
  { max: 25, label: "EXTREME FEAR", color: "var(--bear)" },
  { max: 45, label: "FEAR", color: "#c05621" },
  { max: 55, label: "NEUTRAL", color: "var(--neutral)" },
  { max: 75, label: "GREED", color: "#2f855a" },
  { max: 100, label: "EXTREME GREED", color: "var(--bull)" },
];

function sentiment(score) {
  return LABELS.find((s) => score <= s.max) || LABELS[LABELS.length - 1];
}

export function computeFearGreed(verdicts) {
  if (!verdicts || !verdicts.length) return null;
  const bull = verdicts.filter((v) => num(v.confidence) > 0 && (v.verdict || "").toUpperCase() === "BULL").length;
  const bear = verdicts.filter((v) => num(v.confidence) > 0 && (v.verdict || "").toUpperCase() === "BEAR").length;
  const total = verdicts.length;
  if (total === 0) return null;
  const ratio = bull / (bull + bear || 1);
  const score = Math.round(ratio * 100);
  return { score, bull, bear, total, neutral: total - bull - bear, ...sentiment(score) };
}

export default function FearGreedGauge({ verdicts, compact = false }) {
  const fg = computeFearGreed(verdicts);
  if (!fg) {
    return <div className="empty" style={{ padding: compact ? 8 : 16 }}>NO SENTIMENT DATA YET.</div>;
  }
  const pctStr = `${fg.score}%`;
  return (
    <div className={`fear-greed ${compact ? "compact" : ""}`}>
      <div className="fg-header">
        <span className="fg-title">FEAR &amp; GREED</span>
        {!compact && <span className="fg-live">● LIVE</span>}
      </div>
      <div className="fg-gauge">
        <div className="fg-score" style={{ color: fg.color }}>{fg.score}</div>
        <div className="fg-max">/100</div>
      </div>
      <div className="fg-bar">
        <div className="fg-bar-fill" style={{ width: pctStr, background: fg.color }} />
        <div className="fg-bar-marker" style={{ left: pctStr }} />
      </div>
      <div className="fg-sentiment" style={{ color: fg.color }}>{fg.label}</div>
      {!compact && (
        <div className="fg-breakdown">
          <span className="bull">{fg.bull} BULL</span>
          <span className="dim">{fg.neutral} NEUTRAL</span>
          <span className="bear">{fg.bear} BEAR</span>
        </div>
      )}
    </div>
  );
}
