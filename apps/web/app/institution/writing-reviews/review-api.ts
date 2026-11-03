import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";
import type { RubricEvidence } from "./governance-ui";
import type { ReviewRecord } from "./foundation-api";

export type WritingReview = {
  id: string;
  version_id: string;
  status: string;
  checksum: string;
  candidate_text: string;
  rubric: RubricEvidence | null;
  criteria: Array<Record<string, unknown>>;
  assessment_origin: "HUMAN" | "AI" | "UNSPECIFIED";
  assessor_id?: string | null;
  reviewer_id?: string | null;
  assessed_at?: string | null;
  reviewed_at?: string | null;
  task_label?: string;
  task_prompt?: string;
  task_prompt_available?: boolean;
  attempt_id?: string;
  exam_version_id?: string;
  exam_version_item_binding_id?: string;
  question_bank_item_version_id?: string;
  candidate_response_revision_id?: string;
  response_checksum?: string;
  standard_pack_version_id?: string;
  ai_run_id?: string | null;
  legacy_ai_review_eligible?: boolean;
  approved_score?: string | null;
  review?: ReviewRecord | null;
  provenance?: {
    assessed_at?: string;
    reviewed_at?: string;
    decision?: string;
    decision_reason?: string;
    [key: string]: unknown;
  };
  skill?: string | null;
};
export type WritingReviewSummary = {
  id: string;
  attempt_id: string;
  status: string;
  current_version_id: string | null;
  assessment_origin?: "HUMAN" | "AI" | "UNSPECIFIED";
  skill?: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* Preserve HTTP status for outages. */
    }
    throw new AssessmentApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const writingReviewApi = {
  list: (organisationId: string) =>
    request<WritingReviewSummary[]>(
      `/api/v1/organisations/${organisationId}/subjective-assessments`,
    ),
  detail: (organisationId: string, assessmentId: string) =>
    request<WritingReview>(
      `/api/v1/organisations/${organisationId}/subjective-assessments/${assessmentId}`,
    ),
  history: (organisationId: string, assessmentId: string) =>
    request<{
      current_version_id: string | null;
      versions: Array<{
        id: string;
        version_number: number;
        status: string;
        checksum: string;
      }>;
    }>(
      `/api/v1/organisations/${organisationId}/subjective-assessments/${assessmentId}/versions`,
    ),
  approve: (organisationId: string, versionId: string) =>
    request(
      `/api/v1/organisations/${organisationId}/subjective-assessments/${versionId}/approve`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  decide: (
    organisationId: string,
    versionId: string,
    decision: "REJECTED" | "CHANGES_REQUESTED",
  ) =>
    request(
      `/api/v1/organisations/${organisationId}/subjective-assessments/${versionId}/decision`,
      { method: "POST", body: JSON.stringify({ decision }) },
    ),
};
