"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { authenticatedFetch } from "../../../lib/api";
import { useAuth } from "../../../lib/auth";
import {
  enrollmentFailure,
  enrollmentOptions,
  EnrollmentSupport,
  type EnrollmentOptions,
} from "../../../lib/enrollment";

export default function InstitutionEnrollmentPage() {
  const { organisationId, permissions, membership } = useAuth();
  const allowed = Boolean(
    organisationId && permissions.includes("membership.manage"),
  );
  const [options, setOptions] = useState<EnrollmentOptions>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean }>();
  const noticeRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    let active = true;
    if (allowed)
      void enrollmentOptions()
        .then((value) => {
          if (active) setOptions(value);
        })
        .catch(() => {
          if (active)
            setNotice({
              text: "Einladungen sind derzeit nicht erreichbar.",
              error: true,
            });
        });
    return () => {
      active = false;
    };
  }, [allowed]);

  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!allowed || !options?.enabled || busy || !organisationId) return;
    const form = event.currentTarget;
    const email = new FormData(form).get("email");
    setBusy(true);
    setNotice(undefined);
    try {
      const response = await authenticatedFetch(
        "/api/v1/organisations/" + organisationId + "/invitations",
        {
          method: "POST",
          body: JSON.stringify({ email }),
        },
      );
      if (!response.ok) {
        setNotice({ text: await enrollmentFailure(response), error: true });
        return;
      }
      form.reset();
      setNotice({
        text: "Die Einladung wurde erstellt. Eine Zustellung wird hier nicht bestätigt.",
        error: false,
      });
    } catch {
      setNotice({
        text: "Die Einladung konnte nicht bestätigt werden. Bitte prüfen Sie den Vorgang vor einem weiteren Versuch.",
        error: true,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section lang="de">
      <h1>Person zur Übung einladen</h1>
      {membership && <p>Einrichtung: {membership.organisation_name}</p>}
      {notice && (
        <p
          role={notice.error ? "alert" : "status"}
          tabIndex={-1}
          ref={noticeRef}
        >
          {notice.text}
        </p>
      )}
      {!allowed ? (
        <p role="alert">
          Die Berechtigung zur Mitgliederverwaltung ist erforderlich.
        </p>
      ) : options?.enabled ? (
        <form onSubmit={submit}>
          <label>
            E-Mail-Adresse{" "}
            <input
              name="email"
              type="email"
              required
              autoComplete="off"
              disabled={busy}
            />
          </label>{" "}
          <button type="submit" disabled={busy}>
            Einladung erstellen
          </button>
        </form>
      ) : options ? (
        <p role="status">Einladungen sind derzeit nicht verfügbar.</p>
      ) : (
        !notice && <p role="status">Einladungsstatus wird geladen …</p>
      )}
      <EnrollmentSupport contact={options?.support_contact} />
    </section>
  );
}
