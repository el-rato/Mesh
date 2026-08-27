// Shared, profile-scoped workflow store. Both the Workflows tab and the
// Overview AgentPanel read from here so saved workflows appear everywhere.

export const WORKFLOW_MODES = ["AUTO", "EQUITY", "MACRO", "NEWS"];

export const WORKFLOW_TEMPLATES = [
  { title: "China & EM Recovery Trade", prompt: "Analyze the China & EM recovery trade — describe the setup and what to watch." },
  { title: "Healthcare & Biotech Rotation", prompt: "Analyze the Healthcare & Biotech rotation — describe the setup and what to watch." },
  { title: "Energy & Infrastructure Play", prompt: "Analyze the Energy & Infrastructure play — describe the setup and what to watch." },
  { title: "Geopolitical Risk Monitor", prompt: "Give me a geopolitical risk monitor — summarize risks and affected exposures." },
];

function storageKey(email) {
  return `sv-workflows-${email || "anon"}`;
}

export function loadWorkflows(email) {
  try {
    const raw = localStorage.getItem(storageKey(email));
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

export function persistWorkflows(email, list) {
  try {
    localStorage.setItem(storageKey(email), JSON.stringify(list));
    // Notify listeners in the same window (the `storage` event only fires in
    // *other* tabs), so the Overview AgentPanel updates without a reload.
    window.dispatchEvent(new CustomEvent("sv-workflows-changed", { detail: { email } }));
  } catch {
    /* ignore quota / private-mode errors */
  }
}
