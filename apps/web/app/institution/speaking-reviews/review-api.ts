import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";
import type { AudioMediaAsset } from "../../candidate/api";

export type AudioEvidenceAsset = AudioMediaAsset & {
  attempt_id: string;
  binding_id: string;
  item_version_id: string;
  exam_version_id: string;
  recording_session_id: string;
  response_revision_id?: string;
};

export type SpeakingReviewSummary = {
  assessment_id: string;
  assessment_version_id: string;
  attempt_id: string;
  status: string;
  review_required: boolean;
  transcript_available: boolean;
  disagreement: Record<string, unknown>;
};

export type SpeakingReview = SpeakingReviewSummary & {
  original_audio_primary_evidence: boolean;
  derived_transcription: boolean;
  candidate_media_asset_id: string;
  audio_sha256: string | null;
  speech_transcription_version_id: string | null;
  transcript_checksum: string | null;
  evidence_modes: Record<string, unknown>;
  ai_proposal: Record<string, unknown>;
  human_approved_values: Record<string, unknown>;
  confidence: Record<string, unknown>;
  consistency: Record<string, unknown>;
  criteria: Array<Record<string, unknown>>;
};

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await authenticatedFetch(path, { signal });
  if (!response.ok) throw new AssessmentApiError(response.status);
  return (await response.json()) as T;
}

export const speakingReviewApi = {
  audioList: (organisationId: string, signal?: AbortSignal) =>
    request<AudioEvidenceAsset[]>(
      `/api/v1/organisations/${organisationId}/audio-evidence`,
      signal,
    ),
  audioDetail: (
    organisationId: string,
    assetId: string,
    signal?: AbortSignal,
  ) =>
    request<AudioEvidenceAsset>(
      `/api/v1/organisations/${organisationId}/audio-evidence/${assetId}`,
      signal,
    ),
  list: (organisationId: string, signal?: AbortSignal) =>
    request<SpeakingReviewSummary[]>(
      `/api/v1/organisations/${organisationId}/speaking-reviews`,
      signal,
    ),
  detail: (
    organisationId: string,
    assessmentId: string,
    signal?: AbortSignal,
  ) =>
    request<SpeakingReview>(
      `/api/v1/organisations/${organisationId}/speaking-reviews/${assessmentId}`,
      signal,
    ),
};
