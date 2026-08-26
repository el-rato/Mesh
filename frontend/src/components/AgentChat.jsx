import { useCallback, useEffect, useRef, useState } from "react";
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
const INTRO =
  "I'm **StockVerdict AI**. Markets are moving and I'm watching. What should I dig into?";

export default function AgentChat({ open, onClose, seed = "", seedId = 0, initialMode = "AUTO" }) {
  const { market } = useApp();
  const [tab, setTab] = useState(initialMode || "AUTO");
  const [messages, setMessages] = useState([{ role: "assistant", content: INTRO }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [provider, setProvider] = useState("");
  const inputRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (initialMode && TABS.includes(initialMode.toUpperCase())) setTab(initialMode.toUpperCase());
  }, [initialMode]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, sending]);

  const send = useCallback(
    async (text) => {
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
    },
    [input, messages, sending, market, tab]
  );

  useEffect(() => {
    if (seedId > 0 && seed && open) send(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedId]);

  if (!open) return null;

  const started = messages.some((m) => m.role === "user");

  return (
    <div className="agent-chat">
      <header className="agent-chat-header">
        <span className="agent-chat-mark">⬡</span>
        <div className="agent-chat-title">STOCKVERDICT AI</div>
        <span className="agent-chat-status">
          <span className="dot" /> {sending ? "WORKING" : "READY"}
          {provider && <em className="agent-chat-prov">· {provider.toUpperCase()}</em>}
        </span>
        <button className="agent-chat-close" onClick={onClose} title="Close (Esc)">
          ✕
        </button>
      </header>

      <div className="agent-tabs agent-chat-tabs">
        {TABS.map((t) => (
          <button key={t} className={`agent-tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      <div className="agent-chat-msgs" ref={scrollRef}>
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
              <span className="agent-typing">making it so…</span>
            </div>
          </div>
        )}
        {started && provider && (
          <div className="agent-provider-foot dim">AGENT · {provider.toUpperCase()} · {tab}</div>
        )}
      </div>

      <div className="agent-chat-inputbar">
        <input
          ref={inputRef}
          className="agent-input"
          placeholder="Ask about markets, news, verdicts… (type / for commands)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <button className="agent-send" onClick={() => send()} disabled={sending}>
          →
        </button>
      </div>
    </div>
  );
}
