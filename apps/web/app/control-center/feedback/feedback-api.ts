import { authenticatedFetch } from "../../../lib/api";
import type { EvaluationPage } from "../evaluation/evaluation-api";

export const categories = [
  "RUBRIC_ALIGNMENT",
  "SCORING_CORRECTNESS",
  "EVIDENCE_PROVENANCE",
  "CITATION_GROUNDING",
  "RETRIEVAL_QUALITY",
  "TRANSCRIPTION_ERROR",
  "AUDIO_EVIDENCE",
  "EXAM_CONTENT",
  "MODEL_OUTPUT",
  "ACCESS_CONTROL",
  "RUNTIME_RELIABILITY",
  "ACCESSIBILITY",
  "OTHER_REVIEW_REQUIRED",
] as const;
export const severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type Pin = { id: string; checksum: string };
export type Classification = {
  category: (typeof categories)[number];
  severity: (typeof severities)[number];
};

export const feedbackReasons = {
  TRIAGE: ["SOURCE_REVIEWED"],
  CONFIRM: ["REPRODUCED", "GOVERNED_EVIDENCE_CONFIRMED"],
  NOT_CONFIRM: ["INSUFFICIENT_EVIDENCE", "NOT_REPRODUCIBLE"],
  CLOSE: ["RESOLVED_WITHOUT_CHANGE"],
} as const;
type FeedbackAction = keyof typeof feedbackReasons;

export const proposalReasons = {
  APPROVE_FOR_EVALUATION: ["EVALUATION_PLAN_SUFFICIENT"],
  REJECT: ["INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE", "DUPLICATE"],
  CLOSE: ["SUPERSEDED", "WITHDRAWN"],
} as const;
type ProposalAction = keyof typeof proposalReasons;

export const targetTypes = [
  "STANDARD_PACK_VERSION",
  "RUBRIC_VERSION",
  "PROMPT_VERSION",
  "WORKFLOW_VERSION",
  "QUESTION_BANK_ITEM_VERSION",
  "EXAM_VERSION",
  "MODEL_PROFILE",
  "TRANSCRIPTION_PROFILE",
] as const;
export const changeCategories = [
  "VALIDATION_GUARD",
  "CONTENT_CORRECTION",
  "RUBRIC_CLARIFICATION",
  "PROMPT_REVISION",
  "MODEL_CONFIGURATION",
  "WORKFLOW_REVISION",
] as const;
export const hypotheses = [
  "PREVENT_RECORDED_FAILURE",
  "IMPROVE_REFERENCE_AGREEMENT",
  "IMPROVE_EVIDENCE_GROUNDING",
  "IMPROVE_RECOVERY_RELIABILITY",
] as const;
type TargetPin = Pin & { type: (typeof targetTypes)[number] };

type Source =
  | {
      kind: "EVALUATION_FAILURE";
      run: Pin;
      result: Pin;
      failed_check: string;
    }
  | {
      kind: "REVIEWER_FLAG";
      artifact_type: "SUBJECTIVE_ASSESSMENT_VERSION";
      artifact: Pin;
    };

type Intake = Classification & { source: Source };
type Decision<Action> = {
  expected_checksum: string;
  action: Action;
  reason_code: string;
};
type ProposalInput = {
  pattern: Pin;
  target: TargetPin;
  change_category: (typeof changeCategories)[number];
  hypothesis_code: (typeof hypotheses)[number];
  suite_version?: Pin;
  baseline_run?: Pin;
  candidate_run?: Pin;
  predecessor?: Pin;
};

export type FeedbackItem = Pin &
  Classification & {
    organisation_id: string | null;
    source_kind: string;
    source_id: string;
    source_checksum: string;
    source_run?: Pin | null;
    failed_check?: string | null;
    assessment_family: string;
    skill: string;
    evidence_class: string;
    privacy_class: string;
    status: string;
    requires_reference: boolean;
    execution_class: string | null;
    reason_code: string | null;
    method: string | null;
    artifact_pins: TargetPin[];
    created_at: string;
    allowed_actions: FeedbackAction[];
  };

export type Pattern = Pin & {
  status: string;
  category: string;
  evidence_class: string;
  privacy_class: string;
  member_count: number;
  artifact_pins: TargetPin[];
  requires_reference: boolean;
  required_checks: string[];
  distinct_source_count: number;
  members: (Pin & { confirmation_operation_id: string })[];
};

export type Proposal = Pin & {
  proposal_id: string;
  version_number: number;
  pattern: Pin;
  target: TargetPin & { version_label: string };
  receipt: Pin;
  reviewed_by: string | null;
  change_category: string;
  hypothesis_code: string;
  status: string;
  evaluation_requirements: {
    status: "REQUIRE_EVALUATION" | "REGRESSION_SUITE_LINKED";
    required_checks: string[];
    reference_evidence:
      | "REQUIRE_APPROVED_REFERENCE"
      | "NO_ADDITIONAL_REFERENCE_REQUIRED";
  };
  links: {
    suite_version?: Pin | null;
    baseline_run?: (Pin & { hard_gate: string }) | null;
    candidate_run?: (Pin & { hard_gate: string }) | null;
    quality_gate: string;
  };
  release_readiness: "NOT_READY";
  allowed_actions: ProposalAction[];
  decision_reason_code: string | null;
  production_activation: false;
};

export type FeedbackSection = "items" | "patterns" | "proposals";
export type FeedbackRecords = {
  items: FeedbackItem;
  patterns: Pattern;
  proposals: Proposal;
};

const readPrefix = "/api/v1/platform/control-center/feedback";
const writePrefix = "/api/v1/platform/feedback";
const tenantPath = (id: string) =>
  `/api/v1/organisations/${encodeURIComponent(id)}/feedback`;

export class FeedbackApiError extends Error {
  constructor(readonly status: number) {
    super(
      {
        401: "Your session has expired. Sign in again.",
        403: "Your current permissions do not allow this feedback action.",
        404: "The exact receipt was not found or is outside your scope.",
        409: "The request conflicts with the current evidence or decision. Reload the exact receipt and review its state.",
        422: "A required field or exact receipt is invalid.",
      }[status] ?? "Feedback is currently unavailable. Please try again.",
    );
  }
}

async function request<T>(path: string, data?: object): Promise<T> {
  const response = await authenticatedFetch(
    path,
    data ? { method: "POST", body: JSON.stringify(data) } : undefined,
  );
  if (!response.ok) throw new FeedbackApiError(response.status);
  return (await response.json()) as T;
}

type Operation = { operation_id: string };
export const feedbackApi = {
  list: <K extends FeedbackSection>(section: K, offset: number) =>
    request<EvaluationPage<FeedbackRecords[K]>>(
      `${readPrefix}/${section}?${new URLSearchParams({ offset: String(offset), limit: "20" })}`,
    ),
  detail: <K extends FeedbackSection>(section: K, id: string) =>
    request<FeedbackRecords[K]>(
      `${readPrefix}/${section}/${encodeURIComponent(id)}`,
    ),
  intake: (data: Intake & Operation) =>
    request<FeedbackItem>(`${writePrefix}/items`, data),
  decide: (
    id: string,
    data: Decision<FeedbackAction> & Partial<Classification> & Operation,
  ) =>
    request<FeedbackItem>(
      `${writePrefix}/items/${encodeURIComponent(id)}/decision`,
      data,
    ),
  pattern: (data: { anchor: Pin } & Operation) =>
    request<Pattern>(`${writePrefix}/patterns`, data),
  propose: (data: ProposalInput & Operation) =>
    request<Proposal>(`${writePrefix}/proposals`, data),
  review: (id: string, data: Decision<ProposalAction> & Operation) =>
    request<Proposal>(
      `${writePrefix}/proposals/${encodeURIComponent(id)}/decision`,
      data,
    ),
  tenantList: (organisation: string, version: string) =>
    request<EvaluationPage<FeedbackItem>>(
      `${tenantPath(organisation)}?${new URLSearchParams({ source_id: version, limit: "20" })}`,
    ),
  tenantFlag: (organisation: string, data: Intake & Operation) =>
    request<FeedbackItem>(tenantPath(organisation), data),
};

export function pinOf({ id, checksum }: Pin): Pin {
  return { id, checksum };
}
