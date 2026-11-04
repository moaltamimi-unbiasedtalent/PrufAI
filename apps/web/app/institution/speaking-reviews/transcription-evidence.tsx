"use client";

import React, { FormEvent, useEffect, useRef, useState } from "react";
import { useAuth } from "../../../lib/auth";
import type { AudioEvidenceAsset } from "./review-api";
import {
  exactTranscriptionSource,
  exactTranscriptVersion,
  transcriptionApi,
  type AssetTranscriptions,
  type TranscriptionJob,
  type TranscriptionOptions,
  type TranscriptVersion,
} from "./transcription-api";
import css from "../assessment.module.css";

export function TranscriptionJobView({ job }: { job: TranscriptionJob }) {
  return (
    <section aria-label="Persisted transcription job">
      <p role="status">Transcription status: {job.status}</p>
      {job.test_only && (
        <p className={css.notice}>
          TEST ONLY · Deterministic workflow evidence. No speech recognition or
          language proficiency is demonstrated.
        </p>
      )}
      <dl className={css.provenance}>
        <dt>Execution job</dt>
        <dd>{job.id}</dd>
        <dt>Pinned profile version</dt>
        <dd>{job.profile_version}</dd>
        <dt>Provider / model / adapter</dt>
        <dd>
          {job.provider} / {job.model_name} / {job.adapter_version}
        </dd>
        <dt>Declared language</dt>
        <dd>{job.language}</dd>
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
        {job.failure_category && (
          <>
            <dt>Failure classification</dt>
            <dd>{job.failure_category}</dd>
          </>
        )}
      </dl>
      <details>
        <summary>Persisted execution history</summary>
        <ol>
          {job.state_history.map((state, index) => (
            <li key={`${state.at}:${index}`}>
              {state.status} · {state.at} · attempt {state.attempt_count}
              {state.failure_category ? ` · ${state.failure_category}` : ""}
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}

export function DerivedTranscriptView({
  item,
  version,
  job,
}: {
  item: AudioEvidenceAsset;
  version: TranscriptVersion;
  job: TranscriptionJob | undefined;
}) {
  if (!exactTranscriptVersion(item, version, job))
    return (
      <p role="alert">
        This transcript does not have matching execution provenance for the
        exact submitted recording and response revision. Its text is not
        displayed.
      </p>
    );
  return (
    <section aria-label="Derived machine transcript">
      <h3>Machine-derived transcript — secondary evidence</h3>
      <p>
        The original recording remains primary evidence. This transcript does
        not score criteria, approve an assessment or alter the human review.
      </p>
      {version.test_only && (
        <p className={css.notice}>
          TEST ONLY TRANSCRIPT · Synthetic deterministic output. This is not
          recognition of the recorded speech.
        </p>
      )}
      <dl className={css.provenance}>
        <dt>Immutable transcript version</dt>
        <dd>
          {version.version_number} · {version.id}
        </dd>
        <dt>Transcript checksum</dt>
        <dd>{version.checksum}</dd>
        <dt>Submitted response revision</dt>
        <dd>{version.candidate_response_revision_id}</dd>
        <dt>Original audio checksum</dt>
        <dd>{version.audio_sha256}</dd>
        <dt>Execution job</dt>
        <dd>{version.execution_job_id}</dd>
        <dt>Pinned profile version</dt>
        <dd>{version.profile_version}</dd>
        <dt>Provider / model / adapter</dt>
        <dd>
          {version.provider} / {version.model_name} / {version.adapter_version}
        </dd>
        <dt>Declared language</dt>
        <dd>{version.language}</dd>
      </dl>
      <p style={{ whiteSpace: "pre-wrap" }}>{version.transcript}</p>
      {version.segments.length > 0 && (
        <details>
          <summary>Provider transcript segments</summary>
          <ol>
            {version.segments.map((segment) => (
              <li key={segment.segment_id}>
                {segment.start_ms}–{segment.end_ms} ms · {segment.text}
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

export function TranscriptionEvidencePanel({
  item,
  organisationId,
}: {
  item: AudioEvidenceAsset;
  organisationId: string;
}) {
  const { user, permissions, organisationId: activeOrganisation } = useAuth();
  const canRead = permissions.includes("speech_transcription.read");
  const canRequest = permissions.includes("speech_transcription.request");
  const context =
    user && activeOrganisation === organisationId && (canRead || canRequest)
      ? JSON.stringify([
          user.id,
          organisationId,
          [...permissions].sort(),
          item.media_asset_id,
          item.sha256,
          item.candidate_response_revision_id ?? item.response_revision_id,
        ])
      : null;
  const [snapshot, setSnapshot] = useState<{
    context: string;
    history: AssetTranscriptions;
    options: TranscriptionOptions | null;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [profileId, setProfileId] = useState("");
  const [language, setLanguage] = useState("");
  const [selectedVersion, setSelectedVersion] = useState("");
  const [transcript, setTranscript] = useState<{
    context: string;
    version: TranscriptVersion;
  } | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const operations = useRef<Record<string, string>>({});

  useEffect(() => {
    setSnapshot(null);
    setTranscript(null);
    setProfileId("");
    setLanguage("");
    setSelectedVersion("");
    setError(null);
    setTranscriptError(null);
  }, [context]);

  useEffect(() => {
    if (!context || !canRead) return;
    const abort = new AbortController();
    setError(null);
    void Promise.all([
      transcriptionApi.history(
        organisationId,
        item.media_asset_id,
        abort.signal,
      ),
      canRequest
        ? transcriptionApi.options(
            organisationId,
            item.media_asset_id,
            abort.signal,
          )
        : Promise.resolve(null),
    ])
      .then(([history, options]) => {
        if (abort.signal.aborted) return;
        if (
          history.asset_id !== item.media_asset_id ||
          (options && !exactTranscriptionSource(item, options.source))
        )
          throw new Error(
            "Transcription metadata does not match the exact submitted recording. Refresh the original evidence.",
          );
        setSnapshot({ context, history, options });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Transcription status could not be verified.",
          );
      });
    return () => abort.abort();
  }, [context, canRead, canRequest, organisationId, item, refresh]);

  const data = snapshot?.context === context ? snapshot : null;
  const jobs = data?.history.jobs ?? [];
  const latest = jobs[0];
  const profile = data?.options?.profiles.find(
    (entry) => entry.id === profileId,
  );
  const readyVersions = [...(data?.history.versions ?? [])]
    .filter((version) => ["READY", "SUPERSEDED"].includes(version.status))
    .sort((a, b) => b.version_number - a.version_number);
  const versionId = selectedVersion || readyVersions[0]?.id || "";
  const rootId = data?.history.transcription_id;

  useEffect(() => {
    if (
      !context ||
      !canRead ||
      !latest ||
      !["QUEUED", "RUNNING", "RETRYABLE"].includes(latest.status)
    )
      return;
    const timer = setTimeout(() => setRefresh((value) => value + 1), 2000);
    return () => clearTimeout(timer);
  }, [context, canRead, latest, refresh]);

  useEffect(() => {
    setTranscript(null);
    setTranscriptError(null);
    if (!context || !canRead || !rootId || !versionId) return;
    const abort = new AbortController();
    void transcriptionApi
      .version(organisationId, rootId, versionId, abort.signal)
      .then((version) => {
        if (!abort.signal.aborted) setTranscript({ context, version });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setTranscriptError(
            cause instanceof Error
              ? cause.message
              : "The exact transcript version could not be loaded.",
          );
      });
    return () => abort.abort();
  }, [context, canRead, rootId, versionId, organisationId, refresh]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (
      !context ||
      !canRequest ||
      !data?.options ||
      !profile ||
      profile.availability === "UNCONFIGURED" ||
      !profile.languages.includes(language) ||
      busy
    )
      return;
    setBusy(true);
    setError(null);
    const key = `${context}:${profile.id}:${profile.version}:${language}`;
    const operation = (operations.current[key] ??= crypto.randomUUID());
    try {
      await transcriptionApi.request(
        organisationId,
        data.options.source,
        profile.id,
        language,
        operation,
      );
      setRefresh((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request unavailable.");
    } finally {
      setBusy(false);
    }
  }

  async function retry(job: TranscriptionJob) {
    if (!context || !canRequest || !job.can_retry || busy) return;
    setBusy(true);
    setError(null);
    const key = `${context}:retry:${job.id}:${job.attempt_count}`;
    const operation = (operations.current[key] ??= crypto.randomUUID());
    try {
      await transcriptionApi.retry(organisationId, job.id, operation);
      setRefresh((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Retry unavailable.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={css.card} aria-label="Derived transcription evidence">
      <h2>Transcription</h2>
      <p>
        Original audio is available above as primary evidence. Transcription is
        optional derived evidence and does not change HUMAN_ONLY audio review.
      </p>
      {!context || !canRead ? (
        <p>Institution transcription read permission is required.</p>
      ) : (
        <>
          {error && <p role="alert">{error}</p>}
          {!data && !error && (
            <p role="status">Loading transcription status…</p>
          )}
          {data && (
            <>
              {latest ? (
                <TranscriptionJobView job={latest} />
              ) : (
                <p role="status">Transcription status: NOT REQUESTED</p>
              )}
              {canRequest && data.options && (
                <form
                  className={css.form}
                  onSubmit={(event) => void submit(event)}
                >
                  <h3>Request governed transcription</h3>
                  <label>
                    Approved transcription profile
                    <select
                      value={profileId}
                      disabled={busy}
                      onChange={(event) => {
                        setProfileId(event.target.value);
                        setLanguage("");
                      }}
                    >
                      <option value="">Choose an approved profile</option>
                      {data.options.profiles.map((entry) => (
                        <option key={entry.id} value={entry.id}>
                          {entry.name} · {entry.availability}
                        </option>
                      ))}
                    </select>
                  </label>
                  {profile && (
                    <>
                      <p>
                        Provider configuration: {profile.availability} ·{" "}
                        {profile.provider} / {profile.model_name} / adapter{" "}
                        {profile.adapter_version}
                      </p>
                      {profile.availability === "UNCONFIGURED" && (
                        <p role="status">
                          No approved configured execution path is available for
                          this profile. No transcription will be simulated.
                        </p>
                      )}
                      {profile.test_only && (
                        <p className={css.notice}>
                          TEST ONLY · This profile produces synthetic workflow
                          evidence and does not recognize recorded speech.
                        </p>
                      )}
                      <label>
                        Declared recording language
                        <select
                          value={language}
                          disabled={busy}
                          onChange={(event) => setLanguage(event.target.value)}
                        >
                          <option value="">Choose the declared language</option>
                          {profile.languages.map((entry) => (
                            <option key={entry} value={entry}>
                              {entry}
                            </option>
                          ))}
                        </select>
                      </label>
                    </>
                  )}
                  <button
                    type="submit"
                    disabled={
                      busy ||
                      !profile ||
                      profile.availability === "UNCONFIGURED" ||
                      !profile.languages.includes(language) ||
                      latest?.status === "QUEUED" ||
                      latest?.status === "RUNNING" ||
                      latest?.status === "RETRYABLE"
                    }
                  >
                    {busy
                      ? "Requesting transcription…"
                      : "Request transcription"}
                  </button>
                </form>
              )}
              {!canRequest && (
                <p>
                  Your institution role can read transcripts and cannot request
                  execution.
                </p>
              )}
              {canRequest && latest?.can_retry && (
                <button disabled={busy} onClick={() => void retry(latest)}>
                  Retry transcription
                </button>
              )}
              {readyVersions.length > 0 && (
                <label className={css.form}>
                  Immutable transcript version to inspect
                  <select
                    value={versionId}
                    onChange={(event) => setSelectedVersion(event.target.value)}
                  >
                    {readyVersions.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        Version {entry.version_number} · {entry.status}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {transcriptError && <p role="alert">{transcriptError}</p>}
              {transcript?.context === context &&
                transcript.version.id === versionId && (
                  <DerivedTranscriptView
                    item={item}
                    version={transcript.version}
                    job={jobs.find(
                      (entry) =>
                        entry.id === transcript.version.execution_job_id,
                    )}
                  />
                )}
              {jobs.length > 1 && (
                <details>
                  <summary>Earlier execution jobs</summary>
                  {jobs.slice(1).map((job) => (
                    <TranscriptionJobView key={job.id} job={job} />
                  ))}
                </details>
              )}
            </>
          )}
          <button
            disabled={busy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            Refresh transcription status
          </button>
        </>
      )}
    </section>
  );
}
