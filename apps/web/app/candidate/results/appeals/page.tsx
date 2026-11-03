"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import {
  appealApi,
  type AppealDetail,
} from "../../../institution/appeals/appeal-api";
import css from "../../../institution/assessment.module.css";

export default function CandidateAppealsPage() {
  const [appeals, setAppeals] = useState<AppealDetail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setAppeals(null);
    setError(null);
    void appealApi
      .candidateList()
      .then((items) => {
        if (!cancelled) setAppeals(items);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Appeal history is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);
  return (
    <section>
      <p>
        <Link href="/candidate/results">← My released results</Link>
      </p>
      <h1>My appeal history</h1>
      <p>
        Appeals remain linked to the original result version. A rejected appeal
        does not create a corrected result.
      </p>
      {error && (
        <div className={css.error} role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Retry appeal history
          </button>
        </div>
      )}
      {!appeals && !error && <p role="status">Loading your appeal history…</p>}
      {appeals && !appeals.length && (
        <p>No appeals have been submitted by this account.</p>
      )}
      {appeals?.map((appeal) => (
        <article className={css.card} key={appeal.id}>
          <h2>{appeal.category}</h2>
          <p>Status: {appeal.status}</p>
          <p>
            Challenged result version: {appeal.challenged_result_version_id}
          </p>
          <p>Decision: {appeal.decision ?? "Awaiting human decision"}</p>
          <Link href={`/candidate/results/appeals/${appeal.id}`}>
            Inspect appeal and decision →
          </Link>
        </article>
      ))}
    </section>
  );
}
