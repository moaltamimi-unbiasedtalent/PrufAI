"use client";

import Link from "next/link";
import React, { FormEvent, useEffect, useRef, useState } from "react";
import { useAuth } from "../../../lib/auth";
import {
  completedWritingProposal,
  exactAIWritingSource,
  writingExecutionApi,
  type AIWritingJob,
  type AIWritingOptions,
} from "./execution-api";
import css from "../assessment.module.css";

export function AIWritingJobView({ job }: { job: AIWritingJob }) {
  return (
    <article className={css.card} aria-label={`Writing execution ${job.id}`}>
      <p role="status">AI-assisted Writing execution: {job.status}</p>
      <p>Assessment mode: {job.assessment_mode}</p>
      {job.test_only && (
        <p className={css.notice}>
          TEST ONLY · Deterministic technical proposal. This demonstrates
          execution and governance, not authentic AI assessment or German
          Writing scoring accuracy.
        </p>
      )}
      <dl className={css.provenance}>
        <dt>Execution job</dt>
        <dd>{job.id}</dd>
        <dt>Submitted response revision</dt>
        <dd>{job.candidate_response_revision_id}</dd>
        <dt>Response checksum</dt>
        <dd>{job.response_checksum}</dd>
        <dt>Published rubric version</dt>
        <dd>{job.rubric_version_id}</dd>
        <dt>Rubric checksum</dt>
        <dd>{job.rubric_checksum}</dd>
        <dt>Pinned execution profile</dt>
        <dd>{job.execution_profile_version}</dd>
        <dt>Provider / model / adapter</dt>
        <dd>
          {job.provider} / {job.model_name} / {job.adapter_version}
        </dd>
        <dt>Model configuration version</dt>
        <dd>{job.model_version}</dd>
        <dt>Prompt version</dt>
        <dd>{job.prompt_version_id}</dd>
        <dt>Agent version</dt>
        <dd>{job.agent_version_id}</dd>
        <dt>Workflow version</dt>
        <dd>{job.workflow_version_id}</dd>
        <dt>AI run</dt>
        <dd>{job.ai_run_id ?? "Not started"}</dd>
        <dt>AI run state</dt>
        <dd>{job.ai_run_status ?? "Not started"}</dd>
        <dt>Execution attempts</dt>
        <dd>
          {job.attempt_count} / {job.max_attempts}
        </dd>
        <dt>Requested</dt>
        <dd>{job.created_at}</dd>
        <dt>Started</dt>
        <dd>{job.started_at ?? "Not started"}</dd>
        <dt>Completed</dt>
        <dd>{job.completed_at ?? "Not completed"}</dd>
        <dt>Human review</dt>
        <dd>{job.review_status ?? "Not created"}</dd>
        {job.failure_category && (
          <>
            <dt>Safe failure classification</dt>
            <dd>{job.failure_category}</dd>
          </>
        )}
      </dl>
      {completedWritingProposal(job) ? (
        <>
          <p className={css.notice}>
            The validated proposal has been handed to the existing Writing
            service. A human decision remains mandatory. No final mark or result
            release is inferred from this execution.
          </p>
          <p>
            <Link
              href={`/institution/writing-reviews/${encodeURIComponent(job.assessment_id!)}`}
            >
              Inspect AI Writing proposal {job.assessment_id}
            </Link>
          </p>
        </>
      ) : job.status === "COMPLETED" ? (
        <p role="alert">
          A complete matching run, assessment and review reference is
          unavailable. No completed proposal is inferred.
        </p>
      ) : null}
      <details>
        <summary>Persisted Writing execution history</summary>
        <ol>
          {job.state_history.map((state, index) => (
            <li key={`${state.at}:${index}`}>
              {state.status} · {state.at} · attempt {state.attempt_count}
              {state.failure_category ? ` · ${state.failure_category}` : ""}
            </li>
          ))}
        </ol>
      </details>
    </article>
  );
}

export function AIWritingExecutionPanel() {
  const { user, organisationId, permissions } = useAuth();
  const canRead = Boolean(
    user && organisationId && permissions.includes("subjective.read"),
  );
  const canRequest = canRead && permissions.includes("subjective.assess");
  const contextKey = `${user?.id ?? ""}:${organisationId ?? ""}:${[...permissions].sort().join(",")}`;
  const currentContext = useRef(contextKey);
  currentContext.current = contextKey;
  const [dataContext, setDataContext] = useState<string | null>(null);
  const [options, setOptions] = useState<AIWritingOptions | null>(null);
  const [jobs, setJobs] = useState<AIWritingJob[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const operations = useRef<Record<string, string>>({});

  useEffect(() => {
    setSourceId("");
    setProfileId("");
    setBusy(false);
    operations.current = {};
  }, [contextKey]);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    if (!canRead || !organisationId) {
      setOptions(null);
      setJobs([]);
      setDataContext(null);
      return () => controller.abort();
    }
    void Promise.all([
      canRequest
        ? writingExecutionApi.options(organisationId, controller.signal)
        : Promise.resolve(null),
      writingExecutionApi.list(organisationId, controller.signal),
    ])
      .then(([available, stored]) => {
        if (
          !controller.signal.aborted &&
          currentContext.current === contextKey
        ) {
          setOptions(available);
          setJobs(
            stored.jobs.filter((job) => job.organisation_id === organisationId),
          );
          setDataContext(contextKey);
        }
      })
      .catch((cause: unknown) => {
        if (
          !controller.signal.aborted &&
          currentContext.current === contextKey
        ) {
          setError(
            cause instanceof Error
              ? cause.message
              : "AI Writing execution data could not be loaded.",
          );
        }
      });
    return () => controller.abort();
  }, [contextKey, organisationId, canRead, canRequest, reload]);

  const visibleJobs = dataContext === contextKey ? jobs : [];
  const available = dataContext === contextKey ? options : null;
  const sources =
    available?.sources.filter(
      (source) => source.assessment_mode === "AI_ASSISTED",
    ) ?? [];
  const source = sources.find(
    (entry) => entry.candidate_response_revision_id === sourceId,
  );
  const profiles = available?.profiles ?? [];
  const profile = profiles.find(
    (entry) =>
      `${entry.execution_profile_id}:${entry.execution_profile_version}` ===
      profileId,
  );
  const runnable = profiles.filter((entry) =>
    ["CONFIGURED", "TEST_ONLY"].includes(entry.availability),
  );
  useEffect(() => {
    if (
      dataContext !== contextKey ||
      !jobs.some((job) =>
        ["QUEUED", "RUNNING", "RETRYABLE"].includes(job.status),
      )
    )
      return;
    const timer = window.setTimeout(
      () => setReload((value) => value + 1),
      1500,
    );
    return () => window.clearTimeout(timer);
  }, [jobs, dataContext, contextKey]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (
      !organisationId ||
      !source ||
      !profile ||
      !canRequest ||
      busy ||
      !["CONFIGURED", "TEST_ONLY"].includes(profile.availability)
    )
      return;
    const operationKey = `${source.candidate_response_revision_id}:${source.response_checksum}:${source.rubric_checksum}:${profile.execution_profile_id}:${profile.execution_profile_version}`;
    const operationId = (operations.current[operationKey] ??=
      crypto.randomUUID());
    const requestedContext = contextKey;
    setBusy(true);
    setError(null);
    try {
      const job = await writingExecutionApi.request(
        organisationId,
        source,
        profile,
        operationId,
      );
      if (currentContext.current !== requestedContext) return;
      if (
        job.organisation_id !== organisationId ||
        !exactAIWritingSource(source, job) ||
        job.execution_profile_version !== profile.execution_profile_version
      )
        throw new Error(
          "Returned execution does not match the exact selected Writing evidence and configuration. Refresh before continuing.",
        );
      setJobs((stored) => [
        job,
        ...stored.filter((entry) => entry.id !== job.id),
      ]);
      setDataContext(contextKey);
    } catch (cause) {
      if (currentContext.current === requestedContext)
        setError(
          cause instanceof Error
            ? cause.message
            : "The execution request could not be confirmed. Refresh before retrying.",
        );
    } finally {
      if (currentContext.current === requestedContext) setBusy(false);
    }
  }
  async function mutate(job: AIWritingJob, action: "retry" | "cancel") {
    if (
      !organisationId ||
      !canRequest ||
      busy ||
      job.organisation_id !== organisationId ||
      !(action === "retry" ? job.can_retry : job.can_cancel)
    )
      return;
    const requestedContext = contextKey;
    setBusy(true);
    setError(null);
    try {
      await writingExecutionApi[action](organisationId, job.id);
      if (currentContext.current === requestedContext)
        setReload((value) => value + 1);
    } catch (cause) {
      if (currentContext.current === requestedContext)
        setError(
          cause instanceof Error
            ? cause.message
            : "The execution action could not be confirmed.",
        );
    } finally {
      if (currentContext.current === requestedContext) setBusy(false);
    }
  }
  if (!canRead) return null;
  return (
    <section
      className={css.card}
      aria-label="AI-assisted Writing proposal execution"
    >
      <h2>AI-assisted Writing proposal execution</h2>
      <p>
        This separate workflow requires an explicit published AI_ASSISTED policy
        and an exact approved execution configuration. AI proposes; an
        authorized human decides. HUMAN_ONLY assessments remain independent.
      </p>
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => setReload((value) => value + 1)}
      >
        Refresh Writing execution status
      </button>
      {available && canRequest && (
        <>
          {!sources.length ? (
            <p>
              No eligible submitted AI_ASSISTED Writing response is available.
              HUMAN_ONLY responses cannot be sent through this executor.
            </p>
          ) : (
            <form className={css.form} onSubmit={(event) => void submit(event)}>
              <label>
                AI-assisted submitted Writing response
                <select
                  required
                  value={sourceId}
                  disabled={busy}
                  onChange={(event) => setSourceId(event.target.value)}
                >
                  <option value="">
                    Select exact AI_ASSISTED Writing evidence
                  </option>
                  {sources.map((entry) => (
                    <option
                      key={entry.candidate_response_revision_id}
                      value={entry.candidate_response_revision_id}
                    >
                      {entry.task_label} · response{" "}
                      {entry.candidate_response_revision_id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Approved Writing execution configuration
                <select
                  required
                  value={profileId}
                  disabled={busy || !source}
                  onChange={(event) => setProfileId(event.target.value)}
                >
                  <option value="">
                    Select exact approved execution configuration
                  </option>
                  {profiles.map((entry) => (
                    <option
                      key={`${entry.execution_profile_id}:${entry.execution_profile_version}`}
                      value={`${entry.execution_profile_id}:${entry.execution_profile_version}`}
                      disabled={entry.availability === "UNCONFIGURED"}
                    >
                      {entry.test_only ? "TEST ONLY · " : ""}
                      {entry.name} · {entry.availability}
                    </option>
                  ))}
                </select>
              </label>
              {source && (
                <p>
                  Mode: AI_ASSISTED · published rubric{" "}
                  {source.rubric_version_id} · checksum {source.rubric_checksum}
                </p>
              )}
              {profile?.test_only && (
                <p className={css.notice}>
                  TEST ONLY executor · no authentic model inference or
                  linguistic scoring accuracy is established.
                </p>
              )}
              {profile && (
                <dl className={css.provenance}>
                  <dt>Selected execution profile version</dt>
                  <dd>{profile.execution_profile_version}</dd>
                  <dt>Selected model configuration</dt>
                  <dd>
                    {profile.model_name} · {profile.model_version}
                  </dd>
                  <dt>Selected prompt version</dt>
                  <dd>{profile.prompt_version_id}</dd>
                  <dt>Selected agent / workflow</dt>
                  <dd>
                    {profile.agent_version_id} / {profile.workflow_version_id}
                  </dd>
                </dl>
              )}
              <button
                disabled={
                  busy ||
                  !source ||
                  !profile ||
                  profile.availability === "UNCONFIGURED"
                }
              >
                Request AI-assisted Writing proposal
              </button>
            </form>
          )}
          {!runnable.length && (
            <p role="status">
              Writing executor: UNCONFIGURED. No approved available model
              configuration is connected. No proposal is fabricated and no
              HUMAN_ONLY fallback occurs.
            </p>
          )}
        </>
      )}
      {visibleJobs.map((job) => (
        <React.Fragment key={job.id}>
          <AIWritingJobView job={job} />
          {canRequest && (job.can_retry || job.can_cancel) && (
            <div className={css.actions}>
              {job.can_retry && (
                <button
                  disabled={busy}
                  onClick={() => void mutate(job, "retry")}
                >
                  Retry Writing execution {job.id}
                </button>
              )}
              {job.can_cancel && (
                <button
                  disabled={busy}
                  onClick={() => void mutate(job, "cancel")}
                >
                  Cancel Writing execution {job.id}
                </button>
              )}
            </div>
          )}
        </React.Fragment>
      ))}
    </section>
  );
}
