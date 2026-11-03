"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { styles, useAuth } from "../../lib/auth";
import { workspaceLanding } from "../../lib/access";

export default function LoginPage() {
  const router = useRouter();
  const {
    signIn,
    signOut,
    user,
    membership,
    loading,
    demoMode,
    error: sessionError,
  } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const landing = await signIn(email, password);
      setPassword("");
      router.replace(landing);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main style={styles.center}>
      <form
        onSubmit={submit}
        style={{ ...styles.card, width: "min(100%, 440px)" }}
      >
        <p style={styles.kicker}>PrüfAI</p>
        <h1>Sign in</h1>
        {loading && <p role="status">Restoring your session…</p>}
        {user && (
          <p>
            Signed in as {user.email}.{" "}
            <button
              type="button"
              onClick={() => router.replace(workspaceLanding(user, membership))}
            >
              Open workspace
            </button>{" "}
            <button
              type="button"
              onClick={() =>
                void signOut().catch((cause) => setError(String(cause)))
              }
            >
              Sign out to switch account
            </button>
          </p>
        )}
        <p>Enter your account email and password.</p>
        <label style={{ display: "grid", gap: "0.35rem", marginTop: "1rem" }}>
          Email
          <input
            required
            type="email"
            autoComplete="username"
            disabled={busy || loading || Boolean(user)}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={{ display: "grid", gap: "0.35rem", marginTop: "1rem" }}>
          Password
          <input
            required
            type="password"
            autoComplete="current-password"
            disabled={busy || loading || Boolean(user)}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            style={inputStyle}
          />
        </label>
        {(error || sessionError) && (
          <p role="alert" style={{ color: "#a42b2b" }}>
            {error || sessionError}
          </p>
        )}
        <button
          disabled={busy || loading || Boolean(user)}
          style={{
            ...styles.primary,
            border: 0,
            marginTop: "1.4rem",
            cursor: busy ? "wait" : "pointer",
          }}
        >
          {busy ? "Signing in…" : "Sign in to workspace"}
        </button>
        {demoMode && (
          <p style={{ fontSize: "0.85rem", marginTop: "1.5rem" }}>
            Synthetic development accounts are documented in{" "}
            <a href="/docs">the walkthrough guide</a> for local operators.
          </p>
        )}
      </form>
    </main>
  );
}

const inputStyle = {
  padding: "0.7rem",
  border: "1px solid #bbc6dc",
  borderRadius: "0.35rem",
  fontSize: "1rem",
};
