"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { accountCard, accountPage, styles, useAuth } from "../../lib/auth";
import { workspaceLanding } from "../../lib/access";
import { signInFailureMessage } from "../../lib/sign-in-failure";
import { signOutNoticeMessage } from "../../lib/sign-out-failure";

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
    signOutNotice,
    signingOut,
  } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const switchPending = useRef(false);
  const alertRef = useRef<HTMLParagraphElement>(null);
  const notice = signOutNotice
    ? signOutNoticeMessage(signOutNotice, german)
    : null;
  const alert = error || notice || sessionError;
  useEffect(() => {
    if (alert) alertRef.current?.focus();
  }, [alert]);
  async function switchAccount() {
    if (switchPending.current) return;
    switchPending.current = true;
    setError(null);
    try {
      await signOut();
      setError(null);
    } catch {
      setError(signOutNoticeMessage("unconfirmed", german));
    } finally {
      switchPending.current = false;
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const landing = await signIn(email, password, german);
      setPassword("");
      router.replace(landing);
    } catch (cause) {
      setError(signInFailureMessage(cause, german));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main
      style={{ ...styles.center, ...accountPage }}
      lang={german ? "de" : "en"}
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
              disabled={signingOut}
              onClick={() => router.replace(workspaceLanding(user, membership))}
            >
              {german ? "Arbeitsbereich öffnen" : "Open workspace"}
            </button>{" "}
            <button
              type="button"
              disabled={signingOut}
              onClick={() => void switchAccount()}
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
        {alert && (
          <p
            ref={alertRef}
            role="alert"
            tabIndex={-1}
            lang={error || notice ? (german ? "de" : "en") : "en"}
            style={{ color: "#a42b2b" }}
          >
            {alert}
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
