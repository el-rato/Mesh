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
const PROVIDERS = [
  { id: "auto", label: "Auto" },
  { id: "gemini", label: "Gemini" },
  { id: "ollama", label: "Ollama" },
  { id: "local", label: "Local" },
];
const INTRO =
  "I'm **StockVerdict AI**. Markets are moving and I'm watching. What should I dig into?";

export default function AgentChat({
  open,
  onClose,
  seed = "",
  seedId = 0,
  initialMode = "AUTO",
  initialProvider = "auto",
  initialModel = "",
  onProviderChange,
}) {
  const { market } = useApp();
  const [tab, setTab] = useState(initialMode || "AUTO");
  const [provider, setProvider] = useState(initialProvider || "auto");
  const [model, setModel] = useState(initialModel || "");
  const [messages, setMessages] = useState([{ role: "assistant", content: INTRO }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [used, setUsed] = useState("");
  const [provOpen, setProvOpen] = useState(false);
  const inputRef = useRef(null);
  const scrollRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (initialMode && TABS.includes(initialMode.toUpperCase())) setTab(initialMode.toUpperCase());
    if (initialProvider) setProvider(initialProvider);
    if (initialModel) setModel(initialModel);
  }, [initialMode, initialProvider, initialModel]);

  // Focus the chat input only when the chat opens. Depending on `open` (not
  // `onClose`) means parent re-renders (new closure identity) never steal focus
  // from the model box while the user is typing in it.
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

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
        const res = await agentChat(next, market, tab, provider, model);
        setMessages([...next, { role: "assistant", content: res.content || "" }]);
        setUsed(res.provider || "");
      } catch (e) {
        setMessages([
          ...next,
          { role: "assistant", content: `⚠ Couldn't reach the agent: ${e.message}` },
        ]);
      } finally {
        setSending(false);
      }
    },
    [input, messages, sending, market, tab, provider, model]
  );

  useEffect(() => {
    if (seedId > 0 && seed && open) send(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedId]);

  if (!open) return null;

  const started = messages.some((m) => m.role === "user");
  const setProv = (id) => {
    setProvider(id);
    setProvOpen(false);
    onProviderChange?.(id);
  };

  return (
    <div className="agent-chat">
      <header className="agent-chat-header">
        <span className="agent-chat-mark">⬡</span>
        <div className="agent-chat-title">STOCKVERDICT AI</div>
        <span className="agent-chat-status">
          <span className="dot" /> {sending ? "WORKING" : "READY"}
          {used && <em className="agent-chat-prov">· {used.toUpperCase()}</em>}
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
        {started && used && (
          <div className="agent-provider-foot dim">AGENT · {used.toUpperCase()} · {tab}</div>
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
        <div className="agent-chat-controls">
          <button
            className={`agent-mode ${provOpen ? "open" : ""}`}
            onClick={() => setProvOpen((o) => !o)}
          >
            LLM : <em>{PROVIDERS.find((p) => p.id === provider)?.label}</em> <span className="agent-mode-caret">▾</span>
          </button>
          <input
            className="agent-model"
            placeholder="model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            title="Model id (leave blank to use the configured default)"
          />
        </div>
        <button className="agent-send" onClick={() => send()} disabled={sending}>
          →
        </button>
      </div>

      {provOpen && (
        <div className="agent-mode-menu agent-chat-menu">
          <div className="ov-panel-label">SELECT LLM</div>
          {PROVIDERS.map((p) => (
            <button key={p.id} className={`agent-mode-opt ${provider === p.id ? "selected" : ""}`} onClick={() => setProv(p.id)}>
              <span className={`agent-mode-dot ${p.id === "gemini" ? "cyan" : p.id === "ollama" ? "green" : "amber"} ${provider === p.id ? "on" : ""}`} />
              <span className="agent-mode-info">
                <span className="agent-mode-label">{p.label}</span>
                <span className="agent-mode-desc">
                  {p.id === "gemini" && "Google Gemini (needs API key)"}
                  {p.id === "ollama" && "Local model server (no key)"}
                  {p.id === "local" && "Built-in data-driven responder"}
                  {p.id === "auto" && "Use any configured LLM, else local"}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
