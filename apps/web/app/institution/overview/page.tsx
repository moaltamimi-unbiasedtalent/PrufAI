"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { authenticatedFetch } from "../../../lib/api";
import { useAuth } from "../../../lib/auth";
import { assessmentApi } from "../assessment-api";

type ResultSummary = {
  id: string;
  version_id: string;
  version_number: number;
  status: string;
  outcome: string | null;
  raw_awarded_points: string | null;
  raw_maximum_points: string;
  checksum: string;
};

type AssignmentInspection = {
  id: string;
  candidate_user_id: string;
  candidate_name: string | null;
  exam_name: string | null;
  exam_version_label: string | null;
  status: string;
  maximum_attempts: number;
  attempts: Array<{
    id: string;
    status: string;
    attempt_number: number;
    result: ResultSummary | null;
  }>;
};

export default function InstitutionOverviewPage() {
  const { organisationId, permissions } = useAuth();
  const [assignments, setAssignments] = useState<AssignmentInspection[]>([]);
  const [message, setMessage] = useState("Loading institution assignments…");
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [scoring, setScoring] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const allowed = permissions.includes("exam_assignment.read");

  useEffect(() => {
    let cancelled = false;
    setAssignments([]);
    setError(null);
    if (!organisationId || !allowed) {
      setMessage(
        "Select an active institution with assignment inspection permission.",
      );
      return;
    }
    setMessage("Loading institution assignments…");
    authenticatedFetch(
      `/api/v1/organisations/${organisationId}/exam-assignments`,
    )
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 403) {
            throw new Error(
              "Your account is not permitted to inspect these assignments.",
            );
          }
          throw new Error(
            "Assignments could not be loaded. Check the API connection and retry.",
          );
        }
        return (await response.json()) as AssignmentInspection[];
      })
      .then((rows) => {
        if (!cancelled) {
          setAssignments(rows);
          setMessage(
            rows.length
              ? ""
              : "No assignments are available in this institution.",
          );
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setMessage("");
          setError(
            cause instanceof Error
              ? cause.message
              : "Assignments could not be loaded.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, organisationId, reload]);

  async function score(attemptId: string) {
    if (!organisationId || !permissions.includes("scoring.run") || scoring)
      return;
    setScoring(attemptId);
    setError(null);
    setActionMessage(null);
    try {
      const run = await assessmentApi.score(organisationId, attemptId);
      setActionMessage(
        `Scoring run ${run.id} completed with state ${run.status}. Inspect the persisted result below before requesting human review.`,
      );
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Scoring could not be confirmed. Reload before retrying.",
      );
    } finally {
      setScoring(null);
    }
  }

  return (
    <section>
      <h1>Institution inspection</h1>
      {permissions.includes("exam_assignment.manage") &&
        permissions.includes("exam.read") &&
        permissions.includes("membership.read") && (
          <p>
            <Link href="/institution/assignments">
              Assign a practice assessment →
            </Link>
          </p>
        )}
      <p>
        Inspect assigned exams, candidate attempt status, and persisted result
        versions.
      </p>
      <p>
        Result status and outcome below come directly from the governed API.
      </p>
      {message && <p role="status">{message}</p>}
      {actionMessage && <p role="status">{actionMessage}</p>}
      <p>
        <button type="button" onClick={() => setReload((value) => value + 1)}>
          Refresh assignments
        </button>
      </p>
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button type="button" onClick={() => setReload((value) => value + 1)}>
            Retry assignments
          </button>
        </div>
      )}
      <ul
        aria-label="Institution exam assignments"
        style={{ padding: 0, listStyle: "none" }}
      >
        {assignments.map((assignment) => (
          <li key={assignment.id} style={{ marginTop: "1rem" }}>
            <article
              style={{
                background: "white",
                border: "1px solid #dce2ee",
                borderRadius: "0.5rem",
                padding: "1.25rem",
              }}
            >
              <h2>{assignment.exam_name ?? "Exam assignment"}</h2>
              <p>
                <strong>Candidate:</strong>{" "}
                {assignment.candidate_name ?? assignment.candidate_user_id}
              </p>
              <p>
                <strong>Exam version:</strong>{" "}
                {assignment.exam_version_label ?? "Unavailable"}
              </p>
              <p>
                <strong>Assignment:</strong> {assignment.status} ·{" "}
                {assignment.attempts.length} of {assignment.maximum_attempts}{" "}
                attempts used
              </p>
              {!assignment.attempts.length && (
                <p>The candidate has not prepared an attempt yet.</p>
              )}
              {assignment.attempts.map((attempt) => (
                <section
                  key={attempt.id}
                  aria-label={`Attempt ${attempt.attempt_number}`}
                  style={{
                    borderTop: "1px solid #dce2ee",
                    paddingTop: "0.8rem",
                  }}
                >
                  <h3>
                    Attempt {attempt.attempt_number}: {attempt.status}
                  </h3>
                  {attempt.result ? (
                    <div>
                      <p>
                        <strong>
                          Result version {attempt.result.version_number}:
                        </strong>{" "}
                        {attempt.result.status}
                      </p>
                      <p>
                        <strong>Raw score:</strong>{" "}
                        {attempt.result.raw_awarded_points ?? "Pending"} /{" "}
                        {attempt.result.raw_maximum_points}
                      </p>
                      <p>
                        <strong>Outcome:</strong>{" "}
                        {attempt.result.outcome ?? "Not determined"}
                      </p>
                      <details>
                        <summary>Version provenance</summary>
                        <dl>
                          <dt>Result ID</dt>
                          <dd>{attempt.result.id}</dd>
                          <dt>Version ID</dt>
                          <dd>{attempt.result.version_id}</dd>
                          <dt>Checksum</dt>
                          <dd style={{ overflowWrap: "anywhere" }}>
                            {attempt.result.checksum}
                          </dd>
                        </dl>
                      </details>
                      {permissions.includes("result.read") && (
                        <p>
                          <Link
                            href={`/institution/results/${attempt.result.id}`}
                          >
                            Inspect score, review and release →
                          </Link>
                        </p>
                      )}
                    </div>
                  ) : (
                    <p>
                      No result is available to this account for this attempt.
                    </p>
                  )}
                  {!attempt.result &&
                    attempt.status === "SUBMITTED" &&
                    permissions.includes("scoring.run") && (
                      <p>
                        <button
                          disabled={scoring !== null}
                          onClick={() => void score(attempt.id)}
                        >
                          {scoring === attempt.id
                            ? "Scoring submitted attempt…"
                            : "Score submitted attempt"}
                        </button>
                      </p>
                    )}
                </section>
              ))}
            </article>
          </li>
        ))}
      </ul>
    </section>
  );
}
