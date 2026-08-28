import { useCallback, useEffect, useRef, useState } from "react";
import { agentChat, agentWorkflow, addToGroup, createPortfolioGroup } from "../api.js";
import { saveSession } from "../agentHistory.js";
import { useApp } from "../App.jsx";
import { randomAgentGreeting } from "../greetings.js";
import { GroupPicker } from "./PortfolioGroups.jsx";

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
const INTRO = randomAgentGreeting();

export default function AgentChat({
  open,
  onClose,
  seed = "",
  seedId = 0,
  initialMode = "AUTO",
  initialProvider = "auto",
  initialModel = "",
  initialSearch = "",
  onProviderChange,
  restoreSession = null,
  restoreNonce = 0,
}) {
  const { market, openDrawer } = useApp();
  const [tab, setTab] = useState(initialMode || "AUTO");
  const [provider, setProvider] = useState(initialProvider || "auto");
  const [model, setModel] = useState(initialModel || "");
  const [search, setSearch] = useState(initialSearch || "");
  const [messages, setMessages] = useState([{ role: "assistant", content: INTRO }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [used, setUsed] = useState("");
  const [provOpen, setProvOpen] = useState(false);
  const inputRef = useRef(null);
  const scrollRef = useRef(null);
  const sessionRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const greetedRef = useRef(INTRO);

  // Restore a saved session from the overview history when requested.
  useEffect(() => {
    if (!restoreNonce || !restoreSession) return;
    sessionRef.current = restoreSession.id;
    setMessages(Array.isArray(restoreSession.messages) && restoreSession.messages.length
      ? restoreSession.messages
      : [{ role: "assistant", content: randomAgentGreeting() }]);
    setUsed(restoreSession.provider || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoreNonce]);

  // Greet differently every time the chat opens fresh (no ongoing conversation,
  // not restoring a saved session). This keeps the assistant from repeating the
  // same static NPC line across opens.
  useEffect(() => {
    if (!open) return;
    if (restoreNonce > 0 && restoreSession) return;
    setMessages((msgs) => {
      if (msgs.some((m) => m.role === "user")) return msgs;
      const g = randomAgentGreeting(greetedRef.current);
      greetedRef.current = g;
      return [{ role: "assistant", content: g }];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, restoreNonce]);

  useEffect(() => {
    if (initialMode && TABS.includes(initialMode.toUpperCase())) setTab(initialMode.toUpperCase());
    if (initialProvider) setProvider(initialProvider);
    if (initialModel) setModel(initialModel);
    setSearch(initialSearch || "");
  }, [initialMode, initialProvider, initialModel, initialSearch]);

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
        const res = await agentChat(next, market, tab, provider, model, search);
        const finalMsgs = [...next, { role: "assistant", content: res.content || "" }];
        setMessages(finalMsgs);
        const usedProvider = res.provider || provider;
        setUsed(usedProvider);
        if (!sessionRef.current) sessionRef.current = `sv-${Date.now()}`;
        saveSession({
          id: sessionRef.current,
          title: (next.find((m) => m.role === "user")?.content || payload).slice(0, 64),
          provider: usedProvider,
          mode: tab,
          search,
          updated_at: new Date().toISOString(),
          messages: finalMsgs,
        });
      } catch (e) {
        setMessages([
          ...next,
          { role: "assistant", content: `⚠ Couldn't reach the agent: ${e.message}` },
        ]);
      } finally {
        setSending(false);
      }
    },
    [input, messages, sending, market, tab, provider, model, search]
  );

  useEffect(() => {
    if (seedId > 0 && seed && open) send(seed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedId]);

  // ---- Agent Workflow screening + group integration ----
  // The Agent IS the strategy system: a workflow prompt screens the universe and
  // returns real securities that can be sent straight to a Portfolio Group.
  const [wf, setWf] = useState(null);
  const [wfSel, setWfSel] = useState({});
  const [wfPicker, setWfPicker] = useState(false);
  const [wfNotice, setWfNotice] = useState("");

  const runWorkflow = useCallback(async () => {
    const prompt = input.trim();
    if (!prompt || sending) return;
    setSending(true);
    setWfNotice("");
    try {
      const r = await agentWorkflow(prompt, market);
      setWf(r);
      const sel = {};
      (r.qualifying || []).forEach((q) => { sel[q.security_id] = true; });
      setWfSel(sel);
    } catch (e) {
      setWfNotice(`Workflow failed: ${e.message}`);
    } finally {
      setSending(false);
    }
  }, [input, market, sending]);

  const wfSelectedSecs = wf ? (wf.qualifying || []).filter((q) => wfSel[q.security_id]) : [];

  const wfAddToGroup = async (group) => {
    let added = 0;
    for (const s of wfSelectedSecs) {
      try { await addToGroup(group.group_id, s.market, s.ticker); added += 1; } catch { /* skip */ }
    }
    setWfPicker(false);
    setWfNotice(`Added ${added} securit${added === 1 ? "y" : "ies"} to "${group.name}".`);
  };

  const wfCreateGroup = async (name) => {
    const members = wfSelectedSecs.map((s) => ({ market: s.market, ticker: s.ticker }));
    if (!members.length) { setWfNotice("Select at least one security first."); return; }
    try {
      const g = await createPortfolioGroup({ name, source: "agent_workflow", workflow_text: wf?.workflow || "", members });
      setWfPicker(false);
      setWfNotice(`Created group "${g.name}" with ${members.length} securities.`);
    } catch (e) { setWfNotice(`Failed: ${e.message}`); }
  };

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
          <span className="agent-chat-search">
            <button
              className={`agent-io-btn blue ${search === "deep" ? "on" : ""}`}
              title="Deep search: exhaustive multi-source research"
              onClick={() => setSearch((s) => (s === "deep" ? "" : "deep"))}
            >
              <span className="agent-io dot" /> DEEP
            </button>
            <button
              className={`agent-io-btn green ${search === "low" ? "on" : ""}`}
              title="Low-token search: fast and ultra-concise"
              onClick={() => setSearch((s) => (s === "low" ? "" : "low"))}
            >
              <span className="agent-io dot" /> LOW
            </button>
          </span>
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
          <div className="agent-provider-foot dim">
            AGENT · {used.toUpperCase()} · {tab}
            {search === "deep" && " · DEEP SEARCH"}
            {search === "low" && " · LOW-TOKEN SEARCH"}
          </div>
        )}
      </div>

      {wf && (
        <div className="agent-workflow">
          <div className="agent-wf-head">
            <span className="landing-h" style={{ marginTop: 0 }}>WORKFLOW RESULTS</span>
            <span className="dim">{wf.universe_size} screened · {wf.qualifying_count} qualifying{wf.market_cap_unverified ? " · MARKET CAP UNVERIFIED" : ""}</span>
          </div>
          {wfNotice && <div className="scan-warning">{wfNotice}</div>}
          {wfSelectedSecs.length > 0 && (
            <div className="strategy-actions">
              <button className="primary" onClick={() => setWfPicker((v) => !v)}>⊕ ADD SELECTED TO GROUP</button>
            </div>
          )}
          {wfPicker && (
            <div className="pg-picker-wrap">
              <div className="dim">Send {wfSelectedSecs.length} selected to a group:</div>
              <GroupPicker onPick={wfAddToGroup} onPickNew={wfCreateGroup} />
            </div>
          )}
          <div className="grid">
            {(wf.qualifying || []).map((q) => (
              <div key={q.security_id} className="panel strategy-card" onClick={() => openDrawer({ type: "stock", v: { market: q.market, ticker: q.ticker, company: q.company, reason: ["WORKFLOW RESULT"] } })}>
                <label className="strategy-check" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={!!wfSel[q.security_id]} onChange={() => setWfSel((s) => ({ ...s, [q.security_id]: !s[q.security_id] }))} />
                </label>
                <div className="panel-head">
                  <div>
                    <span className="symbol">{q.ticker}</span>
                    <div className="name">{q.market} · {q.company || ""}</div>
                  </div>
                  <div className="strategy-score">
                    <span className="score">{q.score}</span><span className="dim">/100</span>
                    {q.verdict ? <span className={`badge ${q.verdict === "BULL" ? "bull" : q.verdict === "BEAR" ? "bear" : "neutral"}`}>{q.verdict}</span> : null}
                  </div>
                </div>
                <div className="strategy-expl">
                  {q.explanation.map((e, i) => <div key={i} className="expl-item">+ {e}</div>)}
                  {q.price_status === "stale" && <div className="expl-item stale">~ price STALE (as_of {String(q.price_as_of).slice(0, 10)})</div>}
                </div>
              </div>
            ))}
          </div>
          {wf.not_evaluable && wf.not_evaluable.length > 0 && (
            <div className="dim" style={{ marginTop: 8 }}>
              NOT_EVALUABLE ({wf.not_evaluable.length}): {wf.not_evaluable.slice(0, 6).map((n) => `${n.ticker} (${n.missing_required.join("/")})`).join(", ")}
              {" "}— cannot be judged due to missing data (kept separate from non-matches).
            </div>
          )}
        </div>
      )}

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
            list="agent-models"
            title="Model id (blank = configured default)"
          />
          <datalist id="agent-models">
            <option value="deepseek-v4-flash-vision-exp" />
            <option value="deepseek-v4-flash" />
            <option value="deepseek-v4-pro" />
            <option value="kimi-k3" />
            <option value="qwen3.7-max" />
            <option value="glm-5.3" />
            <option value="gpt-5.6-luna" />
          </datalist>
        </div>
        <button className="agent-send" onClick={() => send()} disabled={sending}>
          →
        </button>
        <button className="agent-io-btn amber" onClick={runWorkflow} disabled={sending} title="Screen the universe for this workflow and return real candidates">
          ▶ WORKFLOW
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
