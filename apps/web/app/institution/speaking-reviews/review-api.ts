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

async function request<T>(path: string): Promise<T> {
  const response = await authenticatedFetch(path);
  if (!response.ok) throw new Error(`REVIEW_HTTP_${response.status}`);
  return (await response.json()) as T;
}

export const speakingReviewApi = {
  list: (organisationId: string) =>
    request<SpeakingReviewSummary[]>(
      `/api/v1/organisations/${organisationId}/speaking-reviews`,
    ),
  detail: (organisationId: string, assessmentId: string) =>
    request<SpeakingReview>(
      `/api/v1/organisations/${organisationId}/speaking-reviews/${assessmentId}`,
    ),
};
import { authenticatedFetch } from "../../../lib/api";
