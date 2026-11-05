import type { EvaluationPage } from "../../evaluation/evaluation-api";
import type { RegressionComparison } from "../../evaluation/regressions/regression-api";
import {
  feedbackReadPrefix,
  feedbackRequest,
  feedbackWritePrefix,
  type Pin,
  type Proposal,
} from "../feedback-api";

type PromptPin = Pin & {
  type: "PROMPT_VERSION";
  version_label: string;
};
type CandidateInput = {
  proposal: Pin;
  approval: Pin;
  target: Pin & { type: "PROMPT_VERSION" };
  suite_version: Pin;
  baseline_run: Pin;
};
export type ReadinessDecision =
  | "APPROVE_FOR_NONPRODUCTION_VALIDATION"
  | "NOT_READY";

export type ChangeCandidate = Pin &
  CandidateInput & {
    target: PromptPin;
    draft: PromptPin;
    pattern: Pin;
    source_feedback: Pin[];
    change_category: string;
    change_code: "CLEAR_LEGACY_INSTRUCTIONS";
    risk: "ASSESSMENT_AFFECTING";
    evaluation_requirements: Proposal["evaluation_requirements"];
    status: string;
    receipt: Pin;
    allowed_actions: ("EVALUATE" | "REVIEW")[];
    proposal_authority_current?: boolean | null;
    production_readiness: "NOT_READY";
    production_activation: false;
    evaluation: null | {
      baseline_run: Pin;
      candidate_run: Pin;
      comparison_run: Pin;
      comparison_status: string;
      technical_status: string;
      empirical_status: string;
      case_differences: {
        case_id: string;
        new_failures: string[];
        resolved_failures: string[];
        unchanged_failures: string[];
      }[];
      missing_case_count: number;
    };
    comparison: RegressionComparison | null;
    history: (Pin & {
      revision: number;
      action: string;
      status: string;
      actor_id: string;
      created_at: string;
      decision?: ReadinessDecision | null;
      rationale?: string | null;
    })[];
  };

type Operation = { operation_id: string };
type Mutation = Operation & { expected_checksum: string };
type MutationReceipt = Pin & { status: string };
const read = `${feedbackReadPrefix}/change-candidates`;
const write = `${feedbackWritePrefix}/change-candidates`;

export const candidateApi = {
  list: (offset: number) =>
    feedbackRequest<EvaluationPage<ChangeCandidate>>(
      `${read}?${new URLSearchParams({ offset: String(offset), limit: "20" })}`,
    ),
  detail: (id: string) =>
    feedbackRequest<ChangeCandidate>(`${read}/${encodeURIComponent(id)}`),
  create: (data: CandidateInput & Operation) =>
    feedbackRequest<ChangeCandidate>(write, data),
  evaluate: (id: string, data: Mutation) =>
    feedbackRequest<MutationReceipt>(
      `${write}/${encodeURIComponent(id)}/evaluate`,
      data,
    ),
  review: (
    id: string,
    data: Mutation & { decision: ReadinessDecision; rationale: string },
  ) =>
    feedbackRequest<MutationReceipt>(
      `${write}/${encodeURIComponent(id)}/review`,
      data,
    ),
};
