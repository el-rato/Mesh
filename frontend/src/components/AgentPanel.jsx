import { useEffect, useRef, useState } from "react";
import { agentChat } from "../api.js";
import { useApp } from "../App.jsx";

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderRich(text) {
  return escapeHtml(text || "")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br/>");
}

const TABS = ["AUTO", "EQUITY", "MACRO", "NEWS"];

const SHELL_INTRO =
  "I'm **StockVerdict AI** — markets are moving and I'm watching. Pick a topic below or just ask.";

const TEMPLATES = [
  { title: "China & EM Recovery Trade", prompt: "Analyze the China & EM recovery trade — describe the setup and what to watch." },
  { title: "Healthcare & Biotech Rotation", prompt: "Analyze the Healthcare & Biotech rotation — describe the setup and what to watch." },
  { title: "Energy & Infrastructure Play", prompt: "Analyze the Energy & Infrastructure play — describe the setup and what to watch." },
  { title: "Geopolitical Risk Monitor", prompt: "Give me a geopolitical risk monitor — summarize risks and affected exposures." },
];

export default function AgentPanel() {
  const { market } = useApp();
  const [tab, setTab] = useState("AUTO");
  const [messages, setMessages] = useState([{ role: "assistant", content: SHELL_INTRO }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [provider, setProvider] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, sending]);

  const send = async (text) => {
    const payload = (text ?? input).trim();
    if (!payload || sending) return;
    const next = [...messages, { role: "user", content: payload }];
    setMessages(next);
    setInput("");
    setSending(true);
    try {
      const res = await agentChat(next, market, tab);
      setMessages([...next, { role: "assistant", content: res.content || "" }]);
      setProvider(res.provider || "");
    } catch (e) {
      setMessages([
        ...next,
        { role: "assistant", content: `⚠ Couldn't reach the agent: ${e.message}` },
      ]);
    } finally {
      setSending(false);
    }
  };

  const started = messages.some((m) => m.role === "user");

  return (
    <div className="agent">
      {/* Tabs */}
      <div className="agent-tabs">
        {TABS.map((t) => (
          <button
            key={t}
            className={`agent-tab ${tab === t ? "active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Prompt line */}
      <div className="agent-prompt">
        <span className="agent-caret">❯</span>
        <span className="agent-hint">
          {sending ? "Working…" : "Ask your Agent to start your workflow"}
        </span>
        <span className="agent-icons">
          <span className="agent-io green" />
          <span className="agent-io blue" />
        </span>
      </div>

      {/* Conversation */}
      <div className="agent-msgs" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={`agent-msg ${m.role}`}>
            <div className="agent-bubble">
              <div dangerouslySetInnerHTML={{ __html: renderRich(m.content) }} />
            </div>
          </div>
        ))}
        {sending && (
          <div className="agent-msg assistant">
            <div className="agent-bubble">
              <span className="agent-typing">working…</span>
            </div>
          </div>
        )}
        {started && provider && (
          <div className="agent-provider-foot dim">AGENT · {provider.toUpperCase()}</div>
        )}
      </div>

      {/* Command bar */}
      <div className="agent-inputbar">
        <input
          className="agent-input"
          placeholder="Type / for commands"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <button className="agent-act" disabled>{`⚙ Optimize`}</button>
        <button className="agent-act" disabled>{`▦ Approve Trades`}</button>
        <button className="agent-mode">Agent : <em>Lite</em></button>
        <button className="agent-send" onClick={() => send()} disabled={sending}>
          →
        </button>
      </div>

      {/* Suggested templates */}
      <div className="agent-workflow">
        <div className="ov-panel-label">WORKFLOW <span className="dim">· SUGGESTED TEMPLATES</span></div>
        <div className="agent-template-grid">
          {TEMPLATES.map((t) => (
            <button key={t.title} className="agent-template" onClick={() => send(t.prompt)}>
              <span className="agent-template-title">◆ {t.title}</span>
              <span className="agent-template-desc">{t.prompt}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
