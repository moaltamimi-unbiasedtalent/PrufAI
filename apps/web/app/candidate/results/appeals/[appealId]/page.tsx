"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import {
  appealApi,
  type AppealDetail,
} from "../../../../institution/appeals/appeal-api";
import css from "../../../../institution/assessment.module.css";

export default function CandidateAppealDetailPage({
  params,
}: {
  params: Promise<{ appealId: string }>;
}) {
  const [appeal, setAppeal] = useState<AppealDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setAppeal(null);
    setError(null);
    void params
      .then(({ appealId }) => appealApi.candidateDetail(appealId))
      .then((item) => {
        if (!cancelled) setAppeal(item);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "This appeal is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [params, reload]);
  return (
    <section>
      <p>
        <Link href="/candidate/results/appeals">← My appeal history</Link>
      </p>
      <h1>My appeal and decision</h1>
      {error && (
        <div className={css.error} role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Retry appeal
          </button>
        </div>
      )}
      {!appeal && !error && <p role="status">Loading your exact appeal…</p>}
      {appeal && (
        <article className={css.card}>
          <p>Status: {appeal.status}</p>
          <h2>{appeal.category}</h2>
          <p>{appeal.grounds}</p>
          <h2>Human decision</h2>
          <p>Decision: {appeal.decision ?? "Awaiting human decision"}</p>
          <p>
            Decision reason:{" "}
            {appeal.decision_reason ?? "No decision has been recorded."}
          </p>
          {appeal.decision === "REJECT" && (
            <p className={css.notice}>
              This appeal was rejected. The challenged result remains unchanged;
              no correction or successor release was created by this decision.
            </p>
          )}
          <dl className={css.provenance}>
            <dt>Appeal reference</dt>
            <dd>{appeal.id}</dd>
            <dt>Exact result version</dt>
            <dd>{appeal.challenged_result_version_id}</dd>
            <dt>Result checksum</dt>
            <dd>{appeal.challenged_result_checksum}</dd>
            <dt>Appeal version</dt>
            <dd>{appeal.current_version_number}</dd>
            <dt>Appeal checksum</dt>
            <dd>{appeal.current_checksum}</dd>
          </dl>
        </article>
      )}
    </section>
  );
}
