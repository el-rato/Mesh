import { useEffect, useState } from "react";
import { agentConfig } from "../api.js";

const TABS = ["AUTO", "EQUITY", "MACRO", "NEWS"];

const PROVIDERS = [
  { id: "auto", label: "Auto", desc: "Use any configured LLM, else local" },
  { id: "opencode", label: "OpenCode GO", desc: "OpenAI-compatible chat endpoint" },
  { id: "gemini", label: "Gemini", desc: "Google Gemini (needs API key)" },
  { id: "ollama", label: "Ollama", desc: "Local model server (no key)" },
  { id: "local", label: "Local", desc: "Built-in data-driven responder" },
];

const TEMPLATES = [
  { title: "China & EM Recovery Trade", prompt: "Analyze the China & EM recovery trade — describe the setup and what to watch." },
  { title: "Healthcare & Biotech Rotation", prompt: "Analyze the Healthcare & Biotech rotation — describe the setup and what to watch." },
  { title: "Energy & Infrastructure Play", prompt: "Analyze the Energy & Infrastructure play — describe the setup and what to watch." },
  { title: "Geopolitical Risk Monitor", prompt: "Give me a geopolitical risk monitor — summarize risks and affected exposures." },
];

export default function AgentPanel({ onOpen, onAsk }) {
  const [tab, setTab] = useState("AUTO");
  const [provider, setProvider] = useState("auto");
  const [model, setModel] = useState("");
  const [provOpen, setProvOpen] = useState(false);
  const [cfg, setCfg] = useState(null);

  useEffect(() => {
    agentConfig().then(setCfg).catch(() => {});
  }, []);

  const providerLabel = PROVIDERS.find((p) => p.id === provider)?.label || "Auto";

  const open = () => {
    setProvOpen(false);
    onOpen(tab, provider, model);
  };
  const ask = (prompt) => {
    setProvOpen(false);
    onAsk(prompt, tab, provider, model);
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
      <button className="agent-prompt agent-launcher" onClick={open}>
        <span className="agent-caret">❯</span>
        <span className="agent-hint">Ask your Agent to start your workflow</span>
        <span className="agent-icons">
          <span className="agent-io green" />
          <span className="agent-io blue" />
        </span>
      </button>

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

      {/* Templates */}
      <div className="agent-workflow">
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
