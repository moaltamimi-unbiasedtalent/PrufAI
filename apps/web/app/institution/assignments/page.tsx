"use client";

import Link from "next/link";
import React, { FormEvent, useEffect, useState } from "react";
import { useAuth } from "../../../lib/auth";
import { assessmentApi, type AssignmentOptions } from "../assessment-api";
import css from "../assessment.module.css";

export default function AssignAssessmentPage() {
  const { organisationId, permissions } = useAuth();
  const [options, setOptions] = useState<AssignmentOptions | null>(null);
  const [candidateId, setCandidateId] = useState("");
  const [examId, setExamId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const allowed = [
    "exam_assignment.manage",
    "exam.read",
    "membership.read",
  ].every((key) => permissions.includes(key));
  useEffect(() => {
    let cancelled = false;
    setOptions(null);
    setError(null);
    setCreated(null);
    setCandidateId("");
    setExamId("");
    if (!organisationId || !allowed) return;
    void assessmentApi
      .options(organisationId)
      .then((value) => {
        if (!cancelled) setOptions(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Assignment options are unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, organisationId, reload]);
  const exams =
    options?.exam_versions.filter(
      (exam) => exam.classification === "PRACTICE",
    ) ?? [];
  const selected = exams.find((exam) => exam.id === examId);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (
      !organisationId ||
      !allowed ||
      !candidateId ||
      !selected ||
      busy ||
      created
    )
      return;
    setBusy(true);
    setError(null);
    try {
      const result = await assessmentApi.assign(
        organisationId,
        candidateId,
        selected.id,
      );
      setCreated(result.id);
    } catch (cause) {
      setError(
        `${cause instanceof Error ? cause.message : "The assignment could not be confirmed."} Inspect existing assignments before retrying to avoid a duplicate.`,
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <p>
        <Link href="/institution/overview">← Institution inspection</Link>
      </p>
      <h1>Assign a practice assessment</h1>
      <p>
        Select an active candidate and an existing deliverable practice exam.
        The assignment pins the exact published exam version; it does not create
        questions, answers or a result.
      </p>
      {!allowed || !organisationId ? (
        <p className={css.error} role="alert">
          Assignment management, exam read and membership read permissions in an
          active institution are required.
        </p>
      ) : (
        <>
          {error && (
            <div className={css.error} role="alert">
              <p>{error}</p>
              <button onClick={() => setReload((value) => value + 1)}>
                Reload assignment options
              </button>
            </div>
          )}
          {!options && !error && (
            <p role="status">
              Loading active candidates and deliverable exams…
            </p>
          )}
          {options && (!options.candidates.length || !exams.length) && (
            <p className={css.notice}>
              No active candidate or deliverable PRACTICE exam is available in
              this institution. No synthetic success is substituted.
            </p>
          )}
          {options && options.candidates.length > 0 && exams.length > 0 && (
            <form
              className={`${css.form} ${css.card}`}
              onSubmit={(event) => void submit(event)}
            >
              <label>
                Candidate
                <select
                  required
                  value={candidateId}
                  disabled={busy || Boolean(created)}
                  onChange={(event) => setCandidateId(event.target.value)}
                >
                  <option value="">Select an active candidate</option>
                  {options.candidates.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.display_name ?? candidate.id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Practice exam version
                <select
                  required
                  value={examId}
                  disabled={busy || Boolean(created)}
                  onChange={(event) => setExamId(event.target.value)}
                >
                  <option value="">Select a deliverable practice exam</option>
                  {exams.map((exam) => (
                    <option key={exam.id} value={exam.id}>
                      {exam.exam_name} · {exam.version_label} · {exam.status}
                    </option>
                  ))}
                </select>
              </label>
              {selected && (
                <div className={css.notice}>
                  <p>Classification: {selected.classification}</p>
                  <p>Exact exam version: {selected.id}</p>
                  <p>
                    Standard Pack version: {selected.standard_pack_version_id}
                  </p>
                  <p>This assignment permits one attempt.</p>
                </div>
              )}
              <button
                disabled={busy || Boolean(created) || !candidateId || !selected}
              >
                {created
                  ? "Assessment assigned"
                  : busy
                    ? "Assigning assessment…"
                    : "Assign assessment"}
              </button>
            </form>
          )}
          {created && (
            <div className={css.notice} role="status">
              <p>
                Assessment assigned. The candidate can now open it in My exams.
              </p>
              <p>Assignment reference: {created}</p>
              <Link href="/institution/overview">
                Inspect assignments and submitted attempts →
              </Link>
            </div>
          )}
        </>
      )}
    </section>
  );
}
