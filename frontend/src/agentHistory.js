const KEY = "sv-agent-sessions";
const MAX_SESSIONS = 20;
const MAX_MESSAGES = 60;

export function loadSessions() {
  try {
    const raw = localStorage.getItem(KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

export function saveSession(session) {
  try {
    if (!session || !session.id || !Array.isArray(session.messages)) return;
    if (!session.messages.some((m) => m.role === "user")) return;
    const trimmed = { ...session, messages: session.messages.slice(-MAX_MESSAGES) };
    const rest = loadSessions().filter((s) => s.id !== session.id);
    localStorage.setItem(KEY, JSON.stringify([trimmed, ...rest].slice(0, MAX_SESSIONS)));
  } catch {
    /* storage unavailable — history is best-effort */
  }
}

export function deleteSession(id) {
  try {
    localStorage.setItem(KEY, JSON.stringify(loadSessions().filter((s) => s.id !== id)));
  } catch {
    /* ignore */
  }
}
