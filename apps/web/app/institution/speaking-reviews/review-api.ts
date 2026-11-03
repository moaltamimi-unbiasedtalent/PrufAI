import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";
import type { AudioMediaAsset } from "../../candidate/api";
import type {
  RubricVersionDetail,
  ReviewRecord,
} from "../writing-reviews/foundation-api";

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
  assessment_origin?: "HUMAN" | "AI" | "UNSPECIFIED";
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
  version_id?: string;
  checksum?: string;
  assessment_origin?: "HUMAN" | "AI" | "UNSPECIFIED";
  assessor_id?: string | null;
  reviewer_id?: string | null;
  approved_score?: string | null;
  rubric?: RubricVersionDetail | null;
  review?:
    | (ReviewRecord & { decision?: string | null; reason?: string | null })
    | null;
  candidate_response_revision_id?: string;
  response_checksum?: string;
  question_bank_item_version_id?: string;
  standard_pack_version_id?: string;
  task_label?: string;
  task_prompt?: string | Record<string, unknown> | null;
  task_prompt_available?: boolean;
  is_current_version?: boolean;
  current_version_id?: string | null;
  provenance?: Record<string, unknown>;
  history?: Array<{
    id: string;
    version_number: number;
    status: string;
    checksum: string;
  }>;
  audio?: AudioEvidenceAsset;
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
    versionId?: string,
  ) =>
    request<SpeakingReview>(
      `/api/v1/organisations/${organisationId}/speaking-reviews/${assessmentId}${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`,
      signal,
    ),
};
