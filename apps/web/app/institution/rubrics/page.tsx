"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useAuth } from "../../../lib/auth";
import {
  writingFoundationApi,
  type RubricSummary,
} from "../writing-reviews/foundation-api";
import { GovernanceError } from "../writing-reviews/governance-ui";
import css from "../assessment.module.css";

export default function InstitutionRubricsPage() {
  const { organisationId, permissions } = useAuth();
  const [rubrics, setRubrics] = useState<RubricSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const allowed = permissions.includes("rubric.read");
  useEffect(() => {
    let cancelled = false;
    setRubrics(null);
    setError(null);
    if (!organisationId || !allowed) return;
    void writingFoundationApi
      .rubrics(organisationId)
      .then((value) => {
        if (!cancelled) setRubrics(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Rubrics could not be loaded.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [organisationId, allowed, reload]);
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/institution/writing-reviews">← Writing review</Link>
        {" · "}
        <Link href="/institution/speaking-reviews">Speaking review →</Link>
      </p>
      <h1>Writing and Speaking rubric governance</h1>
      <p>
        Inspect exact rubric versions and their declared criteria before
        requesting human review, approval and publication. Published versions
        remain historical evidence.
      </p>
      {!allowed || !organisationId ? (
        <p role="alert">
          Rubric read permission in an active institution is required.
        </p>
      ) : error ? (
        <GovernanceError
          error={error}
          retry={() => setReload((value) => value + 1)}
        />
      ) : !rubrics ? (
        <p role="status">Loading governed rubric versions…</p>
      ) : !rubrics.length ? (
        <p>No rubrics are available in this institution.</p>
      ) : (
        rubrics.map((rubric) => (
          <article className={css.card} key={rubric.id}>
            <h2>{rubric.name}</h2>
            <p>Code: {rubric.code}</p>
            {!rubric.versions.length && (
              <p>No rubric version has been created.</p>
            )}
            <ul>
              {rubric.versions.map((version) => (
                <li key={version.id}>
                  <Link
                    href={`/institution/rubrics/${rubric.id}/versions/${version.id}`}
                  >
                    Inspect rubric {version.version_label}
                  </Link>{" "}
                  · {version.status} · {version.skill}
                </li>
              ))}
            </ul>
          </article>
        ))
      )}
    </section>
  );
}
