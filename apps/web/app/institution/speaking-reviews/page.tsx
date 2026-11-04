"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import React, { FormEvent, useEffect, useRef, useState } from "react";
import { useAuth } from "../../../lib/auth";
import {
  speakingReviewApi,
  type SpeakingReviewSummary,
  type AudioEvidenceAsset,
} from "./review-api";
import css from "../assessment.module.css";
import { AISpeakingExecutionPanel } from "./execution-panel";
import {
  speakingFoundationApi,
  eligibleSpeakingRubrics,
  type HumanSpeakingOptions,
} from "./foundation-api";

export default function SpeakingReviewQueuePage() {
  const { organisationId, permissions, user } = useAuth();
  const allowed = permissions.includes("subjective.read");
  const canInitialize = allowed && permissions.includes("subjective.assess");
  const router = useRouter();
  const context =
    user && organisationId && allowed
      ? JSON.stringify([user.id, organisationId, [...permissions].sort()])
      : null;
  const [snapshot, setSnapshot] = useState<{
    context: string;
    audio: AudioEvidenceAsset[];
    reviews: SpeakingReviewSummary[];
    options: HumanSpeakingOptions | null;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [sourceId, setSourceId] = useState("");
  const [rubricId, setRubricId] = useState("");
  const [busy, setBusy] = useState(false);
  const operations = useRef<Record<string, string>>({});
  useEffect(() => {
    setSnapshot(null);
    setError(null);
    setSourceId("");
    setRubricId("");
    if (!context || !organisationId) return;
    const abort = new AbortController();
    void Promise.all([
      speakingReviewApi.audioList(organisationId, abort.signal),
      speakingReviewApi.list(organisationId, abort.signal),
      canInitialize
        ? speakingFoundationApi.options(organisationId, abort.signal)
        : Promise.resolve(null),
    ])
      .then(([audio, reviews, options]) => {
        if (!abort.signal.aborted)
          setSnapshot({ context, audio, reviews, options });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Recorded evidence could not be loaded.",
          );
      });
    return () => abort.abort();
  }, [context, organisationId, canInitialize, refresh]);
  const data = snapshot?.context === context ? snapshot : null;
  const source = data?.options?.submissions.find(
    (item) => item.candidate_response_revision_id === sourceId,
  );
  const rubrics = eligibleSpeakingRubrics(source, data?.options?.rubrics ?? []);
  const rubric = rubrics.find((item) => item.id === rubricId);
  async function initialize(event: FormEvent) {
    event.preventDefault();
    if (!organisationId || !source || !rubric || !canInitialize || busy) return;
    setBusy(true);
    setError(null);
    const key = `${context}:${source.candidate_response_revision_id}:${source.response_checksum}:${rubric.id}:${rubric.checksum}`;
    const operation = (operations.current[key] ??= crypto.randomUUID());
    try {
      const created = await speakingFoundationApi.initialize(
        organisationId,
        source,
        rubric,
        operation,
      );
      router.push(`/institution/speaking-reviews/${created.id}`);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The assessment could not be initialized. Refresh the exact evidence before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <h1>Speaking review queue</h1>
      <p>
        Original audio is primary evidence. Playback access does not create a
        transcript or an assessment.
      </p>
      {permissions.includes("rubric.read") && (
        <p>
          <Link href="/institution/rubrics">
            Inspect and publish Speaking rubrics →
          </Link>
        </p>
      )}
      {!context ? (
        <p role="alert">
          Subjective read permission in the selected institution is required.
        </p>
      ) : (
        <>
          {error && (
            <div role="alert">
              <p>{error}</p>
              <button onClick={() => setRefresh((value) => value + 1)}>
                Retry recorded evidence
              </button>
            </div>
          )}
          {!data && !error && (
            <p role="status">Loading recorded audio evidence…</p>
          )}
          <AISpeakingExecutionPanel />
          {data && (
            <>
              {canInitialize && data.options && (
                <section className={css.card}>
                  <h2>Initialize human Speaking assessment</h2>
                  <p>
                    HUMAN_ONLY · AUDIO. Select a submitted response and its
                    exact published rubric. The server resolves the bound
                    original recording; no asset identifier can be entered here.
                  </p>
                  {!data.options.submissions.length ? (
                    <p>
                      No qualifying submitted original audio responses are
                      available. A published compatible HUMAN_ONLY / AUDIO
                      rubric and verified attached recording are required.
                    </p>
                  ) : (
                    <form
                      className={css.form}
                      onSubmit={(event) => void initialize(event)}
                    >
                      <label>
                        Submitted Speaking response
                        <select
                          required
                          value={sourceId}
                          disabled={busy}
                          onChange={(event) => {
                            setSourceId(event.target.value);
                            setRubricId("");
                          }}
                        >
                          <option value="">
                            Select an exact submitted response
                          </option>
                          {data.options.submissions.map((item) => (
                            <option
                              key={item.candidate_response_revision_id}
                              value={item.candidate_response_revision_id}
                            >
                              {item.task_label} · response{" "}
                              {item.candidate_response_revision_id}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Published Speaking rubric
                        <select
                          required
                          value={rubricId}
                          disabled={!source || busy}
                          onChange={(event) => setRubricId(event.target.value)}
                        >
                          <option value="">
                            Select its pinned published rubric
                          </option>
                          {rubrics.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.name} · {item.version_label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {source && (
                        <p>
                          Verified original duration: {source.duration_ms} ms ·
                          SHA256: {source.audio_sha256} · exact item version:{" "}
                          {source.question_bank_item_version_id}
                        </p>
                      )}
                      {source && !rubrics.length && (
                        <p role="status">
                          The item-pinned Speaking rubric is not published or is
                          incompatible. Complete governed rubric publication
                          before initializing an assessment.
                        </p>
                      )}
                      <button disabled={busy || !source || !rubric}>
                        {busy
                          ? "Initializing human assessment…"
                          : "Initialize human Speaking assessment"}
                      </button>
                    </form>
                  )}
                </section>
              )}
              <h2>Recorded audio evidence</h2>
              {!data.audio.length && (
                <p>
                  No submitted, bound audio evidence is available to this
                  account.
                </p>
              )}
              {data.audio.map((item) => (
                <article
                  className={css.card}
                  key={item.media_asset_id}
                  aria-label={`Recorded audio ${item.media_asset_id}`}
                >
                  <p>Audio asset: {item.media_asset_id}</p>
                  <p>Attempt: {item.attempt_id}</p>
                  <p>Item binding: {item.binding_id}</p>
                  <p>
                    Verified duration: {item.duration_ms} ms ·{" "}
                    {item.attachment_status}
                  </p>
                  <Link
                    href={`/institution/speaking-reviews/audio/${item.media_asset_id}`}
                  >
                    Inspect recorded audio {item.media_asset_id}
                  </Link>
                </article>
              ))}
              <h2>Existing Speaking assessments</h2>
              {!data.reviews.length && (
                <p>
                  No Speaking assessments have been created. Audio evidence is
                  available separately above.
                </p>
              )}
              <ul>
                {data.reviews.map((review) => (
                  <li key={review.assessment_id}>
                    <Link
                      href={`/institution/speaking-reviews/${review.assessment_id}`}
                    >
                      {review.assessment_id}
                    </Link>{" "}
                    · {review.status}
                    {review.assessment_origin
                      ? ` · ${review.assessment_origin}`
                      : ""}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p>
            <button onClick={() => setRefresh((value) => value + 1)}>
              Refresh recorded evidence
            </button>
          </p>
        </>
      )}
    </section>
  );
}
