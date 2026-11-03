import React from "react";
import { PrivateAudioPlayer } from "../../private-audio-player";
import type { AudioEvidenceAsset } from "./review-api";
import css from "../assessment.module.css";

export function AudioEvidenceView({
  item,
  organisationId,
}: {
  item: AudioEvidenceAsset;
  organisationId: string;
}) {
  return (
    <section
      className={css.card}
      aria-label="Bound recorded audio evidence"
      style={{ overflowWrap: "anywhere" }}
    >
      <h2>Original audio — primary evidence</h2>
      <p>
        This screen provides authenticated playback only. No Speaking
        assessment, transcript, or score is created.
      </p>
      <dl className={css.provenance}>
        <dt>Media asset</dt>
        <dd>{item.media_asset_id}</dd>
        <dt>Attempt</dt>
        <dd>{item.attempt_id}</dd>
        <dt>Exact item binding</dt>
        <dd>{item.binding_id}</dd>
        <dt>Item version</dt>
        <dd>{item.item_version_id}</dd>
        <dt>Response revision</dt>
        <dd>
          {item.candidate_response_revision_id ??
            item.response_revision_id ??
            "Unavailable"}
        </dd>
        <dt>Audio checksum</dt>
        <dd>{item.sha256}</dd>
        <dt>Verified duration</dt>
        <dd>{item.duration_ms} ms</dd>
        <dt>Size</dt>
        <dd>{item.byte_size} bytes</dd>
        <dt>Container type</dt>
        <dd>{item.content_type}</dd>
      </dl>
      <PrivateAudioPlayer
        key={item.media_asset_id}
        path={`/api/v1/organisations/${organisationId}/candidate-attempts/${item.attempt_id}/items/${item.binding_id}/audio-assets/${item.media_asset_id}/stream`}
        maximumBytes={item.byte_size}
      />
    </section>
  );
}
