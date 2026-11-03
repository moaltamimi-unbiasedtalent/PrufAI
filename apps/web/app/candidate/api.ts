export type CandidateAssignment = {
  id: string;
  status: "ASSIGNED" | "AVAILABLE" | "COMPLETED" | "CANCELLED" | "EXPIRED";
  available_from: string | null;
  available_until: string | null;
  maximum_attempts: number;
  attempts: Array<{ id: string; status: string; attempt_number: number }>;
};

export type CandidateAttempt = {
  id: string;
  status: string;
  attempt_number: number;
  started_at: string | null;
  deadline_at: string | null;
  submitted_at: string | null;
  expired_at: string | null;
  runtime_plan_checksum: string;
};

export type RuntimeState = {
  attempt_id: string;
  status: string;
  server_now: string;
  started_at: string | null;
  deadline_at: string | null;
  runtime_plan_checksum: string;
  runtime_plan: {
    duration_seconds: number | null;
    modules: Array<{
      module_id: string;
      sections: Array<{
        section_id: string;
        navigation_mode: string;
        tasks: Array<{
          task_id: string;
          response_mode: string;
          navigation_mode: string;
          items: Array<{
            binding_id: string;
            response_mode: string;
            content: Record<string, unknown>;
          }>;
        }>;
      }>;
    }>;
  };
  session: { current_binding_id: string | null };
  responses: Array<{
    binding_id: string;
    revision: number;
    payload: Record<string, unknown>;
  }>;
};

export type AudioRecordingSession = {
  recording_session_id: string;
  status: string;
  recording_started_at: string;
  recording_expires_at: string;
  attempt_number: number;
  policy: Record<string, unknown>;
};

export type AudioMediaAsset = {
  media_asset_id: string;
  status: string;
  sha256: string;
  byte_size: number;
};

export type CandidateRelease = {
  id: string;
  result_version_id: string;
  classification: string;
  released_at: string;
  status: string;
};

export class CandidateApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string) {
    super(code);
    this.name = "CandidateApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(`/api/v1/candidate${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let code = `RUNTIME.HTTP_${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      code = body.detail ?? code;
    } catch {
      // Preserve a normalized status when the server response is not JSON.
    }
    throw new CandidateApiError(response.status, code);
  }
  return (await response.json()) as T;
}

async function uploadAudio(
  path: string,
  content: Blob,
  filename: string,
): Promise<AudioMediaAsset> {
  const response = await authenticatedFetch(`/api/v1/candidate${path}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": (content.type || "application/octet-stream").split(
        ";",
        1,
      )[0],
      "X-Filename": filename,
    },
    body: content,
  });
  if (!response.ok) {
    let code = `RUNTIME.HTTP_${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      code = body.detail ?? code;
    } catch {
      // Preserve a normalized status when the response is not JSON.
    }
    throw new CandidateApiError(response.status, code);
  }
  return (await response.json()) as AudioMediaAsset;
}

export const candidateApi = {
  assignments: () => request<CandidateAssignment[]>("/exam-assignments"),
  attempt: (attemptId: string) =>
    request<CandidateAttempt>(`/exam-attempts/${attemptId}`),
  createAttempt: (assignmentId: string) =>
    request<CandidateAttempt>(`/exam-assignments/${assignmentId}/attempts`, {
      method: "POST",
    }),
  runtime: (attemptId: string) =>
    request<RuntimeState>(`/exam-attempts/${attemptId}/runtime`),
  start: (attemptId: string) =>
    request<RuntimeState>(`/exam-attempts/${attemptId}/start`, {
      method: "POST",
    }),
  startAudioRecording: (attemptId: string, bindingId: string) =>
    request<AudioRecordingSession>(
      `/attempts/${attemptId}/items/${bindingId}/audio-recordings`,
      { method: "POST" },
    ),
  finalizeAudioRecording: (
    recordingSessionId: string,
    content: Blob,
    filename: string,
  ) =>
    uploadAudio(
      `/audio-recordings/${recordingSessionId}/finalize`,
      content,
      filename,
    ),
  save: (
    attemptId: string,
    bindingId: string,
    payload: Record<string, unknown>,
    expectedRevision: number,
    operationId: string,
  ) =>
    request<{
      response_id: string;
      revision: number;
      payload: Record<string, unknown>;
    }>(`/exam-attempts/${attemptId}/responses/${bindingId}`, {
      method: "PUT",
      body: JSON.stringify({
        payload,
        expected_revision: expectedRevision,
        client_operation_id: operationId,
      }),
    }),
  navigate: (attemptId: string, bindingId: string) =>
    request<{ current_binding_id: string }>(
      `/exam-attempts/${attemptId}/navigate?binding_id=${encodeURIComponent(bindingId)}`,
      { method: "POST" },
    ),
  heartbeat: (attemptId: string) =>
    request<CandidateAttempt & { server_now: string }>(
      `/exam-attempts/${attemptId}/heartbeat`,
      {
        method: "POST",
      },
    ),
  submit: (attemptId: string) =>
    request<CandidateAttempt>(`/exam-attempts/${attemptId}/submit`, {
      method: "POST",
    }),
  results: () => request<Array<Record<string, unknown>>>("/results"),
  releases: () => request<CandidateRelease[]>("/releases"),
  result: (resultId: string) =>
    request<Record<string, unknown>>(`/results/${resultId}`),
};
import { authenticatedFetch } from "../../lib/api";
