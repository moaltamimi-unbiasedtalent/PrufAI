"use client";

import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../lib/auth";
import {
  feedbackApi,
  type FeedbackItem,
  type Pin,
} from "../../control-center/feedback/feedback-api";
import {
  classification,
  ClassificationFields,
} from "../../control-center/feedback/feedback-forms";
import css from "../assessment.module.css";

function ScopedFeedback({
  organisation,
  artifact,
}: {
  organisation: string;
  artifact: Pin;
}) {
  const [items, setItems] = useState<FeedbackItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const operations = useRef(new Map<string, string>());

  useEffect(() => {
    let active = true;
    setListError(null);
    void feedbackApi
      .tenantList(organisation, artifact.id)
      .then((value) => {
        if (active) setItems(value.items);
      })
      .catch((cause: unknown) => {
        if (active)
          setListError(
            cause instanceof Error
              ? cause.message
              : "Feedback could not be loaded.",
          );
      });
    return () => {
      active = false;
    };
  }, [organisation, artifact.id, reload]);

  return (
    <section className={css.card} aria-label="Writing assessment feedback">
      <h2>Writing assessment feedback</h2>
      <p>
        Record an observation against this exact assessment version. This form
        records the category and severity without copying the submitted Writing
        or review notes.
      </p>
      {listError ? (
        <div role="alert" className={css.error}>
          <p>{listError}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Retry feedback
          </button>
        </div>
      ) : !items ? (
        <p role="status">Loading assessment feedback…</p>
      ) : !items.length ? (
        <p>No feedback is recorded for this assessment version.</p>
      ) : (
        <ul aria-label="Recent feedback for this assessment version">
          {items.map((item) => (
            <li key={item.id}>
              {item.category} · {item.severity} · {item.status} ·{" "}
              <code>{item.id}</code>
            </li>
          ))}
        </ul>
      )}
      {saveError && (
        <p className={css.error} role="alert">
          {saveError}
        </p>
      )}
      {saved && (
        <p className={css.notice} role="status">
          Feedback recorded for this exact assessment version.
        </p>
      )}
      <form
        className={css.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (locked.current) return;
          const fields = new FormData(event.currentTarget);
          locked.current = true;
          setBusy(true);
          setSaved(false);
          setSaveError(null);
          void (async () => {
            const labels = classification(fields);
            const key = JSON.stringify(labels);
            const operation =
              operations.current.get(key) ?? crypto.randomUUID();
            operations.current.set(key, operation);
            await feedbackApi.tenantFlag(organisation, {
              ...labels,
              operation_id: operation,
              source: {
                kind: "REVIEWER_FLAG",
                artifact_type: "SUBJECTIVE_ASSESSMENT_VERSION",
                artifact,
              },
            });
            setSaved(true);
            setReload((value) => value + 1);
          })()
            .catch((cause: unknown) => {
              setSaveError(
                cause instanceof Error
                  ? cause.message
                  : "The observation could not be recorded.",
              );
            })
            .finally(() => {
              locked.current = false;
              setBusy(false);
            });
        }}
      >
        <ClassificationFields />
        <button disabled={busy}>Record Writing feedback</button>
      </form>
    </section>
  );
}

const reviewStates = [
  "AI_PROPOSED",
  "REVIEW_REQUIRED",
  "IN_REVIEW",
  "APPROVED",
  "SUPERSEDED",
  "REJECTED",
  "CHANGES_REQUESTED",
];

export default function WritingFeedbackPanel({
  artifact,
  status,
}: {
  artifact: Pin;
  status: string;
}) {
  const { user, organisationId, permissions } = useAuth();
  if (
    !user ||
    !organisationId ||
    !reviewStates.includes(status) ||
    !permissions.includes("subjective.read") ||
    !permissions.includes("subjective.review")
  )
    return null;
  return (
    <ScopedFeedback
      key={`${user.id}:${organisationId}:${artifact.id}:${artifact.checksum}`}
      organisation={organisationId}
      artifact={artifact}
    />
  );
}
