import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";

export type AISpeakingSource = {
  attempt_id: string;
  exam_version_id: string;
  exam_version_item_binding_id: string;
  question_bank_item_version_id: string;
  candidate_response_revision_id: string;
  response_checksum: string;
  standard_pack_version_id: string;
  standard_pack_checksum: string;
  rubric_version_id: string;
  rubric_checksum: string;
  assessment_mode: "AI_ASSISTED";
  candidate_media_asset_id: string;
  audio_sha256: string;
  duration_ms: number;
  recording_policy_checksum: string;
  assessment_policy_checksum: string;
  evidence_mode: "AUDIO";
  speech_transcription_version_id: null;
  transcript_checksum: null;
  task_label: string;
};
export type AISpeakingProfile = {
  execution_profile_id: string;
  execution_profile_version: string;
  model_profile_id: string;
  model_version: string;
  prompt_version_id: string;
  prompt_checksum: string;
  agent_version_id: string;
  agent_checksum: string;
  workflow_version_id: string;
  workflow_checksum: string;
  name: string;
  provider: string;
  model_name: string;
  adapter_version: string;
  test_only: boolean;
  availability: "CONFIGURED" | "UNCONFIGURED" | "TEST_ONLY";
  unavailable_reason: string | null;
};
export type AISpeakingJob = Omit<AISpeakingSource, "task_label"> &
  Omit<AISpeakingProfile, "name" | "availability" | "unavailable_reason"> & {
    id: string;
    organisation_id: string;
    operation_id: string;
    status:
      | "QUEUED"
      | "RUNNING"
      | "RETRYABLE"
      | "COMPLETED"
      | "FAILED"
      | "CANCELLED";
    ai_run_id: string | null;
    ai_run_status: string | null;
    assessment_id: string | null;
    assessment_version_id: string | null;
    review_id: string | null;
    review_status: string | null;
    attempt_count: number;
    max_attempts: number;
    created_at: string;
    started_at: string | null;
    completed_at: string | null;
    failure_category: string | null;
    can_retry: boolean;
    can_cancel: boolean;
    state_history: Array<{
      status: string;
      at: string;
      attempt_count: number;
      failure_category?: string | null;
    }>;
  };
export type SpeakingExecutionProvenance = {
  attempt_id: string;
  exam_version_id: string;
  exam_version_item_binding_id: string;
  question_bank_item_version_id: string;
  standard_pack_version_id: string;
  standard_pack_checksum: string;
  candidate_media_asset_id: string;
  audio_sha256: string;
  duration_ms: number;
  recording_policy_checksum: string;
  assessment_policy_checksum: string;
  evidence_mode: "AUDIO";
  speech_transcription_version_id: null;
  transcript_checksum: null;
  execution_job_id: string;
  execution_status: string;
  ai_run_id: string;
  ai_run_status: string;
  execution_profile_id: string;
  profile_version: string;
  model_profile_id: string;
  model_version: string;
  prompt_version_id: string;
  prompt_checksum: string;
  agent_version_id: string;
  agent_checksum: string;
  workflow_version_id: string;
  workflow_checksum: string;
  candidate_response_revision_id: string;
  response_checksum: string;
  rubric_version_id: string;
  rubric_checksum: string;
  test_only: boolean;
  provider: string;
  model_name: string;
  adapter_version: string;
};
export type AISpeakingOptions = {
  sources: AISpeakingSource[];
  profiles: AISpeakingProfile[];
};
const sourcePins = [
  "attempt_id",
  "exam_version_id",
  "exam_version_item_binding_id",
  "question_bank_item_version_id",
  "candidate_response_revision_id",
  "response_checksum",
  "standard_pack_version_id",
  "standard_pack_checksum",
  "rubric_version_id",
  "rubric_checksum",
  "candidate_media_asset_id",
  "audio_sha256",
  "recording_policy_checksum",
  "assessment_policy_checksum",
] as const;
export function supportedSpeakingExecutor(profile: {
  provider: string;
  model_name: string;
  adapter_version: string;
  test_only: boolean;
}) {
  return (
    profile.adapter_version === "1" &&
    ((profile.test_only === true &&
      profile.provider === "DETERMINISTIC_TEST" &&
      profile.model_name === "speaking-fixture-v1") ||
      (profile.test_only === false &&
        profile.provider === "AZURE_OPENAI" &&
        profile.model_name === "gpt-audio-1.5"))
  );
}
export function exactAISpeakingSource(
  source: AISpeakingSource,
  job: AISpeakingJob,
) {
  return (
    source.evidence_mode === "AUDIO" &&
    job.evidence_mode === "AUDIO" &&
    source.duration_ms > 0 &&
    Number.isSafeInteger(source.duration_ms) &&
    source.duration_ms === job.duration_ms &&
    source.speech_transcription_version_id === null &&
    source.transcript_checksum === null &&
    job.speech_transcription_version_id === null &&
    job.transcript_checksum === null &&
    source.assessment_mode === "AI_ASSISTED" &&
    job.assessment_mode === "AI_ASSISTED" &&
    sourcePins.every((key) => Boolean(source[key]) && source[key] === job[key])
  );
}
export function completedSpeakingProposal(job: AISpeakingJob) {
  return (
    job.assessment_mode === "AI_ASSISTED" &&
    job.evidence_mode === "AUDIO" &&
    Number.isSafeInteger(job.duration_ms) &&
    job.duration_ms > 0 &&
    job.speech_transcription_version_id === null &&
    job.transcript_checksum === null &&
    job.status === "COMPLETED" &&
    job.ai_run_status === "SUCCEEDED" &&
    sourcePins.every((key) => Boolean(job[key])) &&
    supportedSpeakingExecutor(job) &&
    Boolean(
      job.completed_at &&
        job.ai_run_id &&
        job.assessment_id &&
        job.assessment_version_id &&
        job.review_id &&
        job.review_status &&
        [
          "PENDING",
          "IN_REVIEW",
          "APPROVED",
          "REJECTED",
          "CHANGES_REQUESTED",
          "CANCELLED",
        ].includes(job.review_status) &&
        job.execution_profile_version &&
        job.execution_profile_id &&
        job.model_profile_id &&
        job.model_version &&
        job.prompt_version_id &&
        job.prompt_checksum &&
        job.agent_version_id &&
        job.agent_checksum &&
        job.workflow_version_id &&
        job.workflow_checksum,
    )
  );
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      /* Preserve the authoritative HTTP status for service outages. */
    }
    throw new AssessmentApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
const root = (org: string) =>
  `/api/v1/organisations/${org}/speaking-executions`;
export const speakingExecutionApi = {
  options: (org: string, signal?: AbortSignal) =>
    request<AISpeakingOptions>(`${root(org)}/options`, { signal }),
  list: (org: string, signal?: AbortSignal) =>
    request<{ jobs: AISpeakingJob[] }>(root(org), { signal }),
  job: (org: string, id: string, signal?: AbortSignal) =>
    request<AISpeakingJob>(`${root(org)}/${id}`, { signal }),
  request: (
    org: string,
    source: AISpeakingSource,
    profile: AISpeakingProfile,
    operationId: string,
  ) =>
    request<AISpeakingJob>(root(org), {
      method: "POST",
      body: JSON.stringify({
        operation_id: operationId,
        attempt_id: source.attempt_id,
        exam_version_item_binding_id: source.exam_version_item_binding_id,
        candidate_response_revision_id: source.candidate_response_revision_id,
        response_checksum: source.response_checksum,
        candidate_media_asset_id: source.candidate_media_asset_id,
        audio_sha256: source.audio_sha256,
        duration_ms: source.duration_ms,
        recording_policy_checksum: source.recording_policy_checksum,
        assessment_policy_checksum: source.assessment_policy_checksum,
        rubric_version_id: source.rubric_version_id,
        rubric_checksum: source.rubric_checksum,
        execution_profile_id: profile.execution_profile_id,
        execution_profile_version: profile.execution_profile_version,
        model_profile_id: profile.model_profile_id,
        prompt_version_id: profile.prompt_version_id,
        agent_version_id: profile.agent_version_id,
        workflow_version_id: profile.workflow_version_id,
      }),
    }),
  retry: (org: string, id: string) =>
    request<AISpeakingJob>(`${root(org)}/${id}/retry`, { method: "POST" }),
  cancel: (org: string, id: string) =>
    request<AISpeakingJob>(`${root(org)}/${id}/cancel`, { method: "POST" }),
};
