import { authenticatedFetch } from "../../../lib/api";
import { PlatformApiError } from "../control-api";
import type { RegressionReport } from "./regressions/regression-api";

export type EvaluationEvidenceClass =
  | "SYNTHETIC_TEST"
  | "TECHNICAL_TEST"
  | "SYNTHETIC_REGRESSION"
  | "EXPERT_LABELLED"
  | "REAL_PROVIDER"
  | "PILOT_CALIBRATED";

export type EvaluationPage<T> = {
  items: T[];
  total: number;
  offset: number;
  limit: number;
};

export type EvaluationDataset = {
  id: string;
  dataset_id: string;
  code: string;
  version_number: number;
  status: "DRAFT" | "REVIEW_REQUIRED" | "APPROVED" | "PUBLISHED";
  assessment_family: string;
  language: string;
  cefr_level: string | null;
  skill: string;
  evidence_class: EvaluationEvidenceClass;
  checksum: string;
  case_count: number;
  approval_status: string;
  created_at: string;
  approved_at: string | null;
  published_at: string | null;
};

export type EvaluationRun = {
  id: string;
  checksum?: string;
  dataset_version_id: string;
  dataset_checksum: string;
  status: "COMPLETED" | "FAILED";
  hard_gate: "PASS" | "FAIL";
  execution_class:
    | "TEST_ONLY"
    | "REAL_PROVIDER"
    | "HUMAN_REFERENCE"
    | "NOT_RUN";
  evidence_class: EvaluationEvidenceClass;
  case_count: number;
  failure_count: number;
  created_at: string;
  completed_at: string;
  review_status: "PENDING" | "ACCEPTED" | "REJECTED" | "CHANGES_REQUIRED";
};

export type EvaluationPins = {
  dataset_version_id: string;
  dataset_checksum: string;
  dataset_split: "TRAIN" | "CALIBRATION" | "TEST" | "VALIDATION" | "HELD_OUT";
  standard_pack_version_id: string;
  standard_pack_checksum: string;
  rubric_version_id: string | null;
  rubric_checksum: string | null;
  question_bank_item_version_id: string | null;
  item_checksum: string | null;
  model_profile_id: string | null;
  model_version: string | null;
  prompt_version_id: string | null;
  agent_version_id: string | null;
  workflow_version_id: string | null;
  profile_version: string | null;
  execution_adapter: string;
  evaluation_method: string;
  evaluation_method_version: string;
  metric_schema_version: "1";
  metric_normalization: "NFC_WHITESPACE" | "NFC_WHITESPACE_CASEFOLD";
  metric_retrieval_k: number;
  metric_configuration_checksum: string;
};

export type EvaluationMetric = {
  name: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  sample_size: number;
  missing_count: number;
  evidence_class: EvaluationEvidenceClass;
  unavailable_reason: string | null;
  limitations: string[];
};

export type EvaluationRunDetail = {
  run: EvaluationRun;
  pins: EvaluationPins;
  metrics: EvaluationMetric[];
  failures: {
    case_id: string;
    failure_categories: string[];
    result_id?: string;
    result_checksum?: string;
    failed_checks?: string[];
  }[];
  reviews: {
    id: string;
    decision: string;
    reviewer_user_id: string;
    created_at: string;
  }[];
  limitations: string[];
  regression?: RegressionReport | null;
  comparison?: {
    comparison_kind: string;
    left: {
      kind: string;
      version_id: string;
      checksum: string;
      execution_class: string;
    };
    right: {
      kind: string;
      version_id: string;
      checksum: string;
      execution_class: string;
    };
    empirical_thresholds: string;
    quality_claim: string;
    uncertainty: Record<string, unknown>;
    criteria: {
      criterion_code: string;
      expected_count: number;
      sample_size: number;
      disagreement_distribution: {
        difference: string;
        absolute_difference: string;
        count: number;
        rate: number;
      }[];
    }[];
    limitations: string[];
    production_activation: false;
  };
};

const prefix = "/api/v1/platform/control-center/evaluation";

async function read<T>(path: string): Promise<T> {
  const response = await authenticatedFetch(`${prefix}${path}`);
  if (!response.ok) throw new PlatformApiError(response.status);
  return (await response.json()) as T;
}

function query(kind: "datasets" | "runs", offset: number) {
  return `?${new URLSearchParams({ kind, offset: String(offset), limit: "20" })}`;
}

export const evaluationApi = {
  datasets: (offset = 0) =>
    read<EvaluationPage<EvaluationDataset>>(query("datasets", offset)),
  runs: (offset = 0) =>
    read<EvaluationPage<EvaluationRun>>(query("runs", offset)),
  run: (id: string) => {
    if (
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        id,
      )
    )
      throw new Error("An exact evaluation run reference is required.");
    return read<EvaluationRunDetail>(`/runs/${encodeURIComponent(id)}`);
  },
};
