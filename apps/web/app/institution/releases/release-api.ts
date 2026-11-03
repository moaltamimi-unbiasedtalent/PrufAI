import { authenticatedFetch } from "../../../lib/api";
import type { AssessmentComponent } from "../assessment-evidence";

export type ResultRelease = {
  id: string;
  candidate_user_id?: string;
  result_version_id: string;
  classification: string;
  status: string;
  effective_status?: string;
  released_at: string;
  attempt_id?: string;
  assessment_result_id?: string;
  checksum?: string;
  supersedes_release_id?: string | null;
  released_by_user_id?: string;
};

export type ReleaseArtifact = {
  id: string;
  status: string;
  effective_status?: string;
  currently_valid?: boolean;
  checksum?: string;
  serial?: string;
  issued_at?: string;
  payload?: Record<string, unknown> & {
    assessment_components?: AssessmentComponent[];
  };
};

export type ReleaseDetail = ResultRelease & {
  assessment_result_id?: string;
  attempt_id?: string;
  scoring_run_id?: string;
  standard_pack_version_id?: string;
  result_checksum?: string;
  statements?: ReleaseArtifact[];
  certificates?: ReleaseArtifact[];
  result?: ReleasedScore;
};

export type ReleasedScore = {
  result_version_id: string;
  raw_awarded_points: string | null;
  raw_maximum_points: string | null;
  outcome: string | null;
  result_checksum: string;
  assessment_components?: AssessmentComponent[];
};

export type ReleaseQueueItem = {
  assignment_id: string;
  candidate_name: string | null;
  exam_name: string | null;
  attempt_id: string;
  result_id: string;
  version_id: string;
  status: string;
  checksum: string;
};

type AssignmentInspection = {
  id: string;
  candidate_name: string | null;
  exam_name: string | null;
  attempts: Array<{
    id: string;
    result: null | {
      id: string;
      version_id: string;
      status: string;
      checksum: string;
    };
  }>;
};

export class ReleaseApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = "ReleaseApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    let detail = `The release request failed (HTTP ${response.status}).`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* Retain a useful status when the proxy response is not JSON. */
    }
    throw new ReleaseApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const releaseApi = {
  list: (organisationId: string) =>
    request<ResultRelease[]>(
      `/api/v1/organisations/${organisationId}/releases`,
    ),
  detail: (organisationId: string, releaseId: string) =>
    request<ReleaseDetail>(
      `/api/v1/organisations/${organisationId}/releases/${releaseId}`,
    ),
  queue: async (organisationId: string): Promise<ReleaseQueueItem[]> => {
    const assignments = await request<AssignmentInspection[]>(
      `/api/v1/organisations/${organisationId}/exam-assignments`,
    );
    return assignments.flatMap((assignment) =>
      assignment.attempts.flatMap((attempt) =>
        attempt.result
          ? [
              {
                assignment_id: assignment.id,
                candidate_name: assignment.candidate_name,
                exam_name: assignment.exam_name,
                attempt_id: attempt.id,
                result_id: attempt.result.id,
                version_id: attempt.result.version_id,
                status: attempt.result.status,
                checksum: attempt.result.checksum,
              },
            ]
          : [],
      ),
    );
  },
  release: (
    organisationId: string,
    versionId: string,
    operationId: string,
    supersedesReleaseId?: string,
  ) =>
    request<{ id: string; release_id: string; status: string }>(
      `/api/v1/organisations/${organisationId}/result-versions/${versionId}/release`,
      {
        method: "POST",
        body: JSON.stringify({
          operation_id: operationId,
          ...(supersedesReleaseId
            ? { supersedes_release_id: supersedesReleaseId }
            : {}),
        }),
      },
    ),
  issueStatement: (
    organisationId: string,
    releaseId: string,
    operationId: string,
  ) =>
    request<ReleaseArtifact>(
      `/api/v1/organisations/${organisationId}/releases/${releaseId}/statement`,
      { method: "POST", body: JSON.stringify({ operation_id: operationId }) },
    ),
  revoke: (
    organisationId: string,
    releaseId: string,
    reason: string,
    operationId: string,
  ) =>
    request<{ id: string; status: string }>(
      `/api/v1/organisations/${organisationId}/releases/${releaseId}/revoke`,
      {
        method: "POST",
        body: JSON.stringify({ reason, operation_id: operationId }),
      },
    ),
  candidateList: () => request<ResultRelease[]>("/api/v1/candidate/releases"),
  candidateDetail: (releaseId: string) =>
    request<ReleaseDetail>(`/api/v1/candidate/releases/${releaseId}`),
  candidateStatement: (releaseId: string) =>
    request<ReleaseArtifact & { classification: string }>(
      `/api/v1/candidate/releases/${releaseId}/statement`,
    ),
};

export function exactReleasedScore(
  release: ReleaseDetail,
): ReleasedScore | null {
  return release.result?.result_version_id === release.result_version_id &&
    (!release.result_checksum ||
      release.result.result_checksum === release.result_checksum)
    ? release.result
    : null;
}

export function statementMatchesRelease(
  statement: ReleaseArtifact,
  release: ReleaseDetail,
): boolean {
  const payload = statement.payload;
  if (
    !payload ||
    payload.result_release_id !== release.id ||
    payload.result_version_id !== release.result_version_id
  )
    return false;
  return [
    ["scoring_run_id", release.scoring_run_id],
    ["standard_pack_version_id", release.standard_pack_version_id],
    ["attempt_id", release.attempt_id],
    ["candidate_user_id", release.candidate_user_id],
    ["release_checksum", release.checksum],
    ["result_checksum", release.result_checksum],
  ].every(([key, expected]) => !expected || payload[key!] === expected);
}

export function statementProvenanceFields(statement: ReleaseArtifact) {
  const fields = [
    "statement_type",
    "classification",
    "raw_awarded_points",
    "raw_maximum_points",
    "outcome",
    "result_release_id",
    "result_version_id",
    "scoring_run_id",
    "standard_pack_version_id",
    "candidate_user_id",
    "organisation_id",
    "attempt_id",
    "release_checksum",
    "result_checksum",
  ];
  return fields.flatMap((name) => {
    const value = statement.payload?.[name];
    return typeof value === "string" || typeof value === "number"
      ? [{ label: name.replaceAll("_", " "), value: String(value) }]
      : [];
  });
}

export function releaseStateMessage(status: string): string {
  if (status === "RELEASED")
    return "Current release. Artifact availability is confirmed by the server.";
  if (status === "REVOKED")
    return "Revoked release. Its historical records are retained; it cannot issue new valid artifacts.";
  if (status === "SUPERSEDED")
    return "Superseded release. A successor carries the current result; this record remains historical.";
  return "This release is not eligible for artifact issuance.";
}

export function releaseActions(status: string, permissions: string[]) {
  return {
    issueStatement:
      status === "RELEASED" && permissions.includes("result.release"),
    revoke: status === "RELEASED" && permissions.includes("result.manage"),
  };
}
