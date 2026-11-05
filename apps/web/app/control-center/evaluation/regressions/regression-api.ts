import { authenticatedFetch } from "../../../../lib/api";
import { PlatformApiError } from "../../control-api";
import type { EvaluationPage, EvaluationRun } from "../evaluation-api";

export type Pin = { id: string; checksum: string };
export type VersionKind = "cases" | "suites";
export type VersionAction = "submit" | "approve" | "reject" | "publish";

export type RegressionVersion = Pin & {
  dataset_id: string;
  code: string;
  version_number: number;
  status: string;
  origin: "DYNAMIC_REGRESSION_CASE" | "DYNAMIC_REGRESSION_SUITE";
  assessment_family: string;
  kind: string;
  split: string;
  purpose: string;
  evidence_maturity: string;
  case_count: number;
  required_checks: string[];
  case_pins: {
    case_id: string;
    case_version_id: string;
    checksum: string;
    required_checks: string[];
  }[];
  recent_comparisons?: EvaluationRun[];
  source_basis?:
    | "MEASURED_TECHNICAL_FAILURE"
    | "APPROVED_REFERENCE_OBSERVATION"
    | null;
  source?: {
    run_id: string;
    run_checksum: string;
    result_id: string;
    result_checksum: string;
    case_id: string;
  } | null;
  allowed_actions: VersionAction[];
  created_by?: string | null;
  approved_by?: string | null;
  published_by?: string | null;
};

export type ComparisonDimension =
  | "TECHNICAL_FIXTURE"
  | "EXECUTION_REPEAT"
  | "MODEL"
  | "PROMPT"
  | "RUBRIC"
  | "WORKFLOW"
  | "RETRIEVAL"
  | "TRANSCRIPTION";

type CheckMeasurement = {
  status: string;
  numerator: number | null;
  denominator: number | null;
  reason_code: string | null;
};

type MetricMeasurement = {
  value: number | null;
  numerator: number | string | null;
  denominator: number | string | null;
  sample_size: number;
  missing_count: number;
};

type ComparisonComponent = {
  run: Pin;
  dataset_version: Pin;
  configuration_checksum: string;
  case_count: number;
};

type CaseExecutionReceipt = {
  run: Pin;
  dataset_version: Pin;
  receipt_checksum: string;
  input_checksum: string;
  observation_checksum: string;
};

export type RegressionReport = {
  suite_version: Pin;
  baseline_run: Pin;
  candidate_run: Pin;
  baseline_components?: ComparisonComponent[];
  candidate_components?: ComparisonComponent[];
  baseline_dataset_version: Pin;
  candidate_dataset_version: Pin;
  dataset_split: string;
  dimension: ComparisonDimension;
  comparison_status: "COMPARABLE" | "NOT_COMPARABLE" | "NOT_EVALUATED";
  comparison_reason_code: string | null;
  technical_status: "PASS" | "FAIL" | "NOT_EVALUATED";
  empirical_status: "UNCONFIGURED" | "NOT_EVALUATED" | "PASS" | "FAIL";
  decision:
    | "PASS"
    | "FAIL"
    | "NOT_EVALUATED"
    | "NOT_COMPARABLE"
    | "UNCONFIGURED";
  expected_case_count: number;
  paired_case_count: number;
  missing_baseline_case_ids: string[];
  missing_candidate_case_ids: string[];
  case_findings: {
    case_id: string;
    case_version: Pin;
    baseline_result: Pin | null;
    candidate_result: Pin | null;
    baseline_execution?: CaseExecutionReceipt | null;
    candidate_execution?: CaseExecutionReceipt | null;
    baseline_status: string;
    candidate_status: string;
    baseline_failure_categories: string[];
    candidate_failure_categories: string[];
    checks: {
      code: string;
      baseline: CheckMeasurement | null;
      candidate: CheckMeasurement | null;
    }[];
  }[];
  paired_metrics: {
    name: string;
    baseline: MetricMeasurement | null;
    candidate: MetricMeasurement | null;
    delta: number | null;
    paired_case_count: number;
    missing_case_count: number;
    evidence_class: string;
  }[];
  changed_pins: {
    name: string;
    baseline: string | null;
    candidate: string | null;
  }[];
  baseline_artifacts: Record<string, string | null>;
  candidate_artifacts: Record<string, string | null>;
  empirical: { reason_code: string | null };
  reference_maturity: string;
  reference_quality_status: string;
  execution_class: string;
  limitations: string[];
  production_activation: false;
};

export type EvidenceDecision = "ACCEPTED" | "REJECTED" | "CHANGES_REQUIRED";
export type RegressionComparison = {
  run: EvaluationRun;
  regression: RegressionReport;
  reviews: {
    id: string;
    decision: EvidenceDecision;
    reviewer_user_id: string;
    created_at: string;
  }[];
  allowed_actions: "review"[];
  release_recommendation: "NOT_READY" | "ELIGIBLE_FOR_HUMAN_PUBLICATION_REVIEW";
  production_activation: false;
};

const summaryPrefix = "/api/v1/platform/control-center/evaluation";
const writePrefix = "/api/v1/platform/evaluation";

function exactId(id: string) {
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id)) {
    throw new Error("Enter an exact version or run UUID.");
  }
  return encodeURIComponent(id);
}

export function exactPin(id: string, checksum: string): Pin {
  exactId(id);
  if (!/^[a-f0-9]{64}$/.test(checksum)) {
    throw new Error("Enter the exact 64-character lowercase checksum.");
  }
  return { id, checksum };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (response.status === 409) {
    throw new Error(
      "This operation conflicts with the selected version or history. Reload its exact receipt before retrying.",
    );
  }
  if (!response.ok) throw new PlatformApiError(response.status);
  return (await response.json()) as T;
}

function post<T>(path: string, body: object) {
  return request<T>(`${writePrefix}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const regressionApi = {
  list: (kind: VersionKind, offset = 0) =>
    request<EvaluationPage<RegressionVersion>>(
      `${summaryPrefix}/regression-${kind}?${new URLSearchParams({ offset: String(offset), limit: "20" })}`,
    ),
  version: (kind: VersionKind, id: string) =>
    request<RegressionVersion>(
      `${summaryPrefix}/regression-${kind}/${exactId(id)}`,
    ),
  comparison: (id: string) =>
    request<RegressionComparison>(
      `${summaryPrefix}/regression-comparisons/${exactId(id)}`,
    ),
  promote: (body: {
    operation_id: string;
    source_run: Pin;
    source_result: Pin;
    failed_check: string;
  }) => post<RegressionVersion>("/regression-cases", body),
  createSuite: (body: {
    operation_id: string;
    code: string;
    case_pins: Pin[];
    predecessor: null;
  }) => post<RegressionVersion>("/regression-suites", body),
  transition: (
    id: string,
    action: VersionAction,
    body: { expected_checksum: string; review_reference?: string },
  ) =>
    post<RegressionVersion>(
      `/regression-versions/${exactId(id)}/${action}`,
      body,
    ),
  compare: (body: {
    operation_id: string;
    suite_version: Pin;
    baseline_run: Pin;
    candidate_run: Pin;
    baseline_additional_runs?: Pin[];
    candidate_additional_runs?: Pin[];
    dimension: ComparisonDimension;
    threshold_policy?: Pin;
  }) => post<RegressionComparison>("/regression-comparisons", body),
  review: (
    id: string,
    body: {
      operation_id: string;
      expected_checksum: string;
      decision: EvidenceDecision;
      review_reference: string;
    },
  ) =>
    post<{ id: string }>(`/regression-comparisons/${exactId(id)}/review`, body),
};
