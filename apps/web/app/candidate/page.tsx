"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import {
  candidateApi,
  type CandidateAssignment,
  CandidateApiError,
} from "./api";

function availabilityLabel(
  assignment: CandidateAssignment,
  now: number,
): string {
  if (["CANCELLED", "EXPIRED", "COMPLETED"].includes(assignment.status)) {
    return assignment.status[0] + assignment.status.slice(1).toLowerCase();
  }
  if (
    assignment.available_from &&
    Date.parse(assignment.available_from) > now
  ) {
    return "Not yet available";
  }
  if (
    assignment.available_until &&
    Date.parse(assignment.available_until) <= now
  ) {
    return "Expired";
  }
  return "Available";
}

function actionFor(
  assignment: CandidateAssignment,
): { label: string; href: string } | { label: string; disabled: true } {
  const latest = assignment.attempts.at(-1);
  if (latest?.status === "IN_PROGRESS")
    return { label: "Resume", href: `/candidate/exams/${latest.id}` };
  if (latest?.status === "SUBMITTED")
    return {
      label: "View submitted state",
      href: `/candidate/exams/${latest.id}`,
    };
  if (latest?.status === "EXPIRED") return { label: "Expired", disabled: true };
  if (assignment.status === "CANCELLED")
    return { label: "Cancelled", disabled: true };
  return {
    label: "Prepare exam",
    href: `/candidate/exams/assignment-${assignment.id}`,
  };
}

export default function CandidatePage() {
  const [assignments, setAssignments] = useState<CandidateAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    candidateApi
      .assignments()
      .then(setAssignments)
      .catch((cause: unknown) => {
        setError(
          cause instanceof CandidateApiError
            ? "Assignments are unavailable right now."
            : "Unable to load assignments.",
        );
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate
      </p>
      <h1>My exams</h1>
      <p>
        Only assignments belonging to the authenticated candidate are shown.
      </p>
      <section
        aria-labelledby="assignment-heading"
        style={{ marginTop: "2rem" }}
      >
        <h2 id="assignment-heading">Assignments</h2>
        {loading && <p role="status">Loading assignments…</p>}
        {error && <p role="alert">{error}</p>}
        {!loading && !error && assignments.length === 0 && (
          <p>No assignments are currently available.</p>
        )}
        <ul aria-label="Candidate exam assignments">
          {assignments.map((assignment) => {
            const action = actionFor(assignment);
            const status = availabilityLabel(assignment, Date.now());
            return (
              <li key={assignment.id} style={{ marginTop: "1rem" }}>
                <article aria-labelledby={`assignment-${assignment.id}`}>
                  <h3 id={`assignment-${assignment.id}`}>Exam assignment</h3>
                  <p>Status: {status}</p>
                  <p>
                    Attempts: {assignment.attempts.length} of{" "}
                    {assignment.maximum_attempts} used
                  </p>
                  <p>
                    Availability:{" "}
                    {assignment.available_from
                      ? new Date(assignment.available_from).toLocaleString()
                      : "Now"}
                    {assignment.available_until
                      ? ` — ${new Date(assignment.available_until).toLocaleString()}`
                      : ""}
                  </p>
                  {"href" in action ? (
                    <Link href={action.href}>
                      {action.label} for exam assignment
                    </Link>
                  ) : (
                    <button type="button" disabled>
                      {action.label}
                    </button>
                  )}
                </article>
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}
