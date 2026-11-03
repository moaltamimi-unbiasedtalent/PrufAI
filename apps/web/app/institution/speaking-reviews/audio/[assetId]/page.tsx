"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useAuth } from "../../../../../lib/auth";
import { speakingReviewApi, type AudioEvidenceAsset } from "../../review-api";
import { AudioEvidenceView } from "../../audio-evidence-view";

export default function RecordedAudioPage({
  params,
}: {
  params: Promise<{ assetId: string }>;
}) {
  const { user, organisationId, permissions } = useAuth();
  const context =
    user && organisationId && permissions.includes("subjective.read")
      ? `${user.id}:${organisationId}`
      : null;
  const [snapshot, setSnapshot] = useState<{
    context: string;
    item: AudioEvidenceAsset;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    setSnapshot(null);
    setError(null);
    if (!context || !organisationId) return;
    const abort = new AbortController();
    void params
      .then(({ assetId }) =>
        speakingReviewApi.audioDetail(organisationId, assetId, abort.signal),
      )
      .then((item) => {
        if (!abort.signal.aborted) setSnapshot({ context, item });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "The audio evidence could not be loaded.",
          );
      });
    return () => abort.abort();
  }, [context, organisationId, params, refresh]);
  const data = snapshot?.context === context ? snapshot.item : null;
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/institution/speaking-reviews">
          ← Speaking review queue
        </Link>
      </p>
      <h1>Recorded audio evidence</h1>
      {!context ? (
        <p role="alert">
          Subjective read permission in the selected institution is required.
        </p>
      ) : error ? (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => setRefresh((value) => value + 1)}>
            Retry audio evidence
          </button>
        </div>
      ) : data && organisationId ? (
        <AudioEvidenceView item={data} organisationId={organisationId} />
      ) : (
        <p role="status">Loading exact audio evidence…</p>
      )}
    </section>
  );
}
