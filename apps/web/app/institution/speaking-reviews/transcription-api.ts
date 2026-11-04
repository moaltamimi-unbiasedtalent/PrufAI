import { authenticatedFetch } from "../../../lib/api";
import type { AudioEvidenceAsset } from "./review-api";

export type TranscriptionSource = {
  candidate_media_asset_id: string;
  candidate_response_revision_id: string;
  audio_sha256: string;
  verified_duration_ms: number;
};
export type TranscriptionProfile = {
  id: string;
  name: string;
  version: string;
  provider: string;
  model_name: string;
  adapter_version: string;
  languages: string[];
  test_only: boolean;
  availability: "CONFIGURED" | "TEST_ONLY" | "UNCONFIGURED";
  unavailable_reason: string | null;
};
export type TranscriptionOptions = {
  source: TranscriptionSource;
  profiles: TranscriptionProfile[];
};
export type TranscriptionJob = {
  id: string;
  transcription_id: string;
  asset_id: string;
  candidate_response_revision_id: string;
  audio_sha256: string;
  verified_duration_ms: number;
  status: string;
  profile_id: string;
  profile_version: string;
  provider: string;
  model_name: string;
  adapter_version: string;
  language: string;
  test_only: boolean;
  attempt_count: number;
  max_attempts: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  failure_category: string | null;
  transcription_version_id: string | null;
  can_retry: boolean;
  can_cancel: boolean;
  state_history: Array<{
    status: string;
    at: string;
    attempt_count: number;
    failure_category?: string;
  }>;
};
export type TranscriptVersionSummary = {
  id: string;
  version_number: number;
  status: string;
  checksum: string;
};
export type AssetTranscriptions = {
  transcription_id: string | null;
  asset_id: string;
  jobs: TranscriptionJob[];
  versions: TranscriptVersionSummary[];
};
export type TranscriptVersion = TranscriptVersionSummary & {
  transcription_id: string;
  candidate_media_asset_id: string;
  candidate_response_revision_id: string | null;
  audio_sha256: string;
  execution_job_id: string | null;
  profile_version: string | null;
  provider: string;
  model_name: string;
  adapter_version: string;
  language: string;
  test_only: boolean;
  transcript: string;
  segments: Array<{
    segment_id: string;
    start_ms: number;
    end_ms: number;
    text: string;
  }>;
};

export class TranscriptionApiError extends Error {
  constructor(readonly status: number) {
    super(
      status === 401
        ? "Your session has expired. Sign in again."
        : status === 403
          ? "Your institution permissions do not allow this transcription action."
          : status === 409
            ? "The exact recording, profile or job state has changed. Refresh before retrying."
            : status === 400 || status === 422
              ? "This transcription request is not available for the selected recording and approved profile. Check configuration and refresh."
              : "Transcription is unavailable. Refresh its persisted status before retrying.",
    );
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) throw new TranscriptionApiError(response.status);
  return (await response.json()) as T;
}
const organisationPath = (organisationId: string) =>
  `/api/v1/organisations/${encodeURIComponent(organisationId)}`;
const assetPath = (organisationId: string, assetId: string) =>
  `${organisationPath(organisationId)}/candidate-media/${encodeURIComponent(assetId)}/transcriptions`;

export function exactTranscriptionSource(
  item: AudioEvidenceAsset,
  source: TranscriptionSource,
): boolean {
  const revision =
    item.candidate_response_revision_id ?? item.response_revision_id;
  return Boolean(
    revision &&
      source.candidate_media_asset_id === item.media_asset_id &&
      source.candidate_response_revision_id === revision &&
      source.audio_sha256 === item.sha256 &&
      source.verified_duration_ms === item.duration_ms,
  );
}

export function exactTranscriptVersion(
  item: AudioEvidenceAsset,
  version: TranscriptVersion,
  job: TranscriptionJob | undefined,
): boolean {
  return Boolean(
    job &&
      job.status === "READY" &&
      ["READY", "SUPERSEDED"].includes(version.status) &&
      version.execution_job_id === job.id &&
      version.transcription_id === job.transcription_id &&
      version.id === job.transcription_version_id &&
      version.profile_version === job.profile_version &&
      version.test_only === job.test_only &&
      version.provider === job.provider &&
      version.model_name === job.model_name &&
      version.adapter_version === job.adapter_version &&
      version.language === job.language &&
      exactTranscriptionSource(item, {
        candidate_media_asset_id: version.candidate_media_asset_id,
        candidate_response_revision_id:
          version.candidate_response_revision_id ?? "",
        audio_sha256: version.audio_sha256,
        verified_duration_ms: job.verified_duration_ms,
      }) &&
      exactTranscriptionSource(item, {
        candidate_media_asset_id: job.asset_id,
        candidate_response_revision_id: job.candidate_response_revision_id,
        audio_sha256: job.audio_sha256,
        verified_duration_ms: job.verified_duration_ms,
      }),
  );
}

export const transcriptionApi = {
  options: (organisationId: string, assetId: string, signal?: AbortSignal) =>
    request<TranscriptionOptions>(
      `${assetPath(organisationId, assetId)}/options`,
      { signal },
    ),
  history: (organisationId: string, assetId: string, signal?: AbortSignal) =>
    request<AssetTranscriptions>(assetPath(organisationId, assetId), {
      signal,
    }),
  request: (
    organisationId: string,
    source: TranscriptionSource,
    profileId: string,
    language: string,
    operationId: string,
  ) =>
    request<TranscriptionJob>(
      assetPath(organisationId, source.candidate_media_asset_id),
      {
        method: "POST",
        body: JSON.stringify({
          profile_id: profileId,
          language,
          operation_id: operationId,
          candidate_response_revision_id: source.candidate_response_revision_id,
          audio_sha256: source.audio_sha256,
        }),
      },
    ),
  retry: (organisationId: string, jobId: string, operationId: string) =>
    request<TranscriptionJob>(
      `${organisationPath(organisationId)}/speech-transcription-jobs/${encodeURIComponent(jobId)}/retry`,
      {
        method: "POST",
        body: JSON.stringify({ operation_id: operationId }),
      },
    ),
  version: (
    organisationId: string,
    transcriptionId: string,
    versionId: string,
    signal?: AbortSignal,
  ) =>
    request<TranscriptVersion>(
      `${organisationPath(organisationId)}/speech-transcriptions/${encodeURIComponent(transcriptionId)}/versions/${encodeURIComponent(versionId)}`,
      { signal },
    ),
};
