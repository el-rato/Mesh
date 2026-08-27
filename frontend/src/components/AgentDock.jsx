import { useEffect, useRef, useState } from "react";
import { agentChat } from "../api.js";
import { useApp } from "../App.jsx";
import { randomAgentGreeting } from "../greetings.js";

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderRich(text) {
  return escapeHtml(text || "")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br/>");
}

const WELCOME = {
  role: "assistant",
  content: randomAgentGreeting(),
};

export default function AgentDock() {
  const { market } = useApp();
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [provider, setProvider] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, sending]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    const next = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setSending(true);
    try {
      const res = await agentChat(next, market);
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

  return (
    <div className="agent">
      <div className="agent-header">
        <span className="agent-mark">⬡</span>
        <div className="agent-title">STOCKVERDICT AI</div>
        <span className="agent-status">
          <span className="dot" /> {sending ? "THINKING" : "READY"}
          {provider && <em className="agent-prov">· {provider}</em>}
        </span>
      </div>

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
              <span className="agent-typing">AI is thinking…</span>
            </div>
          </div>
        )}
      </div>

      <div className="agent-inputbar">
        <input
          className="agent-input"
          placeholder="Message StockVerdict AI…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <button className="agent-send" onClick={send} disabled={sending}>
          SEND ⟶
        </button>
      </div>
    </div>
  );
}
