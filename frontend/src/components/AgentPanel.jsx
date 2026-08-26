import { useState } from "react";

const TABS = ["AUTO", "EQUITY", "MACRO", "NEWS"];

const TEMPLATES = [
  { title: "China & EM Recovery Trade", prompt: "Analyze the China & EM recovery trade — describe the setup and what to watch." },
  { title: "Healthcare & Biotech Rotation", prompt: "Analyze the Healthcare & Biotech rotation — describe the setup and what to watch." },
  { title: "Energy & Infrastructure Play", prompt: "Analyze the Energy & Infrastructure play — describe the setup and what to watch." },
  { title: "Geopolitical Risk Monitor", prompt: "Give me a geopolitical risk monitor — summarize risks and affected exposures." },
];

export default function AgentPanel({ onOpen, onAsk }) {
  const [tab, setTab] = useState("AUTO");
  const open = () => onOpen(tab);

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
        <button className="agent-mode" onClick={open}>Agent : <em>Lite</em></button>
        <button className="agent-send" onClick={open}>→</button>
      </div>

      {/* Templates */}
      <div className="agent-workflow">
        <div className="ov-panel-label">WORKFLOW <span className="dim">· SUGGESTED TEMPLATES</span></div>
        <div className="agent-template-grid">
          {TEMPLATES.map((t) => (
            <button key={t.title} className="agent-template" onClick={() => onAsk(t.prompt, tab)}>
              <span className="agent-template-title">◆ {t.title}</span>
              <span className="agent-template-desc">{t.prompt}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
