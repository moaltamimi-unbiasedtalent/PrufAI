"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  accountCard,
  accountPage,
  signInUnavailable,
  styles,
  useAuth,
} from "../../lib/auth";
import { workspaceLanding } from "../../lib/access";

export default function LoginForm({ german = false }: { german?: boolean }) {
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
  const alertRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (error) alertRef.current?.focus();
  }, [error]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const landing = await signIn(email, password, german);
      setPassword("");
      router.replace(landing);
    } catch (cause) {
      setError(
        german
          ? cause instanceof Error && cause.message === signInUnavailable
            ? signInUnavailable
            : "Die Anmeldung ist fehlgeschlagen. Prüfen Sie Ihre Eingaben oder versuchen Sie es später erneut."
          : cause instanceof Error
            ? cause.message
            : "Sign in failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main
      style={{ ...styles.center, ...accountPage }}
      lang={german ? "de" : undefined}
    >
      <form onSubmit={submit} style={{ ...styles.card, ...accountCard }}>
        <p style={styles.kicker}>PrüfAI</p>
        <h1>{german ? "Anmelden" : "Sign in"}</h1>
        {loading && (
          <p role="status">
            {german ? "Ihre Sitzung wird geladen …" : "Restoring your session…"}
          </p>
        )}
        {user && (
          <p>
            {german ? "Angemeldet als" : "Signed in as"} {user.email}.{" "}
            <button
              type="button"
              onClick={() => router.replace(workspaceLanding(user, membership))}
            >
              {german ? "Arbeitsbereich öffnen" : "Open workspace"}
            </button>{" "}
            <button
              type="button"
              onClick={() =>
                void signOut().catch((cause) => setError(String(cause)))
              }
            >
              {german ? "Abmelden" : "Sign out to switch account"}
            </button>
          </p>
        )}
        <p>
          {german
            ? "Geben Sie Ihre E-Mail-Adresse und Ihr Passwort ein."
            : "Enter your account email and password."}
        </p>
        <label style={{ display: "grid", gap: "0.35rem", marginTop: "1rem" }}>
          {german ? "E-Mail-Adresse" : "Email"}
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
          {german ? "Passwort" : "Password"}
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
          <p
            ref={alertRef}
            role="alert"
            tabIndex={-1}
            lang={error === signInUnavailable ? "de" : undefined}
            style={{ color: "#a42b2b" }}
          >
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
          {german
            ? busy
              ? "Anmeldung läuft …"
              : "Anmelden"
            : busy
              ? "Signing in…"
              : "Sign in to workspace"}
        </button>
        <p lang="de">
          <Link href="/einladung">Einladung annehmen</Link>
        </p>
        <p lang="de">
          <Link href="/konto/wiederherstellen">Passwort vergessen</Link>
        </p>
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
  minWidth: 0,
};
