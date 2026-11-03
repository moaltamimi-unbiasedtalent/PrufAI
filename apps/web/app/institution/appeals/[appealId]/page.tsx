"use client";

import { useAuth } from "../../../../lib/auth";

import Link from "next/link";
import React, { FormEvent, useEffect, useRef, useState } from "react";
import {
  appealActions,
  appealApi,
  appealEvidenceComplete,
  canInspectAppealEvidence,
  type AppealDetail,
  type AppealEvidence,
} from "../appeal-api";
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
  const [evidence, setEvidence] = useState<AppealEvidence | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidenceReload, setEvidenceReload] = useState(0);
  const canInspectEvidence = canInspectAppealEvidence(permissions);
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
  useEffect(() => {
    let cancelled = false;
    setEvidence(null);
    setEvidenceError(null);
    if (!organisationId || !appeal || !canInspectEvidence) return;
    void appealApi
      .evidence(organisationId, appeal.id)
      .then((value) => {
        if (!cancelled) setEvidence(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setEvidenceError(
            cause instanceof Error
              ? cause.message
              : "The exact challenged response and score evidence is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [organisationId, appeal, canInspectEvidence, evidenceReload]);
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
      !canInspectEvidence ||
      !appealEvidenceComplete(appeal, evidence) ||
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
    <main
      style={{
        maxWidth: 960,
        margin: "0 auto",
        padding: "3rem 1.5rem",
        overflowWrap: "anywhere",
      }}
    >
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
          <section aria-labelledby="objective-evidence" className={css.card}>
            <h2 id="objective-evidence">
              Challenged objective response and score
            </h2>
            <p>
              Read the original item version and submitted response revision
              pinned by the challenged result. These are historical scoring
              records, not the latest question or a newly calculated score.
            </p>
            {!canInspectEvidence ? (
              <p role="alert">
                Appeal adjudication, result read and Question Bank read
                permissions are required to inspect this private evidence before
                deciding.
              </p>
            ) : evidenceError ? (
              <div className={css.error} role="alert">
                <p>{evidenceError}</p>
                <button onClick={() => setEvidenceReload((value) => value + 1)}>
                  Retry challenged evidence
                </button>
              </div>
            ) : !evidence ? (
              <p role="status">
                Loading original objective response and score evidence…
              </p>
            ) : (
              <>
                <AppealEvidencePanel evidence={evidence} />
                {!appealEvidenceComplete(appeal, evidence) && (
                  <p className={css.error} role="alert">
                    The complete evidence does not match the pinned appeal
                    bundle, or includes unsupported or undisplayed items. This
                    objective review screen cannot adjudicate it. Refresh the
                    appeal and evidence before continuing.
                  </p>
                )}
              </>
            )}
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
          {appealActions(appeal, permissions).reject &&
            canInspectEvidence &&
            appealEvidenceComplete(appeal, evidence) && (
              <form
                className={`${css.form} ${css.card}`}
                onSubmit={(event) => void reject(event)}
              >
                <h2>Adjudicate reviewed appeal</h2>
                <p>
                  This bounded workflow records a reasoned rejection against the
                  exact current appeal version and evidence checksum. It does
                  not change the score.
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

function AppealEvidencePanel({ evidence }: { evidence: AppealEvidence }) {
  return (
    <>
      <p>
        Displayed objective items: {evidence.items.length} of{" "}
        {evidence.total_items}.
      </p>
      {(evidence.truncated ||
        evidence.items.some((item) => item.content_truncated)) && (
        <p role="alert">
          This bounded panel does not display the complete original content. Do
          not use this partial evidence to adjudicate the appeal.
        </p>
      )}
      {evidence.unsupported_items > 0 && (
        <p role="status">
          Unsupported subjective or unavailable evidence items:{" "}
          {evidence.unsupported_items}. Writing, audio and subjective evidence
          are not displayed by this objective review panel.
        </p>
      )}
      {evidence.items.length === 0 && (
        <p>No supported objective scoring evidence is available.</p>
      )}
      <dl className={css.provenance}>
        <dt>Challenged result version</dt>
        <dd>{evidence.result_version_id}</dd>
        <dt>Result checksum</dt>
        <dd>{evidence.result_checksum}</dd>
        <dt>Scoring run</dt>
        <dd>{evidence.scoring_run_id}</dd>
        <dt>Scoring-run checksum</dt>
        <dd>{evidence.scoring_run_checksum}</dd>
        <dt>Exact exam version</dt>
        <dd>{evidence.exam_version_id}</dd>
        <dt>Exact Standard Pack version</dt>
        <dd>{evidence.standard_pack_version_id}</dd>
        <dt>Pinned evidence checksum</dt>
        <dd>{evidence.pinned_evidence_bundle_checksum}</dd>
        <dt>Current evidence checksum</dt>
        <dd>{evidence.evidence_bundle_checksum}</dd>
      </dl>
      {evidence.items.map((item, index) => {
        const selected =
          item.response?.option_ids ??
          (item.response?.option_id ? [item.response.option_id] : []);
        const label = (optionId: string) =>
          `${optionId}: ${item.options.find((option) => option.option_id === optionId)?.display_text ?? "Original option label unavailable"}`;
        return (
          <article
            key={`${item.binding_id}:${item.response_revision_id}`}
            className={css.card}
          >
            <h3>Original objective item {index + 1}</h3>
            <p>{item.prompt || "Original prompt unavailable"}</p>
            <h4>Original options</h4>
            <ul>
              {item.options.map((option) => (
                <li key={option.option_id}>
                  {option.option_id}: {option.display_text}
                </li>
              ))}
            </ul>
            <p>
              <strong>Submitted selection:</strong>{" "}
              {selected.length
                ? selected.map(label).join("; ")
                : "No submitted selection"}
            </p>
            <p>
              <strong>Expected selection:</strong>{" "}
              {item.correct_option_ids.length
                ? item.correct_option_ids.map(label).join("; ")
                : "No expected option is recorded"}
            </p>
            <p>
              <strong>Recorded item score:</strong>{" "}
              {item.awarded_points ?? "Unavailable"} / {item.maximum_points} ·{" "}
              {item.status}
            </p>
            <h4>Pinned scoring policy</h4>
            <pre className={css.evidence}>
              {typeof item.scoring_policy === "string"
                ? item.scoring_policy
                : JSON.stringify(item.scoring_policy, null, 2)}
            </pre>
            <details>
              <summary>Exact item and response provenance</summary>
              <dl className={css.provenance}>
                <dt>Runtime binding</dt>
                <dd>{item.binding_id}</dd>
                <dt>Item version</dt>
                <dd>{item.item_version_id}</dd>
                <dt>Item checksum</dt>
                <dd>{item.item_checksum}</dd>
                <dt>Response</dt>
                <dd>{item.response_id ?? "No submitted response"}</dd>
                <dt>Submitted response revision</dt>
                <dd>{item.response_revision_id ?? "No submitted revision"}</dd>
                <dt>Revision number</dt>
                <dd>{item.response_revision_number ?? "Not applicable"}</dd>
                <dt>Response checksum</dt>
                <dd>{item.response_checksum ?? "Not applicable"}</dd>
              </dl>
            </details>
          </article>
        );
      })}
    </>
  );
}
