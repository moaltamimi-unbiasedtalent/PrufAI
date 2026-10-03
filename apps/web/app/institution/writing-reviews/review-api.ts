export type WritingReview = {
  id: string;
  version_id: string;
  status: string;
  checksum: string;
  candidate_text: string;
  rubric: {
    id: string;
    checksum: string;
    criteria: Array<Record<string, unknown>>;
  } | null;
  criteria: Array<Record<string, unknown>>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(`REVIEW_HTTP_${response.status}`);
  return (await response.json()) as T;
}

export const writingReviewApi = {
  detail: (organisationId: string, assessmentId: string) =>
    request<WritingReview>(
      `/api/v1/organisations/${organisationId}/subjective-assessments/${assessmentId}`,
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
