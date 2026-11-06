import { authenticatedFetch } from "../../lib/api";

const base = "/api/v1/scoped-source-review";

export type ReviewGrant = {
  grant_id: string;
  organisation_id: string;
  starts_at: string;
  expires_at: string;
};

export type ReviewDecision = "METADATA_ONLY" | "DENY";

export type ReviewSource = {
  version_id: string;
  metadata_checksum: string;
  metadata: {
    title?: string | null;
    publisher?: string | null;
    version_label?: string | null;
    publication_date?: string | null;
    effective_from?: string | null;
    effective_until?: string | null;
    rights_status?: string | null;
  };
  allowed_decisions: ReviewDecision[];
  decision_checksum?: string;
};

export type ReviewSubmission = {
  expected_metadata_checksum: string;
  decision: ReviewDecision;
  basis_reference: string;
  rights_holder: string;
  licence_type: string;
  retention_policy: string;
};

export class ReviewRequestError extends Error {
  constructor(readonly status: number) {
    super(
      status === 403 || status === 404
        ? "This source-review access is unavailable."
        : status === 409
          ? "The source or review state changed. Reload and inspect the exact version again."
          : status === 422
            ? "Check the required review fields and try again."
            : "The source-review request could not be completed.",
    );
  }
}

async function reviewRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) throw new ReviewRequestError(response.status);
  return (await response.json()) as T;
}

export function getReviewGrants(signal: AbortSignal) {
  return reviewRequest<ReviewGrant[]>(`${base}/grants`, { signal });
}

function sourcesPath(grant: ReviewGrant) {
  const organisation = encodeURIComponent(grant.organisation_id);
  const id = encodeURIComponent(grant.grant_id);
  return `${base}/orgs/${organisation}/grants/${id}/sources`;
}

export function getReviewSources(grant: ReviewGrant, signal: AbortSignal) {
  return reviewRequest<ReviewGrant & { sources: ReviewSource[] }>(
    sourcesPath(grant),
    { signal },
  );
}

export function inspectReviewSource(grant: ReviewGrant, versionId: string) {
  return reviewRequest<ReviewSource>(
    `${sourcesPath(grant)}/${encodeURIComponent(versionId)}`,
  );
}

export function submitReviewDecision(
  grant: ReviewGrant,
  versionId: string,
  submission: ReviewSubmission & { operation_id: string },
) {
  return reviewRequest<ReviewSource>(
    `${sourcesPath(grant)}/${encodeURIComponent(versionId)}/decision`,
    { method: "POST", body: JSON.stringify(submission) },
  );
}
