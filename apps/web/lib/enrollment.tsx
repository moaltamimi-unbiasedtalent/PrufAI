"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { authFetch } from "./api";
import { accountCard, accountPage, styles, useAuth } from "./auth";

export type EnrollmentOptions = {
  enabled: boolean;
  support_contact: string;
};

export async function enrollmentOptions(): Promise<EnrollmentOptions> {
  const response = await authFetch("/api/v1/auth/enrollment");
  if (!response.ok)
    throw new Error("Die Kontohilfe ist derzeit nicht erreichbar.");
  return (await response.json()) as EnrollmentOptions;
}

const messages: Record<string, string> = {
  INVALID_INPUT: "Prüfen Sie Ihre Eingaben und versuchen Sie es erneut.",
  INVALID_PASSWORD: "Wählen Sie ein Passwort mit 12 bis 128 Zeichen.",
  INVITATION_INVALID: "Diese Einladung ist ungültig oder wurde zurückgezogen.",
  INVITATION_EXPIRED:
    "Diese Einladung ist abgelaufen. Bitten Sie Ihre Einrichtung um eine neue Einladung.",
  INVITATION_USED:
    "Diese Einladung wurde bereits verwendet. Sie können sich anmelden.",
  INVITATION_WRONG_INSTITUTION:
    "Diese Einladung gehört zu einer anderen Einrichtung.",
  INVITATION_ROLE_DENIED: "Diese Einladung ist für die Teilnahme nicht gültig.",
  INVITATION_ACCOUNT_EXISTS:
    "Für diese Einladung besteht bereits ein Konto. Melden Sie sich an oder wenden Sie sich an Ihre Einrichtung.",
  INVITATION_DENIED:
    "Sie dürfen für diese Einrichtung keine Einladungen erstellen.",
  RESET_INVALID: "Dieser Wiederherstellungscode ist ungültig.",
  RESET_EXPIRED:
    "Dieser Wiederherstellungscode ist abgelaufen. Fordern Sie einen neuen Code an.",
  RESET_USED: "Dieser Wiederherstellungscode wurde bereits verwendet.",
  TRY_LATER: "Zu viele Versuche. Bitte versuchen Sie es später erneut.",
  DELIVERY_DISABLED:
    "Einladungen und Kontowiederherstellung sind derzeit nicht verfügbar.",
};

export async function enrollmentFailure(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: { code?: string };
  } | null;
  return (
    messages[body?.detail?.code ?? ""] ??
    "Die Anfrage konnte nicht abgeschlossen werden. Bitte versuchen Sie es erneut."
  );
}

export function EnrollmentSupport({ contact }: { contact?: string }) {
  return (
    <p>
      {contact ? (
        <>Kontakt für Kontohilfe: {contact}</>
      ) : (
        "Wenden Sie sich bei Fragen an die Einrichtung, die Sie eingeladen hat."
      )}
    </p>
  );
}

const flows = {
  invitation: {
    title: "Einladung annehmen",
    action: "Konto erstellen",
    path: "/api/v1/auth/invitations/accept",
    success: "Ihr Konto wurde erstellt. Sie können sich jetzt anmelden.",
  },
  request: {
    title: "Kontozugang wiederherstellen",
    action: "Wiederherstellung anfragen",
    path: "/api/v1/auth/password-reset/request",
    success:
      "Ihre Anfrage wurde angenommen. Eine Zustellung wird hier nicht bestätigt.",
  },
  confirm: {
    title: "Neues Passwort festlegen",
    action: "Passwort ändern",
    path: "/api/v1/auth/password-reset/confirm",
    success:
      "Ihr Passwort wurde geändert. Melden Sie sich mit dem neuen Passwort an.",
  },
} as const;

export default function EnrollmentForm({ flow }: { flow: keyof typeof flows }) {
  const { clearRecoveredSession } = useAuth();
  const { title, action, path, success } = flows[flow];
  const [options, setOptions] = useState<EnrollmentOptions>();
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<"idle" | "submitting" | "done">("idle");
  const [error, setError] = useState("");
  const [cleanupWarning, setCleanupWarning] = useState(false);
  const notice = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    let active = true;
    void enrollmentOptions()
      .then((value) => {
        if (active) setOptions(value);
      })
      .catch(() => {
        if (active) setError("Die Kontohilfe ist derzeit nicht erreichbar.");
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  useEffect(() => {
    if (error || status === "done") notice.current?.focus();
  }, [error, status]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!options?.enabled || status !== "idle") return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const password = String(fields.get("password") ?? "");
    if (flow !== "request" && fields.get("confirmation") !== password) {
      setError("Die beiden Passwörter stimmen nicht überein.");
      return;
    }
    const token = String(fields.get("token") ?? "").trim();
    const body =
      flow === "request"
        ? { email: fields.get("email") }
        : flow === "invitation"
          ? {
              token,
              organisation_id: String(fields.get("organisation") ?? "").trim(),
              password,
            }
          : { token, new_password: password };
    setError("");
    setStatus("submitting");
    try {
      const response = await authFetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        setError(await enrollmentFailure(response));
        setStatus("idle");
        return;
      }
      form.reset();
      if (flow === "confirm")
        setCleanupWarning((await clearRecoveredSession?.()) === false);
      setStatus("done");
    } catch {
      setError(
        "Die Anfrage konnte nicht bestätigt werden. Bitte versuchen Sie es erneut.",
      );
      setStatus("idle");
    }
  }

  return (
    <main style={{ ...styles.center, ...accountPage }} lang="de">
      <section style={{ ...styles.card, ...accountCard }}>
        <p style={styles.kicker}>PrüfAI</p>
        <h1>{title}</h1>
        {flow === "invitation" && (
          <p>
            Diese Einladung gilt für eine nicht offizielle Übung in Ihrer
            Einrichtung.
          </p>
        )}
        {error && (
          <p ref={notice} role="alert" tabIndex={-1}>
            {error}
          </p>
        )}
        {cleanupWarning && (
          <p role="alert">
            Die lokalen Entwürfe konnten nicht gelöscht werden. Schließen Sie
            dieses Fenster auf einem gemeinsam genutzten Gerät.
          </p>
        )}
        {status === "done" ? (
          <p ref={notice} role="status" tabIndex={-1}>
            {success}
          </p>
        ) : !options ? (
          error ? (
            <button
              type="button"
              onClick={() => {
                setError("");
                setAttempt((value) => value + 1);
              }}
            >
              Erneut versuchen
            </button>
          ) : (
            <p role="status">Kontohilfe wird geladen …</p>
          )
        ) : options.enabled ? (
          <form onSubmit={submit}>
            <fieldset
              disabled={status === "submitting"}
              style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
            >
              {flow === "request" ? (
                <label style={labelStyle}>
                  E-Mail-Adresse
                  <input
                    style={inputStyle}
                    name="email"
                    type="email"
                    required
                    autoComplete="username"
                  />
                </label>
              ) : (
                <>
                  {flow === "invitation" && (
                    <label style={labelStyle}>
                      Einrichtungs-ID
                      <input
                        style={inputStyle}
                        name="organisation"
                        required
                        maxLength={36}
                        autoComplete="off"
                      />
                    </label>
                  )}
                  <label style={labelStyle}>
                    {flow === "invitation"
                      ? "Einladungscode"
                      : "Wiederherstellungscode"}
                    <input
                      style={inputStyle}
                      name="token"
                      type="password"
                      required
                      maxLength={128}
                      autoComplete="off"
                    />
                  </label>
                  <label style={labelStyle}>
                    Neues Passwort
                    <input
                      style={inputStyle}
                      name="password"
                      type="password"
                      required
                      minLength={12}
                      maxLength={128}
                      autoComplete="new-password"
                    />
                  </label>
                  <label style={labelStyle}>
                    Passwort wiederholen
                    <input
                      style={inputStyle}
                      name="confirmation"
                      type="password"
                      required
                      minLength={12}
                      maxLength={128}
                      autoComplete="new-password"
                    />
                  </label>
                </>
              )}
              <button
                style={{ ...styles.primary, marginTop: "1rem" }}
                type="submit"
              >
                {status === "submitting" ? "Bitte warten …" : action}
              </button>
            </fieldset>
          </form>
        ) : (
          <p role="status">{messages.DELIVERY_DISABLED}</p>
        )}
        {flow === "request" && (
          <p>
            <Link href="/konto/neues-passwort">
              Wiederherstellungscode eingeben
            </Link>
          </p>
        )}
        {flow === "confirm" && (
          <p>
            <Link href="/konto/wiederherstellen">Neuen Code anfragen</Link>
          </p>
        )}
        <EnrollmentSupport contact={options?.support_contact} />
        <p>
          <Link href="/anmelden">Zur Anmeldung</Link>
        </p>
      </section>
    </main>
  );
}

const labelStyle = { display: "grid", gap: "0.35rem", marginTop: "0.8rem" };
const inputStyle = {
  padding: "0.7rem",
  border: "1px solid #bbc6dc",
  borderRadius: "0.35rem",
  minWidth: 0,
};
