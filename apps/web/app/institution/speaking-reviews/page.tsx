"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useAuth } from "../../../lib/auth";
import {
  speakingReviewApi,
  type SpeakingReviewSummary,
  type AudioEvidenceAsset,
} from "./review-api";
import css from "../assessment.module.css";

export default function SpeakingReviewQueuePage() {
  const { organisationId, permissions, user } = useAuth();
  const allowed = permissions.includes("subjective.read");
  const context =
    user && organisationId && allowed
      ? JSON.stringify([user.id, organisationId, [...permissions].sort()])
      : null;
  const [snapshot, setSnapshot] = useState<{
    context: string;
    audio: AudioEvidenceAsset[];
    reviews: SpeakingReviewSummary[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    setSnapshot(null);
    setError(null);
    if (!context || !organisationId) return;
    const abort = new AbortController();
    void Promise.all([
      speakingReviewApi.audioList(organisationId, abort.signal),
      speakingReviewApi.list(organisationId, abort.signal),
    ])
      .then(([audio, reviews]) => {
        if (!abort.signal.aborted) setSnapshot({ context, audio, reviews });
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
  }, [context, organisationId, refresh]);
  const data = snapshot?.context === context ? snapshot : null;
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <h1>Speaking review queue</h1>
      <p>
        Original audio is primary evidence. Playback access does not create a
        transcript or an assessment.
      </p>
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
          {data && (
            <>
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
