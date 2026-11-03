"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import {
  speakingReviewApi,
  type SpeakingReview,
  type AudioEvidenceAsset,
} from "../review-api";
import { AudioEvidenceView } from "../audio-evidence-view";

export default function SpeakingReviewWorkspace({
  params,
}: {
  params: Promise<{ assessmentId: string }>;
}) {
  const { organisationId, user, permissions } = useAuth();
  const context =
    user && organisationId && permissions.includes("subjective.read")
      ? `${user.id}:${organisationId}`
      : null;
  const [snapshot, setSnapshot] = useState<{
    context: string;
    review: SpeakingReview;
    audio: AudioEvidenceAsset;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    setSnapshot(null);
    setError(null);
    if (!context || !organisationId) return;
    const abort = new AbortController();
    void params
      .then(async ({ assessmentId }) => {
        const review = await speakingReviewApi.detail(
          organisationId,
          assessmentId,
          abort.signal,
        );
        const audio = await speakingReviewApi.audioDetail(
          organisationId,
          review.candidate_media_asset_id,
          abort.signal,
        );
        if (!abort.signal.aborted) setSnapshot({ context, review, audio });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "The existing assessment could not be loaded.",
          );
      });
    return () => abort.abort();
  }, [context, organisationId, params, refresh]);
  const data = snapshot?.context === context ? snapshot : null;
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/institution/speaking-reviews">
          ← Speaking review queue
        </Link>
      </p>
      <h1>Speaking assessment review</h1>
      {!context ? (
        <p role="alert">
          Subjective read permission in the selected institution is required.
        </p>
      ) : error ? (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => setRefresh((value) => value + 1)}>
            Retry Speaking evidence
          </button>
        </div>
      ) : data && organisationId ? (
        <>
          <AudioEvidenceView
            item={data.audio}
            organisationId={organisationId}
          />
          <p>Existing assessment status: {data.review.status}</p>
          <p>
            Derived transcription:{" "}
            {data.review.speech_transcription_version_id
              ? "a version reference is recorded; readiness is not verified by this playback screen"
              : "not available"}
            .
          </p>
          <h2>Existing recorded criterion proposals</h2>
          <ul>
            {data.review.criteria.map((criterion) => (
              <li key={String(criterion.criterion_code)}>
                {String(criterion.criterion_code)}: proposed points{" "}
                {String(criterion.proposed_points ?? "Not recorded")}
              </li>
            ))}
          </ul>
          <p>
            No criterion scoring or approval controls are available in this
            audio playback step.
          </p>
        </>
      ) : (
        <p role="status">Loading exact Speaking evidence…</p>
      )}
    </section>
  );
}
