// Rotating agent greetings so the assistant never repeats the same static NPC line.
// Each entry avoids the old "I'm StockVerdict AI. Markets are moving and I'm
// watching. What should I dig into?" opener and instead greets in a fresh voice.

export const AGENT_GREETINGS = [
  "I'm **StockVerdict AI** — your markets desk mate. Tape's live and I'm wired in. What are we digging into?",
  "Fresh eyes on the board. I'm **StockVerdict AI** — where do you want to start?",
  "Markets don't sleep and neither do I. I'm **StockVerdict AI** — what's on your mind?",
  "Ready when you are. I'm **StockVerdict AI** — point me at a name, a theme, or a hunch.",
  "Good to see you. I'm **StockVerdict AI**, your co-pilot for the chaos. What should we chase today?",
  "I'm **StockVerdict AI**, parked at the desk with the feeds humming. What's the first move?",
  "Hot off the wire. I'm **StockVerdict AI** — tell me what to pull apart first.",
  "I'm **StockVerdict AI**. The tape's restless — give me a ticker, a sector, or a question.",
  "Desk's open. I'm **StockVerdict AI** — what corner of the market are we poking at?",
  "I'm **StockVerdict AI**, caffeinated on data. What should I sink my teeth into?",
  "Locked in and listening. I'm **StockVerdict AI** — shoot me your first idea.",
  "I'm **StockVerdict AI**. Signals are warm — what are we hunting for today?",
];

function hash(s) {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}

// Pick a greeting that differs from `avoid` (the previously shown line), so the
// agent feels like it greets differently every time instead of looping one line.
export function randomAgentGreeting(avoid) {
  const pool = AGENT_GREETINGS.filter((g) => g !== avoid);
  const src = pool.length ? pool : AGENT_GREETINGS;
  const idx = Math.abs(hash(`${Date.now()}:${Math.random()}`)) % src.length;
  return src[idx];
}
