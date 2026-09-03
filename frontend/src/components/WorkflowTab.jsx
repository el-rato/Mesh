import { useEffect, useState, useRef, useCallback } from "react";
import { useApp } from "../App.jsx";
import AgentChat from "./AgentChat.jsx";
import { SectionHeader } from "./ui.jsx";
import { GroupPicker } from "./PortfolioGroups.jsx";
import { agentWorkflow, addToGroup, createPortfolioGroup } from "../api.js";
import { WORKFLOW_MODES as MODES, WORKFLOW_TEMPLATES as TEMPLATES, loadWorkflows, persistWorkflows } from "../workflows.js";

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}
function pct(v) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${(n * 100).toFixed(1)}%`;
}
const MATCH_CLS = { MATCH: "up", DOES_NOT_MATCH: "down", NOT_EVALUABLE: "dim" };
const STATUS_CLS = { STALE: "stale", NO_DATA: "down", ERROR: "down", NOT_EVALUABLE: "dim" };

function matchBadge(mc) {
  return <span className={MATCH_CLS[mc] || "dim"} style={{ fontWeight: 700 }}>{mc.replace(/_/g, " ")}</span>;
}
function statusBadge(s) {
  return <span className={STATUS_CLS[s] || "dim"}>{(s || "—").replace(/_/g, " ")}</span>;
}

export default function WorkflowTab() {
  const { userEmail, username, openDrawer, addToPortfolio, inPortfolio } = useApp();
  const [workflows, setWorkflows] = useState(() => loadWorkflows(userEmail));
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState("AUTO");
  const [error, setError] = useState("");

  // ---- Agent Workflow screening results (Strategy == Workflow, one system) ----
  const [screenPrompt, setScreenPrompt] = useState("");
  const [results, setResults] = useState(null);
  const [selected, setSelected] = useState({});
  const [screenBusy, setScreenBusy] = useState(false);
  const [note, setNote] = useState("");

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

  // ---- Screening: natural-language criteria → real securities (Workflow IS Strategy) ----
  const runScreen = useCallback(async (text) => {
    const p = (text ?? screenPrompt).trim();
    if (!p) { setNote("Describe the criteria, e.g. “large cap, strong momentum, bullish trend”."); return; }
    setScreenBusy(true); setNote(""); setSelected({});
    try {
      const r = await agentWorkflow(p, null, 30);
      setResults(r);
      setScreenPrompt(p);
    } catch (e) { setNote(e.message); } finally { setScreenBusy(false); }
  }, [screenPrompt]);

  const allRows = results
    ? [
        ...(results.qualifying || []).map((r) => ({ ...r, match_class: r.match_class || "MATCH" })),
        ...(results.not_matching || []),
        ...(results.not_evaluable || []),
      ]
    : [];
  const selectedRows = allRows.filter((r) => selected[r.security_id]);

  const openDossier = (r) =>
    openDrawer({
      type: "stock",
      v: { market: r.market, ticker: r.ticker, symbol: r.symbol || r.ticker, company: r.company || "", reason: ["AGENT WORKFLOW"] },
    });

  const addToPortfolioOne = async (r) => {
    try { await addToPortfolio(r.market, r.ticker, r.company || ""); setNote(`Added ${r.ticker} to portfolio.`); }
    catch (e) { setNote(e.message); }
  };

  const addSelectedToGroup = async (group) => {
    if (!group || !selectedRows.length) return;
    setScreenBusy(true);
    let added = 0, dup = 0, failed = 0;
    for (const r of selectedRows) {
      try {
        const res = await addToGroup(group.group_id, r.market, r.ticker);
        res?.added ? added++ : dup++;   // market:ticker PK dedupe
      } catch { failed++; }
    }
    setScreenBusy(false);
    setNote(
      `Added ${added} to “${group.name}”` +
      (dup ? ` · ${dup} already in group (market:ticker dedupe)` : "") +
      (failed ? ` · ${failed} failed` : "") +
      ". Groups are static snapshots."
    );
  };

  const createGroupFromSelection = async (name) => {
    const n = (name || "").trim();
    if (!n || !selectedRows.length) return;
    setScreenBusy(true);
    try {
      const g = await createPortfolioGroup({
        name: n,
        description: `Agent workflow snapshot: ${results?.workflow || screenPrompt}`,
        source: "agent_workflow",
        workflow_text: results?.workflow || screenPrompt,
        members: selectedRows.map((r) => ({ market: r.market, ticker: r.ticker })),
      });
      setNote(`Created group “${g.name}” with ${g.members.length} securities (static snapshot, market:ticker dedupe).`);
    } catch (e) { setNote(e.message); } finally { setScreenBusy(false); }
  };

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

            {/* Agent Workflow screening results */}
            <div className="ov-card">
              <SectionHeader title="AGENT WORKFLOW — SCREEN SECURITIES" />
              <div className="wf-builder">
                <div className="wf-field" style={{ flex: 2 }}>
                  <label>CRITERIA (NATURAL LANGUAGE)</label>
                  <input
                    className="wf-input"
                    placeholder="e.g. strong momentum, bullish trend, increasing volume — market-cap requests are kept but marked UNVERIFIED"
                    value={screenPrompt}
                    onChange={(e) => setScreenPrompt(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && runScreen()}
                  />
                </div>
                <div className="wf-builder-actions">
                  {note && <span className="scan-warning" style={{ marginRight: 10 }}>{note}</span>}
                  <button className="primary" onClick={() => runScreen()} disabled={screenBusy}>
                    {screenBusy ? "SCREENING…" : "⟳ RUN SCREEN"}
                  </button>
                </div>
              </div>

              {results && (
                <>
                  <div className="dim" style={{ margin: "6px 0 10px", fontSize: 12 }}>
                    UNIVERSE {results.universe_size} ·{" "}
                    <span className="up">MATCH {results.qualifying_count}</span> ·{" "}
                    <span className="down">DOES_NOT_MATCH {results.not_matching_count ?? (results.not_matching || []).length}</span> ·{" "}
                    <span className="dim">NOT_EVALUABLE {(results.not_evaluable || []).length}</span>
                    {results.market_cap_unverified && (
                      <span className="dim" title="Market-cap data is not reliably available; the requirement is preserved but never used as a filter.">
                        {" "}· MARKET CAP: UNVERIFIED{results.market_cap_bucket ? ` (${results.market_cap_bucket})` : ""}
                      </span>
                    )}
                  </div>

                  {selectedRows.length > 0 && (
                    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "8px 0 12px" }}>
                      <span className="dim">{selectedRows.length} SELECTED</span>
                      <GroupPicker
                        onPick={addSelectedToGroup}
                        onPickNew={createGroupFromSelection}
                      />
                      <button
                        className="ghost"
                        onClick={() => setSelected({})}
                        disabled={screenBusy}
                      >
                        CLEAR
                      </button>
                    </div>
                  )}

                  {allRows.length ? (
                    <div className="screener-table">
                      <div className="screener-row screener-head">
                        <span style={{ width: 28 }}></span>
                        <span>SECURITY</span><span>MATCH</span><span>SCORE</span><span>VERDICT</span>
                        <span>MOM</span><span>RSI</span><span>VOL</span><span>STATUS</span><span>AS_OF</span><span>REASONING</span>
                      </div>
                      {allRows.map((r) => {
                        const inPf = inPortfolio(r.market, r.ticker);
                        return (
                          <div className="screener-row" key={r.security_id}>
                            <span style={{ width: 28 }} onClick={(e) => e.stopPropagation()}>
                              <input
                                type="checkbox"
                                checked={!!selected[r.security_id]}
                                onChange={(e) => setSelected((s) => ({ ...s, [r.security_id]: e.target.checked }))}
                                title={`Select ${r.security_id}`}
                              />
                            </span>
                            <span className="sec">
                              <button className="ghost" style={{ padding: 0 }} onClick={() => openDossier(r)} title="Open Dossier">
                                <strong>{r.ticker}</strong>
                              </button>
                              <span className="dim">{r.market} · {r.company || ""}</span>
                              <span className="pg-actions" onClick={(e) => e.stopPropagation()}>
                                {inPf ? (
                                  <span className="dim">IN PORTFOLIO</span>
                                ) : (
                                  <button className="ghost" onClick={() => addToPortfolioOne(r)}>+ PORTFOLIO</button>
                                )}
                              </span>
                            </span>
                            <span>{matchBadge(r.match_class)}</span>
                            <span>{r.score != null ? num(r.score).toFixed(1) : "—"}</span>
                            <span className={r.verdict === "BULL" ? "up" : r.verdict === "BEAR" ? "down" : "dim"}>{r.verdict || "—"}</span>
                            <span className={num(r.momentum_20) >= 0 ? "up" : "down"}>{pct(r.momentum_20)}</span>
                            <span>{r.rsi_14 != null ? num(r.rsi_14).toFixed(0) : "—"}</span>
                            <span>{r.volume_ratio != null ? num(r.volume_ratio).toFixed(1) + "x" : "—"}</span>
                            <span>{statusBadge(r.status)}</span>
                            <span className="dim">{(r.as_of || r.price_as_of || "").slice(0, 10) || "—"}</span>
                            <span className="dim" title={r.reasoning || r.match_reason || ""}>
                              {(r.reasoning || r.match_reason || "—").slice(0, 90)}
                              {r.market_cap_unverified && <em> · market cap UNVERIFIED</em>}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="empty">NO SECURITIES RETURNED — BROADEN THE CRITERIA.</div>
                  )}
                </>
              )}
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
