import { authenticatedFetch } from "../../lib/api";
import type { AssessmentComponent } from "./assessment-evidence";

export type HumanResultReview = {
  id: string;
  status: string;
  reviewed_checksum: string;
};
export type ResultSummary = {
  id: string;
  version_id: string;
  version_number: number;
  status: string;
  outcome: string | null;
  raw_awarded_points: string | null;
  raw_maximum_points: string;
  checksum: string;
  scoring_run_id: string;
  review: HumanResultReview | null;
};
export type AssignmentInspection = {
  id: string;
  candidate_user_id: string;
  candidate_name: string | null;
  exam_name: string | null;
  exam_version_label: string | null;
  exam_version_id: string;
  status: string;
  maximum_attempts: number;
  attempts: Array<{
    id: string;
    status: string;
    attempt_number: number;
    result: ResultSummary | null;
  }>;
};
export type AssignmentOptions = {
  candidates: Array<{ id: string; display_name: string | null }>;
  exam_versions: Array<{
    id: string;
    exam_id: string;
    exam_name: string;
    version_label: string;
    status: string;
    classification: string;
    standard_pack_version_id: string;
    checksum: string;
  }>;
};
export type ResultVersion = {
  result_id: string;
  result_version_id: string;
  version_number: number;
  attempt_id: string;
  scoring_run_id: string;
  status: string;
  raw_awarded_points: string | null;
  raw_maximum_points: string | null;
  outcome: string | null;
  incomplete_reason: string | null;
  checksum: string;
  response_set_checksum: string;
  scoring_run_checksum: string;
  rule_provenance: Record<string, unknown>;
  created_at: string;
  released_at: string | null;
  review: HumanResultReview | null;
  assessment_components?: AssessmentComponent[];
};
export type ScoringRun = {
  id: string;
  attempt_id: string;
  status: string;
  response_set_checksum: string;
  scoring_engine: string;
  checksum: string;
};

export class AssessmentApiError extends Error {
  constructor(
    readonly status: number,
    detail?: string,
  ) {
    const prefix =
      status === 403
        ? "Permission denied."
        : status === 409
          ? "This action conflicts with the current assessment state. Refresh before continuing."
          : status === 401
            ? "Your session expired. Sign in again."
            : "The assessment request could not be completed.";
    super(detail ? `${prefix} ${detail}` : prefix);
  }
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      /* Preserve HTTP status for non-JSON outages. */
    }
    throw new AssessmentApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
const organisation = (id: string) => `/api/v1/organisations/${id}`;
export const assessmentApi = {
  assignments: (org: string) =>
    request<AssignmentInspection[]>(`${organisation(org)}/exam-assignments`),
  options: (org: string) =>
    request<AssignmentOptions>(`${organisation(org)}/exam-assignment-options`),
  assign: (org: string, candidateId: string, examVersionId: string) =>
    request<{ id: string; status: string }>(
      `${organisation(org)}/exam-assignments`,
      {
        method: "POST",
        body: JSON.stringify({
          candidate_user_id: candidateId,
          exam_version_id: examVersionId,
          maximum_attempts: 1,
        }),
      },
    ),
  score: (org: string, attemptId: string) =>
    request<{ id: string; status: string; checksum: string }>(
      `${organisation(org)}/exam-attempts/${attemptId}/score`,
      { method: "POST" },
    ),
  versions: (org: string, resultId: string) =>
    request<ResultVersion[]>(
      `${organisation(org)}/results/${resultId}/versions`,
    ),
  scoringRun: (org: string, runId: string) =>
    request<ScoringRun>(`${organisation(org)}/scoring-runs/${runId}`),
  requestReview: (org: string, versionId: string) =>
    request<HumanResultReview>(
      `${organisation(org)}/result-versions/${versionId}/request-review`,
      { method: "POST" },
    ),
  approve: (org: string, versionId: string) =>
    request<{ id: string; status: string }>(
      `${organisation(org)}/result-versions/${versionId}/approve`,
      { method: "POST" },
    ),
};

export function resultActions(
  version: ResultVersion,
  latest: boolean,
  permissions: string[],
) {
  const reviewMatches = version.review?.reviewed_checksum === version.checksum;
  return {
    requestReview:
      latest &&
      version.status === "REVIEW_REQUIRED" &&
      permissions.includes("result.review") &&
      !(reviewMatches && version.review?.status === "PENDING"),
    approve:
      latest &&
      version.status === "REVIEW_REQUIRED" &&
      permissions.includes("result.review") &&
      reviewMatches &&
      version.review?.status === "PENDING",
    release:
      latest &&
      version.status === "APPROVED" &&
      permissions.includes("result.release"),
  };
}

export function canScoreAttempt(
  attempt: { status: string; result: { status: string } | null },
  permissions: string[],
) {
  return (
    permissions.includes("scoring.run") &&
    attempt.status === "SUBMITTED" &&
    (!attempt.result || attempt.result.status === "INCOMPLETE")
  );
}
