import { useState } from "react";
import { authLogin, authRegister } from "../api.js";

export default function AuthScreen({ mode, onSwitch, onSuccess }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isRegister = mode === "register";

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (isRegister && password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      if (isRegister) await authRegister(email, password);
      else await authLogin(email, password);
      onSuccess();
    } catch (err) {
      setError(err?.message || "Unable to complete the request.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="auth-brand">SV<span> · STOCK VERDICT</span></div>
        <div className="auth-title">{isRegister ? "Create your account" : "Sign in to the terminal"}</div>
        <div className="auth-sub">
          {isRegister
            ? "Research, Committee and simulated paper trading."
            : "Enter your credentials to open the terminal."}
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label>
            EMAIL
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
            />
          </label>
          <label>
            PASSWORD
            <input
              type="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              required
            />
          </label>
          {isRegister && (
            <label>
              CONFIRM PASSWORD
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                minLength={8}
                required
              />
            </label>
          )}

          {error && <div className="auth-error">{error}</div>}

          <button className="primary auth-submit" type="submit" disabled={busy}>
            {busy ? "…" : isRegister ? "REGISTER" : "LOGIN"}
          </button>
        </form>

        <div className="auth-switch">
          {isRegister ? (
            <span>
              Already have an account?{" "}
              <button className="ghost" onClick={() => onSwitch("login")}>LOG IN</button>
            </span>
          ) : (
            <span>
              New here?{" "}
              <button className="ghost" onClick={() => onSwitch("register")}>REGISTER</button>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
