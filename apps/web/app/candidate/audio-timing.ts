import type { AudioRecordingSession } from "./api";

export type RecordingClock = {
  sessionId: string;
  receivedAt: number;
  allowanceMs: number;
};

// This allowance exists only in the current document. Server-issued remaining
// time is reduced by the entire measured round trip, then by monotonic elapsed
// time. The browser's wall clock never determines recording expiry.
export function anchorRecordingClock(
  session: AudioRecordingSession,
  requestStartedAt: number,
  receivedAt: number,
): AudioRecordingSession {
  const maximum = session.policy?.max_recording_seconds * 1000;
  const remaining = session.recording_remaining_ms;
  const elapsed = receivedAt - requestStartedAt;
  const valid =
    Number.isFinite(Date.parse(session.server_now)) &&
    Number.isSafeInteger(maximum) &&
    maximum > 0 &&
    Number.isSafeInteger(remaining) &&
    remaining >= 0 &&
    remaining <= maximum &&
    Number.isFinite(requestStartedAt) &&
    Number.isFinite(receivedAt) &&
    elapsed >= 0;
  return {
    ...session,
    recording_clock: valid
      ? {
          sessionId: session.recording_session_id,
          receivedAt,
          allowanceMs: Math.max(0, Math.min(maximum, remaining) - elapsed),
        }
      : null,
  };
}

export function recordingRemainingMs(
  session: AudioRecordingSession,
  now: number,
): number | null {
  const clock = session.recording_clock;
  if (
    !clock ||
    clock.sessionId !== session.recording_session_id ||
    !Number.isFinite(now) ||
    now < clock.receivedAt
  )
    return null;
  return Math.max(0, clock.allowanceMs - (now - clock.receivedAt));
}

export function hasUnresolvedAudio(
  session: AudioRecordingSession | null,
): boolean {
  if (!session) return false;
  if (
    ["RECORDING", "UPLOADING", "STAGED", "UPLOADED", "VALIDATING"].includes(
      session.status,
    )
  )
    return true;
  return (
    session.status === "READY" &&
    session.asset?.attachment_status !== "ATTACHED"
  );
}
