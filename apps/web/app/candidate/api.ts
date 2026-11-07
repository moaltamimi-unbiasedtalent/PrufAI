import type {
  LidPracticeAssignment,
  LidPracticeForm,
  LidPracticeResult,
} from "../../lib/lid-practice";
import { anchorRecordingClock, type RecordingClock } from "./audio-timing";

export type CandidateAssignment = {
  id: string;
  status: "ASSIGNED" | "AVAILABLE" | "COMPLETED" | "CANCELLED" | "EXPIRED";
  available_from: string | null;
  available_until: string | null;
  maximum_attempts: number;
  lid_practice?: LidPracticeAssignment | null;
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
  monotonic_deadline?: number | null;
  runtime_plan_checksum: string;
  runtime_plan: {
    duration_seconds: number | null;
    lid_practice?: LidPracticeForm | null;
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
            audio_policy?: AudioRecordingPolicy;
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
  server_now: string;
  recording_remaining_ms: number;
  upload_remaining_ms: number;
  recording_clock?: RecordingClock | null;
  attempt_number: number;
  policy: AudioRecordingPolicy;
  upload_expires_at: string;
  recoverable: boolean;
  last_error_category: string | null;
  asset: AudioMediaAsset | null;
};

export type AudioRecordingPolicy = {
  max_recording_seconds: number;
  max_recording_attempts: number;
  max_upload_bytes: number;
  supported_mime_types: string[];
  submission_behavior: "REQUIRE_ATTACHED";
  upload_grace_seconds: number;
};

export type AudioMediaAsset = {
  media_asset_id: string;
  status: string;
  sha256: string;
  byte_size: number;
  duration_ms: number;
  content_type: string;
  validation_provenance: Record<string, unknown>;
  saved_response_revision: number | null;
  candidate_response_revision_id: string | null;
  attachment_status: "UPLOADED_NOT_ATTACHED" | "ATTACHED";
};

export type AudioEvidence = {
  policy: AudioRecordingPolicy;
  recordings: AudioRecordingSession[];
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

async function recordingRequest(
  path: string,
  init?: RequestInit,
): Promise<AudioRecordingSession> {
  const started = performance.now();
  const value = await request<AudioRecordingSession>(path, init);
  return anchorRecordingClock(value, started, performance.now());
}

async function timedAttemptRequest<
  T extends { server_now: string; deadline_at: string | null },
>(path: string, init?: RequestInit) {
  const started = performance.now();
  const state = await request<T>(path, init);
  const remaining = state.deadline_at
    ? Date.parse(state.deadline_at) - Date.parse(state.server_now)
    : NaN;
  return {
    ...state,
    monotonic_deadline: Number.isFinite(remaining) ? started + remaining : null,
  };
}

async function uploadAudio(
  path: string,
  content: Blob,
  filename: string,
  signal?: AbortSignal,
): Promise<AudioMediaAsset> {
  const response = await authenticatedFetch(`/api/v1/candidate${path}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": content.type || "application/octet-stream",
      "X-Filename": filename,
    },
    body: content,
    signal,
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
  createAttempt: (assignmentId: string, bundesland?: string) =>
    request<CandidateAttempt>(`/exam-assignments/${assignmentId}/attempts`, {
      method: "POST",
      ...(bundesland ? { body: JSON.stringify({ bundesland }) } : {}),
    }),
  lidPracticeResult: (attemptId: string) =>
    request<LidPracticeResult>(
      `/exam-attempts/${attemptId}/lid-practice-result`,
    ),
  runtime: (attemptId: string) =>
    timedAttemptRequest<RuntimeState>(`/exam-attempts/${attemptId}/runtime`),
  start: (attemptId: string) =>
    timedAttemptRequest<RuntimeState>(`/exam-attempts/${attemptId}/start`, {
      method: "POST",
    }),
  startAudioRecording: (
    attemptId: string,
    bindingId: string,
    operationId: string,
    signal?: AbortSignal,
  ) =>
    recordingRequest(
      `/attempts/${attemptId}/items/${bindingId}/audio-recordings`,
      {
        method: "POST",
        body: JSON.stringify({ operation_id: operationId }),
        signal,
      },
    ),
  finalizeAudioRecording: (
    recordingSessionId: string,
    content: Blob,
    filename: string,
    signal?: AbortSignal,
  ) =>
    uploadAudio(
      `/audio-recordings/${recordingSessionId}/finalize`,
      content,
      filename,
      signal,
    ),
  audioEvidence: async (
    attemptId: string,
    bindingId: string,
    signal?: AbortSignal,
  ) => {
    const started = performance.now();
    const evidence = await request<AudioEvidence>(
      `/attempts/${attemptId}/items/${bindingId}/audio-evidence`,
      { signal },
    );
    const receivedAt = performance.now();
    return {
      ...evidence,
      recordings: (evidence.recordings ?? []).map((recording) =>
        anchorRecordingClock(recording, started, receivedAt),
      ),
    };
  },
  recoverAudioRecording: (sessionId: string, signal?: AbortSignal) =>
    recordingRequest(`/audio-recordings/${sessionId}/recover`, {
      method: "POST",
      signal,
    }),
  cancelAudioRecording: (sessionId: string, signal?: AbortSignal) =>
    recordingRequest(`/audio-recordings/${sessionId}/cancel`, {
      method: "POST",
      signal,
    }),
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
    timedAttemptRequest<CandidateAttempt & { server_now: string }>(
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
