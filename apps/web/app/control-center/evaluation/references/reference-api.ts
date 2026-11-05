import { authenticatedFetch } from "../../../../lib/api";
import { PlatformApiError } from "../../control-api";
import type { EvaluationPage } from "../evaluation-api";
import type {
  RubricCriterion,
  RubricLevel,
} from "../../../institution/writing-reviews/governance-ui";

export type ReferenceSummary = {
  id: string;
  dataset_id: string;
  checksum: string;
  kind: string;
  split: "TRAIN" | "CALIBRATION" | "VALIDATION" | "HELD_OUT";
  evidence_class: "SYNTHETIC_TEST" | "APPROVED_REFERENCE";
  status: "PENDING_APPROVAL" | "APPROVED";
  judgment_count: number;
  required_judgment_count: number;
  adjudication_count: number;
  adjudication_status: "PENDING" | "ADJUDICATED";
  dataset_version_ids: string[];
  empirical_thresholds: "UNCONFIGURED";
  production_activation: false;
  limitations?: string[];
};
export type ReferenceSource = {
  code?: string;
  origin: string;
  language: string;
  purpose: string;
  retention_classification: string;
  standard_pack_version_id: string;
  standard_pack_checksum: string;
  rubric_version_id: string | null;
  rubric_checksum: string | null;
  normalization: string;
  input: {
    text?: string | null;
    audio_sha256?: string | null;
    duration_ms?: number | null;
    evidence_identity?: Record<string, string>;
  };
};
export type ReferenceCriterion = {
  criterion_code: string;
  points: number;
  level: string;
  rationale: string;
  evidence: { quote: string; start: number; end: number }[];
};
export type ReferenceJudgment = {
  id: string;
  checksum: string;
  examiner_id: string;
  created_at: string;
  body: { criteria: ReferenceCriterion[] };
};
export type ReferenceWorkbench = {
  reference: ReferenceSummary;
  source: ReferenceSource | null;
  rubric: {
    criteria: RubricCriterion[];
    performance_levels: RubricLevel[];
    aggregation_policy?: Record<string, unknown>;
    evidence_policy?: Record<string, unknown>;
  } | null;
  qualifications: { id: string; checksum: string }[];
  judgments: ReferenceJudgment[];
  adjudications: {
    id: string;
    checksum: string;
    version_number: number;
    body: { rationale?: string; criteria?: ReferenceCriterion[] };
  }[];
  allowed_actions: string[];
};

const prefix = "/api/v1/platform/evaluation/references";
function exactId(id: string) {
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
  )
    throw new Error("An exact reference identity is required.");
  return encodeURIComponent(id);
}
async function request<T>(path: string, init: RequestInit = {}) {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    if (response.status === 409)
      throw new Error(
        "The reference changed or this operation conflicts with immutable history. Reload the exact reference before retrying.",
      );
    throw new PlatformApiError(response.status);
  }
  return (await response.json()) as T;
}

export const referenceApi = {
  summaries: (offset = 0) =>
    request<EvaluationPage<ReferenceSummary>>(
      `/api/v1/platform/control-center/evaluation/references?${new URLSearchParams({ offset: String(offset), limit: "20" })}`,
    ),
  workbench: (id: string) =>
    request<ReferenceWorkbench>(`${prefix}/${exactId(id)}/workbench`),
  act: (
    id: string,
    action: "approve" | "judgments" | "adjudications" | "dataset-versions",
    body: unknown,
  ) =>
    request<{ id: string; checksum: string }>(
      `${prefix}/${exactId(id)}/${action}`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
};
