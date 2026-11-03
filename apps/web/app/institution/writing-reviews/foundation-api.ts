import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";
import type {
  RubricCriterion,
  RubricEvidence,
  RubricLevel,
  HumanCriterionEntry,
} from "./governance-ui";

export type ReviewRecord = {
  id: string;
  status: string;
  requested_by: string | null;
  requested_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  reviewed_checksum: string;
};
export type RubricVersionSummary = {
  id: string;
  version_label: string;
  status: string;
  checksum: string;
  standard_pack_version_id: string;
  task_spec_id: string | null;
  skill: string;
};
export type RubricSummary = {
  id: string;
  code: string;
  name: string;
  versions: RubricVersionSummary[];
};
export type RubricVersionDetail = RubricVersionSummary &
  RubricEvidence & {
    rubric_id: string;
    name?: string;
    code?: string;
    assessment_family?: string;
    language?: string;
    review: ReviewRecord | null;
    approved_by_user_id?: string | null;
    approved_at?: string | null;
    published_by_user_id?: string | null;
    published_at?: string | null;
    ai_assessment_policy?: Record<string, unknown>;
    human_review_policy?: Record<string, unknown>;
  };
export type WritingSubmission = {
  attempt_id: string;
  exam_version_item_binding_id: string;
  candidate_response_revision_id: string;
  response_checksum: string;
  question_bank_item_version_id: string;
  standard_pack_version_id: string;
  task_spec_id: string | null;
  task_label: string;
  rubric_version_id: string | null;
};
export type PublishedWritingRubric = RubricVersionSummary & {
  rubric_id: string;
  name: string;
  criteria: RubricCriterion[];
  performance_levels: RubricLevel[];
};
export type HumanWritingOptions = {
  submissions: WritingSubmission[];
  rubrics: PublishedWritingRubric[];
};
export type HumanCriterionPayload = {
  criterion_code: string;
  level: string;
  points: string;
  rationale: string;
  evidence: Array<{ quote: string; start: number; end: number }>;
};
export type HumanWritingMutation = {
  id: string;
  version_id?: string;
  status: string;
  checksum: string;
  assessment_origin?: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* Preserve authoritative HTTP failure. */
    }
    throw new AssessmentApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
const root = (organisationId: string) =>
  `/api/v1/organisations/${organisationId}`;
export const writingFoundationApi = {
  rubrics: (org: string) => request<RubricSummary[]>(`${root(org)}/rubrics`),
  rubric: (org: string, rubricId: string, versionId: string) =>
    request<RubricVersionDetail>(
      `${root(org)}/rubrics/${rubricId}/versions/${versionId}`,
    ),
  rubricAction: (
    org: string,
    rubricId: string,
    versionId: string,
    action: "request-review" | "approve" | "publish",
    checksum: string,
    operationId: string,
  ) =>
    request<{ id: string; status: string; checksum: string }>(
      `${root(org)}/rubrics/${rubricId}/versions/${versionId}/${action}`,
      {
        method: "POST",
        body: JSON.stringify({
          operation_id: operationId,
          expected_checksum: checksum,
        }),
      },
    ),
  options: (org: string) =>
    request<HumanWritingOptions>(`${root(org)}/writing-review-options`),
  initialize: (
    org: string,
    source: WritingSubmission,
    rubric: PublishedWritingRubric,
    operationId: string,
  ) =>
    request<HumanWritingMutation>(`${root(org)}/subjective-assessments/human`, {
      method: "POST",
      body: JSON.stringify({
        operation_id: operationId,
        attempt_id: source.attempt_id,
        exam_version_item_binding_id: source.exam_version_item_binding_id,
        rubric_version_id: rubric.id,
        response_checksum: source.response_checksum,
        rubric_checksum: rubric.checksum,
      }),
    }),
  propose: (
    org: string,
    versionId: string,
    checksum: string,
    criteria: HumanCriterionPayload[],
    operationId: string,
  ) =>
    request<HumanWritingMutation>(
      `${root(org)}/subjective-assessments/${versionId}/human-proposal`,
      {
        method: "POST",
        body: JSON.stringify({
          operation_id: operationId,
          expected_checksum: checksum,
          criteria,
        }),
      },
    ),
  approve: (
    org: string,
    versionId: string,
    checksum: string,
    operationId: string,
  ) =>
    request<HumanWritingMutation>(
      `${root(org)}/subjective-assessments/${versionId}/human-approve`,
      {
        method: "POST",
        body: JSON.stringify({
          operation_id: operationId,
          expected_checksum: checksum,
        }),
      },
    ),
  decide: (
    org: string,
    versionId: string,
    checksum: string,
    decision: "REJECTED" | "CHANGES_REQUESTED",
    reason: string,
    operationId: string,
  ) =>
    request<HumanWritingMutation>(
      `${root(org)}/subjective-assessments/${versionId}/human-decision`,
      {
        method: "POST",
        body: JSON.stringify({
          operation_id: operationId,
          expected_checksum: checksum,
          decision,
          reason,
        }),
      },
    ),
};

export function rubricActions(
  rubric: RubricVersionDetail,
  permissions: string[],
  userId: string,
) {
  const manager = permissions.includes("rubric.manage");
  return {
    requestReview: manager && rubric.status === "DRAFT",
    approve:
      manager &&
      rubric.status === "HUMAN_REVIEW" &&
      rubric.review?.status === "PENDING" &&
      rubric.review.reviewed_checksum === rubric.checksum &&
      Boolean(rubric.review.requested_by) &&
      rubric.review.requested_by !== userId,
    publish: manager && rubric.status === "APPROVED",
  };
}

export function humanWritingActions(
  review: {
    assessment_origin: string;
    status: string;
    assessor_id?: string | null;
    checksum?: string;
    review?: { status: string; reviewed_checksum: string } | null;
    legacy_ai_review_eligible?: boolean;
  },
  permissions: string[],
  userId: string,
) {
  return {
    propose:
      review.assessment_origin === "HUMAN" &&
      review.status === "REVIEW_REQUIRED" &&
      permissions.includes("subjective.assess"),
    decide:
      review.assessment_origin === "HUMAN" &&
      review.status === "IN_REVIEW" &&
      permissions.includes("subjective.review") &&
      Boolean(review.assessor_id) &&
      review.assessor_id !== userId &&
      review.review?.status === "PENDING" &&
      review.review.reviewed_checksum === review.checksum,
    decideAI:
      (review.assessment_origin === "AI" ||
        (review.assessment_origin === "UNSPECIFIED" &&
          review.legacy_ai_review_eligible === true)) &&
      review.status === "AI_PROPOSED" &&
      permissions.includes("subjective.review"),
  };
}

export function buildHumanCriteria(
  rubric: RubricEvidence,
  values: Record<string, HumanCriterionEntry>,
  candidateText: string,
): HumanCriterionPayload[] {
  return rubric.criteria.map((criterion) => {
    const value = values[criterion.code];
    if (!value || value.points === "" || !value.level || !value.reason.trim())
      throw new Error(`Complete the human judgment for ${criterion.code}.`);
    if (
      !Number.isFinite(Number(value.points)) ||
      Number(value.points) < 0 ||
      Number(value.points) > Number(criterion.max_points)
    )
      throw new Error(
        `Points for ${criterion.code} must stay within the published rubric bounds.`,
      );
    if (!rubric.performance_levels.some((level) => level.code === value.level))
      throw new Error(
        `Select a published rubric performance level for ${criterion.code}.`,
      );
    const quote = value.evidence;
    if (rubric.evidence_policy.required === true && !quote)
      throw new Error(
        `Exact response evidence is required for ${criterion.code}.`,
      );
    const offset = quote ? candidateText.indexOf(quote) : -1;
    if (quote && offset < 0)
      throw new Error(
        `The quote for ${criterion.code} must exactly match the submitted response.`,
      );
    // Python validates Unicode codepoint offsets; JavaScript indices count UTF-16 units.
    const start = quote ? Array.from(candidateText.slice(0, offset)).length : 0;
    return {
      criterion_code: criterion.code,
      level: value.level,
      points: value.points,
      rationale: value.reason.trim(),
      evidence: quote
        ? [{ quote, start, end: start + Array.from(quote).length }]
        : [],
    };
  });
}

export function eligibleWritingRubrics(
  source: WritingSubmission | undefined,
  rubrics: PublishedWritingRubric[],
): PublishedWritingRubric[] {
  return source?.rubric_version_id
    ? rubrics.filter(
        (rubric) =>
          rubric.id === source.rubric_version_id &&
          rubric.standard_pack_version_id === source.standard_pack_version_id &&
          rubric.task_spec_id === source.task_spec_id &&
          ["PUBLISHED", "ACTIVE"].includes(rubric.status),
      )
    : [];
}
