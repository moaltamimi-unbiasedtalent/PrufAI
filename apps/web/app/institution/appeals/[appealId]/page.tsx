"use client";

import { useAuth } from "../../../../lib/auth";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { appealActions, appealApi, type AppealDetail } from "../appeal-api";
import css from "../../assessment.module.css";

type Props = { params: Promise<{ appealId: string }> };

export default function AppealWorkspacePage({ params }: Props) {
  const [appeal, setAppeal] = useState<AppealDetail | null>(null);
  const [message, setMessage] = useState("Loading appeal…");
  const { organisationId, permissions } = useAuth();
  const [reload, setReload] = useState(0);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operations = useRef<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    setAppeal(null);
    setError(null);
    setMessage("Loading appeal…");
    void params.then(({ appealId }) => {
      if (!organisationId) {
        setMessage("Organisation context is required.");
        return;
      }
      appealApi
        .detail(organisationId, appealId)
        .then((value) => {
          if (!cancelled) {
            setAppeal(value);
            setMessage("");
          }
        })
        .catch((cause: unknown) => {
          if (!cancelled) {
            setMessage("");
            setError(
              cause instanceof Error
                ? cause.message
                : "Appeal could not be loaded.",
            );
          }
        });
    });
    return () => {
      cancelled = true;
    };
  }, [organisationId, params, reload]);
  async function startReview() {
    if (
      !appeal ||
      !organisationId ||
      busy ||
      !appealActions(appeal, permissions).review
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await appealApi.review(organisationId, appeal.id);
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The appeal changed; refresh before continuing.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function reject(event: FormEvent) {
    event.preventDefault();
    if (
      !appeal ||
      !organisationId ||
      busy ||
      !reason.trim() ||
      !appealActions(appeal, permissions).reject
    )
      return;
    setBusy(true);
    setError(null);
    const key = `${appeal.id}:${appeal.current_checksum}:${reason.trim()}`;
    const operation = (operations.current[key] ??= crypto.randomUUID());
    try {
      await appealApi.reject(organisationId, appeal, reason.trim(), operation);
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Adjudication could not be confirmed. Refresh before continuing.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution review
      </p>
      <h1>Appeal workspace</h1>
      <p>
        <Link href="/institution/appeals">← Appeals queue</Link>
      </p>
      {message && <p role="status">{message}</p>}
      {error && (
        <div className={css.error} role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Refresh appeal
          </button>
        </div>
      )}
      {appeal && (
        <>
          <section aria-labelledby="identity">
            <h2 id="identity">Pinned result evidence</h2>
            <dl>
              <dt>Appeal</dt>
              <dd>{appeal.id}</dd>
              <dt>Status</dt>
              <dd>{appeal.status}</dd>
              <dt>Result version</dt>
              <dd>{appeal.challenged_result_version_id}</dd>
              <dt>Result checksum</dt>
              <dd>{appeal.challenged_result_checksum}</dd>
              <dt>Evidence checksum</dt>
              <dd>{appeal.evidence_bundle_checksum ?? "Not available"}</dd>
            </dl>
          </section>
          <section aria-labelledby="grounds">
            <h2 id="grounds">Candidate grounds</h2>
            <p>{appeal.grounds}</p>
          </section>
          <section aria-labelledby="correction">
            <h2 id="correction">Correction status</h2>
            <p>
              {appeal.correction
                ? `${appeal.correction.type}: ${appeal.correction.status}`
                : "No correction initiated"}
            </p>
            {appeal.correction?.new_result_version_id && (
              <p>
                New result version: {appeal.correction.new_result_version_id}
              </p>
            )}
          </section>
          {appealActions(appeal, permissions).review && (
            <button
              disabled={busy}
              type="button"
              onClick={() => void startReview()}
            >
              Start review
            </button>
          )}
          {appealActions(appeal, permissions).reject && (
            <form
              className={`${css.form} ${css.card}`}
              onSubmit={(event) => void reject(event)}
            >
              <h2>Adjudicate reviewed appeal</h2>
              <p>
                This bounded workflow records a reasoned rejection against the
                exact current appeal version and evidence checksum. It does not
                change the score.
              </p>
              <label>
                Decision reason
                <textarea
                  required
                  rows={5}
                  maxLength={10000}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <button disabled={busy || !reason.trim()}>
                Reject reviewed appeal
              </button>
            </form>
          )}
          {appeal.decision && (
            <section className={css.card}>
              <h2>Recorded human decision</h2>
              <p>Decision: {appeal.decision}</p>
              <p>Reason: {appeal.decision_reason}</p>
              {appeal.decision === "REJECT" && (
                <p>
                  No correction or successor release is created by rejection.
                </p>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}
