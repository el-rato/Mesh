import { useEffect, useState } from "react";
import { agentConfig } from "../api.js";
import { useApp } from "../App.jsx";
import { WORKFLOW_TEMPLATES as TEMPLATES, loadWorkflows } from "../workflows.js";

const TABS = ["AUTO", "EQUITY", "MACRO", "NEWS"];

const PROVIDERS = [
  { id: "auto", label: "Auto", desc: "Use any configured LLM, else local" },
  { id: "opencode", label: "OpenCode GO", desc: "OpenAI-compatible chat endpoint" },
  { id: "gemini", label: "Gemini", desc: "Google Gemini (needs API key)" },
  { id: "ollama", label: "Ollama", desc: "Local model server (no key)" },
  { id: "local", label: "Local", desc: "Built-in data-driven responder" },
];

export default function AgentPanel({ onOpen, onAsk }) {
  const { userEmail } = useApp();
  const [tab, setTab] = useState("AUTO");
  const [provider, setProvider] = useState("auto");
  const [model, setModel] = useState("");
  const [provOpen, setProvOpen] = useState(false);
  const [cfg, setCfg] = useState(null);
  // Search style toggles: blue = deep search, green = low-token search.
  // Mutually exclusive; both off = the default balanced search.
  const [search, setSearch] = useState("");
  // Saved, profile-scoped workflows mirrored from the Workflows tab.
  const [saved, setSaved] = useState(() => loadWorkflows(userEmail));

  useEffect(() => {
    agentConfig().then(setCfg).catch(() => {});
  }, []);

  // Keep the overview workflow list in sync with the active profile's store.
  useEffect(() => {
    setSaved(loadWorkflows(userEmail));
    const onStorage = (e) => {
      if (!e.key || e.key === `sv-workflows-${userEmail || "anon"}`) {
        setSaved(loadWorkflows(userEmail));
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [userEmail]);

  const providerLabel = PROVIDERS.find((p) => p.id === provider)?.label || "Auto";

  const toggleSearch = (mode) => setSearch((cur) => (cur === mode ? "" : mode));

  const open = () => {
    setProvOpen(false);
    onOpen(tab, provider, model, search);
  };
  const ask = (prompt, mode) => {
    setProvOpen(false);
    onAsk(prompt, mode || tab, provider, model, search);
  };

  const configured = (id) => {
    if (id === "gemini") return !!cfg?.gemini_configured;
    if (id === "ollama") return !!cfg?.ollama_configured;
    if (id === "opencode") return !!cfg?.opencode_configured;
    return true;
  };

  return (
    <div className="agent">
      {/* Tabs */}
      <div className="agent-tabs">
        {TABS.map((t) => (
          <button key={t} className={`agent-tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {/* Prompt launcher */}
      <div
        className="agent-prompt agent-launcher"
        role="button"
        tabIndex={0}
        onClick={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") open();
        }}
      >
        <span className="agent-caret">❯</span>
        <span className="agent-hint">
          Ask your Agent to start your workflow
          {search === "deep" && <em className="agent-search-flag blue"> · DEEP SEARCH</em>}
          {search === "low" && <em className="agent-search-flag green"> · LOW-TOKEN SEARCH</em>}
        </span>
        <span className="agent-icons">
          <button
            className={`agent-io-btn green ${search === "low" ? "on" : ""}`}
            title="Low-token search: fast, minimal tool calls, ultra-concise answers"
            onClick={(e) => {
              e.stopPropagation();
              toggleSearch("low");
            }}
          >
            <span className="agent-io dot" /> LOW
          </button>
          <button
            className={`agent-io-btn blue ${search === "deep" ? "on" : ""}`}
            title="Deep search: exhaustive multi-source research across the terminal and the web"
            onClick={(e) => {
              e.stopPropagation();
              toggleSearch("deep");
            }}
          >
            <span className="agent-io dot" /> DEEP
          </button>
        </span>
      </div>

      {/* Command bar launcher */}
      <div className="agent-inputbar">
        <div className="agent-input agent-input-static" onClick={open}>
          Type / for commands
        </div>
        <button className="agent-act" disabled>{`⚙ Optimize`}</button>
        <button className="agent-act" disabled>{`▦ Approve Trades`}</button>
        <button
          className={`agent-mode ${provOpen ? "open" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            setProvOpen((o) => !o);
          }}
        >
          LLM : <em>{providerLabel}</em> <span className="agent-mode-caret">▾</span>
        </button>
        <button className="agent-send" onClick={open}>→</button>
      </div>

      {/* Provider (LLM type) dropdown */}
      {provOpen && (
        <div className="agent-mode-menu">
          <div className="ov-panel-label">SELECT LLM</div>
          {PROVIDERS.map((p) => {
            const ok = configured(p.id);
            return (
              <button
                key={p.id}
                className={`agent-mode-opt ${provider === p.id ? "selected" : ""}`}
                onClick={() => {
                  setProvider(p.id);
                  setProvOpen(false);
                }}
              >
                <span className={`agent-mode-dot ${p.id === "gemini" ? "cyan" : p.id === "ollama" ? "green" : p.id === "opencode" ? "purple" : "amber"} ${provider === p.id ? "on" : ""}`} />
                <span className="agent-mode-info">
                  <span className="agent-mode-label">
                    {p.label}
                    {!ok && <span className="agent-mode-off"> · not configured</span>}
                  </span>
                  <span className="agent-mode-desc">{p.desc}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* Templates + saved profile workflows */}
      <div className="agent-workflow">
        {saved.length > 0 && (
          <>
            <div className="ov-panel-label">WORKFLOW <span className="dim">· YOURS</span></div>
            <div className="agent-template-grid">
              {saved.map((w) => (
                <button key={w.id} className="agent-template saved" onClick={() => ask(w.prompt, w.mode || "AUTO")}>
                  <span className="agent-template-title">◆ {w.title}</span>
                  <span className="agent-template-desc">{w.prompt}</span>
                  <span className="agent-template-mode dim">{w.mode || "AUTO"}</span>
                </button>
              ))}
            </div>
          </>
        )}
        <div className="ov-panel-label">WORKFLOW <span className="dim">· SUGGESTED TEMPLATES</span></div>
        <div className="agent-template-grid">
          {TEMPLATES.map((t) => (
            <button key={t.title} className="agent-template" onClick={() => ask(t.prompt)}>
              <span className="agent-template-title">◆ {t.title}</span>
              <span className="agent-template-desc">{t.prompt}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
