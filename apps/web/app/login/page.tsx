"use client";

import { FormEvent, useState } from "react";
import { ACCESS_TOKEN_KEY, authenticatedFetch } from "../../lib/api";
import { styles } from "../../lib/auth";

export default function LoginPage() {
  const [email, setEmail] = useState("candidate@demo.prufai.local");
  const [password, setPassword] = useState("Demo-PrufAI-2026!");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await authenticatedFetch("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok)
        throw new Error(
          "Sign in failed. Check the demo account and try again.",
        );
      const result = (await response.json()) as { access_token: string };
      window.sessionStorage.setItem(ACCESS_TOKEN_KEY, result.access_token);
      window.location.assign("/");
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
        <p style={styles.kicker}>PrüfAI local demo</p>
        <h1>Sign in</h1>
        <p>
          These are synthetic development accounts. They are not production
          credentials.
        </p>
        <label style={{ display: "grid", gap: "0.35rem", marginTop: "1rem" }}>
          Email
          <input
            required
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
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            style={inputStyle}
          />
        </label>
        {error && (
          <p role="alert" style={{ color: "#a42b2b" }}>
            {error}
          </p>
        )}
        <button
          disabled={busy}
          style={{
            ...styles.primary,
            border: 0,
            marginTop: "1.4rem",
            cursor: busy ? "wait" : "pointer",
          }}
        >
          {busy ? "Signing in…" : "Open demo workspace"}
        </button>
        <p style={{ fontSize: "0.85rem", marginTop: "1.5rem" }}>
          Demo accounts are documented in{" "}
          <a href="/docs">the walkthrough guide</a> for local operators.
        </p>
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
