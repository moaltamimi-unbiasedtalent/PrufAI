import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";

export type AIWritingSource = {
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
  task_label: string;
};
export type AIWritingProfile = {
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
export type AIWritingJob = Omit<AIWritingSource, "task_label"> &
  Omit<AIWritingProfile, "name" | "availability" | "unavailable_reason"> & {
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
export type WritingExecutionProvenance = {
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
export type AIWritingOptions = {
  sources: AIWritingSource[];
  profiles: AIWritingProfile[];
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
] as const;
export function exactAIWritingSource(
  source: AIWritingSource,
  job: AIWritingJob,
) {
  return (
    source.assessment_mode === "AI_ASSISTED" &&
    job.assessment_mode === "AI_ASSISTED" &&
    sourcePins.every((key) => Boolean(source[key]) && source[key] === job[key])
  );
}
export function completedWritingProposal(job: AIWritingJob) {
  return (
    job.assessment_mode === "AI_ASSISTED" &&
    job.status === "COMPLETED" &&
    job.ai_run_status === "SUCCEEDED" &&
    sourcePins.every((key) => Boolean(job[key])) &&
    typeof job.test_only === "boolean" &&
    ["OPENAI", "DETERMINISTIC_TEST"].includes(job.provider) &&
    job.test_only === (job.provider === "DETERMINISTIC_TEST") &&
    Boolean(
      job.completed_at &&
        job.ai_run_id &&
        job.assessment_id &&
        job.assessment_version_id &&
        job.review_id &&
        job.review_status &&
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
const root = (org: string) => `/api/v1/organisations/${org}/writing-executions`;
export const writingExecutionApi = {
  options: (org: string, signal?: AbortSignal) =>
    request<AIWritingOptions>(`${root(org)}/options`, { signal }),
  list: (org: string, signal?: AbortSignal) =>
    request<{ jobs: AIWritingJob[] }>(root(org), { signal }),
  job: (org: string, id: string, signal?: AbortSignal) =>
    request<AIWritingJob>(`${root(org)}/${id}`, { signal }),
  request: (
    org: string,
    source: AIWritingSource,
    profile: AIWritingProfile,
    operationId: string,
  ) =>
    request<AIWritingJob>(root(org), {
      method: "POST",
      body: JSON.stringify({
        operation_id: operationId,
        attempt_id: source.attempt_id,
        exam_version_item_binding_id: source.exam_version_item_binding_id,
        candidate_response_revision_id: source.candidate_response_revision_id,
        response_checksum: source.response_checksum,
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
    request<AIWritingJob>(`${root(org)}/${id}/retry`, { method: "POST" }),
  cancel: (org: string, id: string) =>
    request<AIWritingJob>(`${root(org)}/${id}/cancel`, { method: "POST" }),
};
