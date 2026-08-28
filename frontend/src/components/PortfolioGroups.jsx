import { useCallback, useEffect, useState } from "react";
import {
  portfolioGroups,
  createPortfolioGroup,
  renamePortfolioGroup,
  deletePortfolioGroup,
  addToGroup,
  removeFromGroup,
} from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";

function GroupView({ group, onChanged }) {
  const { openDrawer } = useApp();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(group.name);
  const [confirmDel, setConfirmDel] = useState(false);

  const saveRename = async () => {
    if (!name.trim()) return;
    await renamePortfolioGroup(group.group_id, name.trim(), group.description || null).catch(() => {});
    setRenaming(false);
    onChanged();
  };

  return (
    <div className="pg-group">
      <div className="pg-group-head">
        {renaming ? (
          <input
            className="pg-rename"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveRename()}
          />
        ) : (
          <span className="pg-name" title={group.description || ""}>{group.name}</span>
        )}
        <span className="pg-meta">
          {group.source === "agent_workflow" ? "FROM AGENT WORKFLOW" : group.source === "strategy" ? "FROM STRATEGY" : "MANUAL"}
          {group.created_from_strategy_at ? ` · ${group.created_from_strategy_at.slice(0, 10)}` : ""}
          {" · "}{group.members.length} securities
        </span>
        <span className="pg-actions">
          {renaming ? (
            <button className="ghost" onClick={saveRename}>SAVE</button>
          ) : (
            <button className="ghost" onClick={() => setRenaming(true)}>RENAME</button>
          )}
          {confirmDel ? (
            <button className="paper-short" onClick={async () => { await deletePortfolioGroup(group.group_id).catch(() => {}); onChanged(); }}>CONFIRM</button>
          ) : (
            <button className="ghost" onClick={() => setConfirmDel(true)}>DELETE</button>
          )}
        </span>
      </div>
      {!group.members.length ? (
        <div className="empty pg-empty">NO SECURITIES IN GROUP — add from a Dossier, Scanner or Strategy results.</div>
      ) : (
        <div className="pg-members">
          {group.members.map((m) => (
            <div className="pg-member" key={`${m.market}:${m.ticker}`}>
              <SecurityLink market={m.market} ticker={m.ticker} className="sym" />
              <span className="dim">{m.market}</span>
              <span className="pg-actions">
                <button className="ghost" onClick={() => openDrawer({ type: "stock", v: { market: m.market, ticker: m.ticker, company: "", reason: ["GROUP MEMBER"] } })}>DOSSIER</button>
                <button className="ghost" onClick={async () => { await removeFromGroup(group.group_id, m.market, m.ticker).catch(() => {}); onChanged(); }}>REMOVE</button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Reusable picker: choose an existing group (or type a new name to create).
export function GroupPicker({ onPick, onPickNew }) {
  const [groups, setGroups] = useState([]);
  const [selected, setSelected] = useState("");
  const [newName, setNewName] = useState("");

  const load = useCallback(() => {
    portfolioGroups().then(setGroups).catch(() => setGroups([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="pg-picker">
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">— existing group —</option>
        {groups.map((g) => (
          <option key={g.group_id} value={g.group_id}>{g.name} ({g.members.length})</option>
        ))}
      </select>
      <button
        className="primary"
        disabled={!selected}
        onClick={() => {
          const g = groups.find((x) => x.group_id === selected);
          if (g) onPick(g);
        }}
      >
        ADD TO GROUP
      </button>
      <div className="pg-new">
        <input
          placeholder="or create new group…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button
          className="primary"
          disabled={!newName.trim()}
          onClick={() => onPickNew(newName.trim())}
        >
          + CREATE
        </button>
      </div>
    </div>
  );
}

export default function PortfolioGroups() {
  const [groups, setGroups] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    portfolioGroups()
      .then((g) => { setGroups(g || []); setError(""); })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  if (!groups) return <div className="empty">LOADING GROUPS…</div>;

  return (
    <div className="portfolio-groups">
      <div className="landing-h" style={{ marginTop: 14 }}>GROUPS</div>
      {error && <div className="scan-warning">⚠ {error}</div>}
      {!groups.length ? (
        <div className="empty">
          NO GROUPS YET — CREATE ONE FROM A STRATEGY, OR ADD SECURITIES TO A NEW GROUP.
        </div>
      ) : (
        groups.map((g) => <GroupView key={g.group_id} group={g} onChanged={load} />)
      )}
    </div>
  );
}
