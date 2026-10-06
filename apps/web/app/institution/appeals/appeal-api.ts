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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(`APPEAL_HTTP_${response.status}`);
  return (await response.json()) as T;
}

export const appealApi = {
  list: (organisationId: string) =>
    request<AppealSummary[]>(`/api/v1/organisations/${organisationId}/appeals`),
  detail: (organisationId: string, appealId: string) =>
    request<AppealDetail>(
      `/api/v1/organisations/${organisationId}/appeals/${appealId}`,
    ),
  review: (organisationId: string, appealId: string) =>
    request(
      `/api/v1/organisations/${organisationId}/appeals/${appealId}/review`,
      {
        method: "POST",
      },
    ),
};
import { authenticatedFetch } from "../../../lib/api";
