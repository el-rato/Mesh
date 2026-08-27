import { useEffect, useState, useRef, useCallback } from "react";
import { useApp } from "../App.jsx";
import AgentChat from "./AgentChat.jsx";
import { SectionHeader } from "./ui.jsx";
import { WORKFLOW_MODES as MODES, WORKFLOW_TEMPLATES as TEMPLATES, loadWorkflows, persistWorkflows } from "../workflows.js";

export default function WorkflowTab() {
  const { userEmail, username } = useApp();
  const [workflows, setWorkflows] = useState(() => loadWorkflows(userEmail));
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState("AUTO");
  const [error, setError] = useState("");

  const [chatOpen, setChatOpen] = useState(false);
  const [chatMode, setChatMode] = useState("AUTO");
  const [chatProvider, setChatProvider] = useState("auto");
  const [chatModel, setChatModel] = useState("");
  const [seed, setSeed] = useState("");
  const [seedId, setSeedId] = useState(0);
  const [searchMode, setSearchMode] = useState("");
  const seedNonce = useRef(0);

  // Reload the per-profile store whenever the signed-in account changes.
  useEffect(() => {
    setWorkflows(loadWorkflows(userEmail));
  }, [userEmail]);

  const save = useCallback(() => {
    const t = title.trim();
    const p = prompt.trim();
    if (!t || !p) {
      setError("Give your workflow a title and a prompt.");
      return;
    }
    setError("");
    const next = [
      ...workflows,
      { id: `wf-${Date.now()}`, title: t, prompt: p, mode },
    ];
    setWorkflows(next);
    persistWorkflows(userEmail, next);
    setTitle("");
    setPrompt("");
    setMode("AUTO");
  }, [title, prompt, mode, workflows, userEmail]);

  const remove = useCallback(
    (id) => {
      const next = workflows.filter((w) => w.id !== id);
      setWorkflows(next);
      persistWorkflows(userEmail, next);
    },
    [workflows, userEmail]
  );

  const run = useCallback((w, m) => {
    setChatMode(m || w.mode || "AUTO");
    setChatProvider("auto");
    setChatModel("");
    setSearchMode("");
    setSeed(w.prompt);
    seedNonce.current += 1;
    setSeedId(seedNonce.current);
    setChatOpen(true);
  }, []);

  const closeChat = useCallback(() => setChatOpen(false), []);

  const profileName = username || userEmail?.split("@")[0] || "you";

  return (
    <div className="workflows">
      <div className="ov-frame">
        <div className="ov-body">
          <main className="ov-main">
            {/* Builder */}
            <div className="ov-card">
              <SectionHeader title="BUILD A WORKFLOW" />
              <div className="wf-builder">
                <div className="wf-field">
                  <label>NAME</label>
                  <input
                    className="wf-input"
                    placeholder="e.g. Morning Macro Sweep"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </div>
                <div className="wf-field">
                  <label>PROMPT</label>
                  <textarea
                    className="wf-textarea"
                    placeholder="What should the agent run? e.g. Summarize today's rate moves and flag exposed sectors."
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    rows={3}
                  />
                </div>
                <div className="wf-field wf-field-mode">
                  <label>MODE</label>
                  <select className="wf-select" value={mode} onChange={(e) => setMode(e.target.value)}>
                    {MODES.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                </div>
                <div className="wf-builder-actions">
                  {error && <span className="scan-warning" style={{ marginRight: 10 }}>{error}</span>}
                  <button className="primary" onClick={save}>＋ SAVE WORKFLOW</button>
                </div>
              </div>
            </div>

            {/* Suggested templates */}
            <div className="ov-card">
              <SectionHeader title="SUGGESTED TEMPLATES" />
              <div className="wf-grid">
                {TEMPLATES.map((t) => (
                  <button key={t.title} className="wf-card" onClick={() => run(t, "AUTO")}>
                    <span className="wf-card-title">◆ {t.title}</span>
                    <span className="wf-card-desc">{t.prompt}</span>
                    <span className="wf-card-run">RUN ⟶</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Saved (profile-scoped) */}
            <div className="ov-card">
              <SectionHeader title={`YOUR WORKFLOWS · ${profileName.toUpperCase()}`} />
              {workflows.length ? (
                <div className="wf-grid">
                  {workflows.map((w) => (
                    <div key={w.id} className="wf-card saved">
                      <span className="wf-card-title">{w.title}</span>
                      <span className="wf-card-desc">{w.prompt}</span>
                      <div className="wf-card-foot">
                        <span className="wf-card-mode dim">{w.mode || "AUTO"}</span>
                        <span className="wf-card-btns">
                          <button className="ghost wf-run" onClick={() => run(w)}>RUN ⟶</button>
                          <button className="ghost wf-del" onClick={() => remove(w.id)} title="Delete workflow">✕</button>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty">NO SAVED WORKFLOWS YET — BUILD ONE ABOVE.</div>
              )}
            </div>
          </main>
        </div>
      </div>

      <AgentChat
        open={chatOpen}
        onClose={closeChat}
        seed={seed}
        seedId={seedId}
        initialMode={chatMode}
        initialProvider={chatProvider}
        initialModel={chatModel}
        initialSearch={searchMode}
        onProviderChange={setChatProvider}
      />
    </div>
  );
}
