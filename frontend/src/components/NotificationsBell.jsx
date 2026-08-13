import { useCallback, useEffect, useRef, useState } from "react";
import { notifications, notificationsAck } from "../api.js";
import { useApp } from "../App.jsx";

function sevCls(s) {
  if (s === "HIGH") return "high";
  if (s === "IMPORTANT") return "important";
  return "";
}

export default function NotificationsBell() {
  const { openDrawer, setTab, setScreenerPrefill } = useApp();
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  const load = useCallback(() => {
    notifications(50)
      .then(setItems)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const unread = items.filter((n) => !n.acked).length;

  const openDossier = (a) => {
    setOpen(false);
    openDrawer({ type: "stock", v: { market: a.market || "", ticker: a.ticker || "", company: "", reason: ["NOTIFICATION"] } });
  };

  const screenSimilar = (a) => {
    setOpen(false);
    setScreenerPrefill({ market: a.market || "" });
    setTab("screener");
  };

  const dismissAll = () => {
    const keys = items.filter((n) => !n.acked).map((n) => n.event_key);
    if (!keys.length) return;
    notificationsAck(keys).then(load).catch(() => {});
  };

  return (
    <div className="bell-wrap" ref={ref}>
      <button className="bell-btn" onClick={() => setOpen((v) => !v)} title="Terminal notifications">
        🔔
        {unread > 0 && <span className="bell-count">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="bell-menu">
          <div className="bell-head">
            <span>TERMINAL NOTIFICATIONS</span>
            <button className="ghost" onClick={dismissAll}>DISMISS ALL</button>
          </div>
          {items.length === 0 ? (
            <div className="empty" style={{ padding: 12 }}>NO NOTIFICATIONS.</div>
          ) : (
            items.slice(0, 10).map((a) => (
              <div key={a.event_key} className={`notification-item ${a.acked ? "acked" : ""} ${sevCls(a.severity)}`}>
                <div className="notif-head">
                  <span className={`badge sev ${sevCls(a.severity)}`}>{a.severity}</span>
                  <span className="notif-title">{a.title}</span>
                  <span className="dim notif-time">{String(a.created_at).slice(11, 19)}</span>
                </div>
                <div className="notif-msg">{a.message}</div>
                <div className="notif-actions">
                  {a.security_id && <button className="ghost" onClick={() => openDossier(a)}>OPEN DOSSIER</button>}
                  <button className="ghost" onClick={() => screenSimilar(a)}>SCREEN SIMILAR</button>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
