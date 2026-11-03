"use client";

import React, { useEffect, useState } from "react";
import {
  CandidateApiError,
  candidateApi,
  type RuntimeState as RuntimeDto,
} from "../../api";

export function applySavedResponse(
  runtime: RuntimeDto,
  bindingId: string,
  saved: { revision: number; payload: Record<string, unknown> },
): RuntimeDto {
  const response = {
    binding_id: bindingId,
    revision: saved.revision,
    payload: saved.payload,
  };
  const exists = runtime.responses.some(
    (item) => item.binding_id === bindingId,
  );
  return {
    ...runtime,
    responses: exists
      ? runtime.responses.map((item) =>
          item.binding_id === bindingId ? response : item,
        )
      : [...runtime.responses, response],
  };
}

export type ResponseMode =
  | "SELECT_ONE"
  | "SELECT_MULTIPLE"
  | "TEXT"
  | "LONG_TEXT"
  | "AUDIO";
export type RuntimeState =
  | "Saved"
  | "Saving"
  | "Save failed"
  | "Offline — queued"
  | "Conflict"
  | "Expired"
  | "Submitted";

const visuallyHidden: React.CSSProperties = {
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: 0,
  margin: "-1px",
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

export function formatRemaining(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

export function runtimeStateMessage(status: RuntimeState): string | null {
  if (status === "Save failed") {
    return "The server has not confirmed this response. Keep the page open and retry before submitting.";
  }
  if (status === "Conflict") {
    return "A newer response exists on the server. Your change was not applied. Reload the latest response before continuing.";
  }
  if (status === "Expired") {
    return "This attempt is no longer writable. Any unsynced local response was not accepted by the server.";
  }
  return null;
}

export function RuntimeTimer({ deadlineAt }: { deadlineAt: number | null }) {
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const [milestone, setMilestone] = useState<string | null>(null);

  useEffect(() => {
    if (deadlineAt === null) {
      setRemainingSeconds(null);
      return;
    }
    const update = () =>
      setRemainingSeconds(
        Math.max(0, Math.ceil((deadlineAt - Date.now()) / 1000)),
      );
    update();
    const interval = window.setInterval(update, 1000);
    return () => window.clearInterval(interval);
  }, [deadlineAt]);

  useEffect(() => {
    if (remainingSeconds === null) return;
    const thresholds: Record<number, string> = {
      600: "10 minutes remaining",
      300: "5 minutes remaining",
      60: "1 minute remaining",
    };
    const threshold = Object.keys(thresholds)
      .map(Number)
      .find((value) => remainingSeconds === value);
    if (threshold !== undefined) setMilestone(thresholds[threshold]);
  }, [remainingSeconds]);

  const label =
    remainingSeconds === null
      ? "Remaining time: waiting for the server deadline"
      : `Remaining time: ${formatRemaining(remainingSeconds)}`;

  return (
    <section aria-labelledby="time-heading">
      <h2 id="time-heading">Time remaining</h2>
      <p>
        <time
          aria-label={label}
          dateTime={
            remainingSeconds === null ? undefined : `PT${remainingSeconds}S`
          }
        >
          {remainingSeconds === null
            ? "Waiting for server deadline"
            : formatRemaining(remainingSeconds)}
        </time>
      </p>
      <p style={visuallyHidden} aria-live="polite">
        {milestone}
      </p>
      <p>
        Timing is controlled by the server. This display is only a local
        estimate.
      </p>
    </section>
  );
}

type AudioResponseControlProps = {
  attemptId: string;
  bindingId: string;
  editable: boolean;
  currentMediaAssetId?: string;
  onSaved: (mediaAssetId: string) => Promise<void>;
};

export function AudioResponseControl({
  attemptId,
  bindingId,
  editable,
  currentMediaAssetId,
  onSaved,
}: AudioResponseControlProps) {
  const [permission, setPermission] = useState<"unknown" | "ready" | "denied">(
    "unknown",
  );
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [deadlineAt, setDeadlineAt] = useState<number | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const recorderRef = React.useRef<MediaRecorder | null>(null);
  const recordingSessionIdRef = React.useRef<string | null>(null);
  const streamRef = React.useRef<MediaStream | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);

  useEffect(() => {
    if (deadlineAt === null) {
      setRemaining(null);
      return;
    }
    const update = () =>
      setRemaining(Math.max(0, Math.ceil((deadlineAt - Date.now()) / 1000)));
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [deadlineAt]);

  useEffect(
    () => () => streamRef.current?.getTracks().forEach((track) => track.stop()),
    [],
  );

  async function preflight() {
    setMessage(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
      setPermission("ready");
      setMessage(
        "Microphone ready. Recording will not begin until you choose Start recording.",
      );
    } catch {
      setPermission("denied");
      setMessage(
        "Microphone access is required for this response and was not granted.",
      );
    }
  }

  async function startRecording() {
    if (!editable || permission !== "ready" || recording) return;
    setBusy(true);
    setMessage(null);
    try {
      const session = await candidateApi.startAudioRecording(
        attemptId,
        bindingId,
      );
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) =>
        event.data.size && chunksRef.current.push(event.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      };
      streamRef.current = stream;
      recorderRef.current = recorder;
      recordingSessionIdRef.current = session.recording_session_id;
      setDeadlineAt(Date.parse(session.recording_expires_at));
      recorder.start();
      setRecording(true);
    } catch (cause) {
      setMessage(
        cause instanceof CandidateApiError
          ? cause.message
          : "Recording could not be started.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function stopRecording() {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    setBusy(true);
    setRecording(false);
    const sessionId = recordingSessionIdRef.current;
    recorder.onstop = async () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      if (!sessionId) {
        setMessage(
          "Recording session metadata was unavailable; audio was not submitted.",
        );
        setBusy(false);
        return;
      }
      try {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        const asset = await candidateApi.finalizeAudioRecording(
          sessionId,
          blob,
          "candidate-audio.webm",
        );
        await onSaved(asset.media_asset_id);
        setMessage("Audio saved to this response.");
      } catch (cause) {
        setMessage(
          cause instanceof CandidateApiError
            ? cause.message
            : "Audio could not be uploaded.",
        );
      } finally {
        recorderRef.current = null;
        recordingSessionIdRef.current = null;
        setDeadlineAt(null);
        setBusy(false);
      }
    };
    recorder.stop();
  }

  return (
    <section aria-labelledby="audio-response-heading">
      <h3 id="audio-response-heading">Recorded response</h3>
      <p id="audio-response-help">
        Your microphone is checked first. Recording starts only after you choose
        the button.
      </p>
      {currentMediaAssetId && (
        <>
          <p role="status">
            An audio response is already saved. A new recording would be a new
            governed attempt.
          </p>
          <p>
            Audio playback is not available in this candidate walkthrough. The
            saved evidence remains on the server.
          </p>
        </>
      )}
      <p role="status" aria-live="polite">
        {message}
      </p>
      {remaining !== null && (
        <p>Server recording time remaining: {formatRemaining(remaining)}</p>
      )}
      <button
        type="button"
        onClick={() => void preflight()}
        disabled={!editable || busy || recording}
        aria-describedby="audio-response-help"
      >
        Check microphone
      </button>{" "}
      <button
        type="button"
        onClick={() => void startRecording()}
        disabled={!editable || busy || recording || permission !== "ready"}
      >
        Start recording
      </button>{" "}
      <button
        type="button"
        onClick={() => void stopRecording()}
        disabled={!recording || busy}
      >
        Stop and save recording
      </button>
      <p>
        Microphone status:{" "}
        {permission === "ready"
          ? "ready"
          : permission === "denied"
            ? "not granted"
            : "not checked"}
      </p>
    </section>
  );
}
