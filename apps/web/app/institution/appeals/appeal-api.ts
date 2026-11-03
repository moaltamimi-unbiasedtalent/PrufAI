import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";

export type AppealSummary = {
  id: string;
  status: string;
  category: string;
  result_id: string;
  challenged_result_version_id: string;
  challenged_result_checksum: string;
  current_version_number: number;
  current_checksum: string;
};
export type AppealDetail = AppealSummary & {
  grounds?: string;
  additional_information?: string | null;
  decision?: string | null;
  decision_reason?: string | null;
  evidence_bundle_checksum?: string;
  correction?: {
    id: string;
    type: string;
    status: string;
    new_scoring_run_id: string | null;
    new_result_version_id: string | null;
  } | null;
};
export type AppealEvidenceItem = {
  binding_id: string;
  item_version_id: string;
  item_checksum: string;
  response_id: string | null;
  response_revision_id: string | null;
  response_revision_number: number | null;
  response_checksum: string | null;
  prompt: string;
  options: Array<{ option_id: string; display_text: string }>;
  response: { option_id?: string; option_ids?: string[] } | null;
  correct_option_ids: string[];
  scoring_policy: unknown;
  awarded_points: string | null;
  maximum_points: string;
  status: string;
  content_truncated?: boolean;
};
export type AppealEvidence = {
  appeal_id: string;
  result_id: string;
  result_version_id: string;
  result_checksum: string;
  scoring_run_id: string;
  scoring_run_checksum: string;
  exam_version_id: string;
  standard_pack_version_id: string;
  evidence_bundle_checksum: string;
  pinned_evidence_bundle_checksum: string | null;
  matches_pinned_bundle: boolean;
  total_items: number;
  limit: number;
  unsupported_items: number;
  supported_items?: number;
  truncated?: boolean;
  items: AppealEvidenceItem[];
};
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      /* Preserve HTTP status for outages. */
    }
    throw new AssessmentApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
export const appealApi = {
  list: (organisationId: string) =>
    request<AppealSummary[]>(`/api/v1/organisations/${organisationId}/appeals`),
  detail: (organisationId: string, appealId: string) =>
    request<AppealDetail>(
      `/api/v1/organisations/${organisationId}/appeals/${appealId}`,
    ),
  evidence: (organisationId: string, appealId: string) =>
    request<AppealEvidence>(
      `/api/v1/organisations/${organisationId}/appeals/${appealId}/evidence`,
    ),
  review: (organisationId: string, appealId: string) =>
    request<{ id: string; status: string }>(
      `/api/v1/organisations/${organisationId}/appeals/${appealId}/review`,
      { method: "POST" },
    ),
  reject: (
    organisationId: string,
    appeal: AppealDetail,
    reason: string,
    operationId: string,
  ) =>
    request<{ id: string; status: string }>(
      `/api/v1/organisations/${organisationId}/appeals/${appeal.id}/adjudicate`,
      {
        method: "POST",
        body: JSON.stringify({
          appeal_version_number: appeal.current_version_number,
          appeal_checksum: appeal.current_checksum,
          evidence_bundle_checksum: appeal.evidence_bundle_checksum,
          decision: "REJECT",
          reason,
          operation_id: operationId,
        }),
      },
    ),
  candidateList: () => request<AppealDetail[]>("/api/v1/candidate/appeals"),
  candidateDetail: (appealId: string) =>
    request<AppealDetail>(`/api/v1/candidate/appeals/${appealId}`),
  submit: (
    versionId: string,
    category: string,
    grounds: string,
    operationId: string,
  ) =>
    request<AppealDetail>(`/api/v1/candidate/results/${versionId}/appeals`, {
      method: "POST",
      body: JSON.stringify({ category, grounds, operation_id: operationId }),
    }),
};

export function canInspectAppealEvidence(permissions: string[]) {
  return [
    "appeal.read",
    "appeal.adjudicate",
    "result.read",
    "question_bank.read",
  ].every((permission) => permissions.includes(permission));
}

export function appealEvidenceComplete(
  appeal: AppealDetail,
  evidence: AppealEvidence | null,
): boolean {
  return Boolean(
    evidence &&
      evidence.appeal_id === appeal.id &&
      evidence.result_id === appeal.result_id &&
      evidence.result_version_id === appeal.challenged_result_version_id &&
      evidence.result_checksum === appeal.challenged_result_checksum &&
      evidence.pinned_evidence_bundle_checksum ===
        appeal.evidence_bundle_checksum &&
      evidence.matches_pinned_bundle &&
      evidence.evidence_bundle_checksum ===
        evidence.pinned_evidence_bundle_checksum &&
      evidence.unsupported_items === 0 &&
      !evidence.truncated &&
      evidence.items.every((item) => !item.content_truncated) &&
      evidence.total_items > 0 &&
      evidence.items.length === evidence.total_items,
  );
}

export function appealActions(appeal: AppealDetail, permissions: string[]) {
  return {
    review:
      appeal.status === "SUBMITTED" &&
      permissions.includes("appeal.adjudicate"),
    reject:
      appeal.status === "UNDER_REVIEW" &&
      permissions.includes("appeal.adjudicate") &&
      Boolean(appeal.evidence_bundle_checksum),
  };
}
