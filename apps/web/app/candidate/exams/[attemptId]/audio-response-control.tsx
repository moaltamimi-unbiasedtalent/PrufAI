"use client";

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAuth } from "../../../../lib/auth";
import { PrivateAudioPlayer } from "../../../private-audio-player";
import {
  candidateApi,
  type AudioMediaAsset,
  type AudioRecordingSession,
} from "../../api";
import {
  audioAttachmentMessage,
  recordingFilename,
  recordingPolicy,
  selectRecordingMime,
  type AudioAttachmentAck,
} from "../../audio-contract";

type Phase =
  | "IDLE"
  | "PREPARING"
  | "RECORDING"
  | "UPLOADING"
  | "UPLOADED_NOT_ATTACHED"
  | "ATTACHING"
  | "ATTACHED"
  | "QUEUED_RESPONSE"
  | "CONFLICT"
  | "FAILED"
  | "UNFINISHED";
type Props = {
  attemptId: string;
  bindingId: string;
  editable: boolean;
  policy: unknown;
  currentMediaAssetId?: string;
  onSaved: (
    assetId: string,
    operationId: string,
  ) => Promise<AudioAttachmentAck>;
  onAttachmentConfirmed: (assetId: string, revision: number) => void;
  onActivityChange: (active: boolean) => void;
};

export function AudioResponseControl({
  attemptId,
  bindingId,
  editable,
  policy: rawPolicy,
  currentMediaAssetId,
  onSaved,
  onAttachmentConfirmed,
  onActivityChange,
}: Props) {
  const { user, organisationId } = useAuth();
  const context =
    user && organisationId
      ? `${user.id}:${organisationId}:${attemptId}:${bindingId}`
      : null;
  const contextRef = useRef(context);
  contextRef.current = context;
  const policy = useMemo(() => recordingPolicy(rawPolicy), [rawPolicy]);
  const [metadataLoading, setMetadataLoading] = useState(true);
  const [phase, setPhase] = useState<Phase>("IDLE");
  const [permission, setPermission] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [session, setSession] = useState<AudioRecordingSession | null>(null);
  const [asset, setAsset] = useState<AudioMediaAsset | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [refresh, setRefresh] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const retained = useRef<Blob | null>(null);
  const sessionRef = useRef<AudioRecordingSession | null>(null);
  const startOperation = useRef<string | null>(null);
  const attachmentOperation = useRef<Record<string, string>>({});
  const confirmed = useRef(onAttachmentConfirmed);
  confirmed.current = onAttachmentConfirmed;
  const active =
    Boolean(context && policy && metadataLoading) ||
    [
      "PREPARING",
      "RECORDING",
      "UPLOADING",
      "ATTACHING",
      "UPLOADED_NOT_ATTACHED",
      "QUEUED_RESPONSE",
      "UNFINISHED",
      "CONFLICT",
    ].includes(phase) ||
    (phase === "FAILED" &&
      Boolean(retained.current || asset || session?.status === "RECORDING"));
  const busy = ["PREPARING", "UPLOADING", "ATTACHING"].includes(phase);

  useEffect(() => {
    onActivityChange(active);
  }, [active, onActivityChange]);

  const adopt = useCallback((value: AudioRecordingSession) => {
    sessionRef.current = value;
    setSession(value);
    setAsset(value.asset);
    if (
      value.asset?.attachment_status === "ATTACHED" &&
      value.asset.saved_response_revision
    ) {
      const ack: AudioAttachmentAck = {
        status: "ATTACHED",
        media_asset_id: value.asset.media_asset_id,
        revision: value.asset.saved_response_revision,
      };
      setMessage(audioAttachmentMessage(ack, value.asset.media_asset_id));
      setPhase("ATTACHED");
      retained.current = null;
      startOperation.current = null;
      confirmed.current(
        value.asset.media_asset_id,
        value.asset.saved_response_revision,
      );
    } else if (value.asset && value.status === "READY")
      setPhase("UPLOADED_NOT_ATTACHED");
    else if (["RECORDING", "UPLOADING", "STAGED"].includes(value.status))
      setPhase("UNFINISHED");
    else {
      setPhase("IDLE");
      if (["CANCELLED", "REJECTED", "EXPIRED"].includes(value.status)) {
        retained.current = null;
        chunks.current = [];
        startOperation.current = null;
      }
    }
  }, []);

  useEffect(() => {
    if (!context || !policy) {
      setMetadataLoading(false);
      return;
    }
    const controller = new AbortController();
    setMetadataLoading(true);
    setMetadataError(null);
    void candidateApi
      .audioEvidence(attemptId, bindingId, controller.signal)
      .then((value) => {
        if (controller.signal.aborted || contextRef.current !== context) return;
        const latest = [...value.recordings].sort(
          (a, b) => b.attempt_number - a.attempt_number,
        )[0];
        if (latest) adopt(latest);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setMetadataError(
            cause instanceof Error
              ? cause.message
              : "Recording evidence could not be loaded.",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setMetadataLoading(false);
      });
    return () => controller.abort();
  }, [context, attemptId, bindingId, policy, refresh, adopt]);

  useEffect(() => {
    contextRef.current = context;
    return () => {
      contextRef.current = null;
      abort.current?.abort();
      if (recorder.current) {
        recorder.current.onstop = null;
        recorder.current.ondataavailable = null;
        if (recorder.current.state !== "inactive") recorder.current.stop();
      }
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      chunks.current = [];
      retained.current = null;
    };
  }, [context]);

  async function attach(value: AudioMediaAsset, expectedContext: string) {
    if (!editable || contextRef.current !== expectedContext) return;
    setPhase("ATTACHING");
    try {
      const operation = (attachmentOperation.current[value.media_asset_id] ??=
        crypto.randomUUID());
      const ack = await onSaved(value.media_asset_id, operation);
      if (contextRef.current !== expectedContext) return;
      setMessage(audioAttachmentMessage(ack, value.media_asset_id));
      if (ack.status === "ATTACHED") {
        setAsset({
          ...value,
          attachment_status: "ATTACHED",
          saved_response_revision: ack.revision,
        });
        setPhase("ATTACHED");
        confirmed.current(value.media_asset_id, ack.revision);
        retained.current = null;
        chunks.current = [];
        startOperation.current = null;
      } else setPhase("QUEUED_RESPONSE");
    } catch (cause) {
      if (contextRef.current !== expectedContext) return;
      setPhase("CONFLICT");
      setMessage(
        cause instanceof Error
          ? cause.message
          : "Audio is uploaded but attachment was not confirmed. Refresh recording evidence before retrying.",
      );
    }
  }

  async function upload(
    value: AudioRecordingSession,
    blob: Blob,
    expectedContext: string,
  ) {
    if (!policy || contextRef.current !== expectedContext) return;
    if (!blob.size || blob.size > policy.max_upload_bytes) {
      setPhase("FAILED");
      setMessage(
        "Recording bytes exceed the published upload limit or are empty. Cancel this recording before trying again.",
      );
      return;
    }
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setPhase("UPLOADING");
    setMessage("Uploading audio. It is not yet attached to your response.");
    try {
      const result = await candidateApi.finalizeAudioRecording(
        value.recording_session_id,
        blob,
        recordingFilename(blob.type),
        controller.signal,
      );
      if (controller.signal.aborted || contextRef.current !== expectedContext)
        return;
      const storedSession = { ...value, status: "READY", asset: result };
      sessionRef.current = storedSession;
      setSession(storedSession);
      setAsset(result);
      setPhase("UPLOADED_NOT_ATTACHED");
      await attach(result, expectedContext);
    } catch (cause) {
      if (controller.signal.aborted || contextRef.current !== expectedContext)
        return;
      setPhase("FAILED");
      setMessage(
        cause instanceof Error
          ? cause.message
          : "Upload could not be confirmed. The recording remains in this page memory only; retry or recover the stored upload.",
      );
    }
  }

  function stopRecording() {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }

  useEffect(() => {
    const capture = recorder.current;
    if (editable || !capture || capture.state !== "recording") return;
    capture.onstop = () => {
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
    };
    capture.stop();
    chunks.current = [];
    retained.current = null;
    setPhase("FAILED");
    setMessage(
      "This attempt is no longer writable. The unfinished recording was stopped and was not attached.",
    );
  }, [editable]);

  useEffect(() => {
    if (phase !== "RECORDING" || !session) {
      setRemaining(null);
      return;
    }
    const update = () => {
      const seconds = Math.max(
        0,
        Math.ceil(
          (Date.parse(session.recording_expires_at) - Date.now()) / 1000,
        ),
      );
      setRemaining(seconds);
      if (seconds === 0) stopRecording();
    };
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [phase, session]);

  async function preflight() {
    if (!policy || !editable) return;
    try {
      if (
        typeof MediaRecorder === "undefined" ||
        !selectRecordingMime(policy, (mime) =>
          MediaRecorder.isTypeSupported(mime),
        )
      )
        throw new Error(
          "This browser cannot record any format accepted by this published task. Use a compatible browser.",
        );
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      media.getTracks().forEach((track) => track.stop());
      if (contextRef.current !== context) return;
      setPermission(true);
      setMessage(
        "Microphone ready. Choose Start recording when you are ready.",
      );
    } catch (cause) {
      setMessage(
        cause instanceof Error
          ? cause.message
          : "Microphone permission was not granted.",
      );
    }
  }

  async function start() {
    if (!context || !policy || !editable || !permission || active) return;
    const expectedContext = context;
    onActivityChange(true);
    setPhase("PREPARING");
    setMessage(null);
    try {
      const mime = selectRecordingMime(policy, (type) =>
        MediaRecorder.isTypeSupported(type),
      );
      if (!mime)
        throw new Error(
          "No compatible recording format is available for this task.",
        );
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (contextRef.current !== expectedContext) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      const capture = new MediaRecorder(media, { mimeType: mime });
      const operation = (startOperation.current ??= crypto.randomUUID());
      const controller = new AbortController();
      abort.current = controller;
      const value = await candidateApi.startAudioRecording(
        attemptId,
        bindingId,
        operation,
        controller.signal,
      );
      if (controller.signal.aborted || contextRef.current !== expectedContext)
        return;
      if (value.asset) {
        adopt(value);
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      sessionRef.current = value;
      setSession(value);
      chunks.current = [];
      let capturedBytes = 0;
      capture.ondataavailable = (event) => {
        capturedBytes += event.data.size;
        if (capturedBytes > policy.max_upload_bytes) {
          capture.onstop = () => {
            media.getTracks().forEach((track) => track.stop());
            stream.current = null;
          };
          chunks.current = [];
          if (capture.state !== "inactive") capture.stop();
          setPhase("FAILED");
          setMessage(
            "The recording exceeded the published upload limit. Cancel this recording before trying again.",
          );
        } else if (event.data.size) chunks.current.push(event.data);
      };
      capture.onstop = () => {
        media.getTracks().forEach((track) => track.stop());
        stream.current = null;
        if (contextRef.current !== expectedContext) return;
        const blob = new Blob(chunks.current, {
          type: capture.mimeType || mime,
        });
        retained.current = blob;
        chunks.current = [];
        void upload(value, blob, expectedContext);
      };
      recorder.current = capture;
      capture.start(1000);
      setPhase("RECORDING");
    } catch (cause) {
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      if (contextRef.current === expectedContext) {
        setPhase("FAILED");
        setMessage(
          cause instanceof Error
            ? cause.message
            : "Recording could not be started. Retry with the same operation or refresh recording evidence.",
        );
      }
    }
  }

  async function recover() {
    if (!session || !context || busy) return;
    setPhase("PREPARING");
    const expectedContext = context;
    try {
      const value = await candidateApi.recoverAudioRecording(
        session.recording_session_id,
      );
      if (contextRef.current === expectedContext) adopt(value);
    } catch (cause) {
      if (contextRef.current === expectedContext) {
        setPhase("FAILED");
        setMessage(
          cause instanceof Error
            ? cause.message
            : "No durable upload could be recovered.",
        );
      }
    }
  }

  async function cancel() {
    if (!session || !context || busy) return;
    const expectedContext = context;
    const previousPhase = phase;
    setPhase("PREPARING");
    try {
      await candidateApi.cancelAudioRecording(session.recording_session_id);
      if (contextRef.current !== expectedContext) return;
      retained.current = null;
      chunks.current = [];
      startOperation.current = null;
      setAsset(null);
      setPhase("IDLE");
      setMessage(
        "The unfinished recording was cancelled. Existing stored evidence is retained according to policy.",
      );
      setRefresh((value) => value + 1);
    } catch (cause) {
      if (contextRef.current === expectedContext) {
        setPhase(previousPhase);
        setMessage(
          cause instanceof Error
            ? cause.message
            : "Cancellation could not be confirmed.",
        );
      }
    }
  }

  const terminal = Boolean(
    session && ["CANCELLED", "REJECTED", "EXPIRED"].includes(session.status),
  );
  const attemptLimit = Boolean(
    policy &&
      session &&
      session.attempt_number >= policy.max_recording_attempts,
  );
  const playbackId =
    asset?.attachment_status === "ATTACHED"
      ? asset.media_asset_id
      : currentMediaAssetId;
  return (
    <section
      aria-labelledby="audio-response-heading"
      style={{ overflowWrap: "anywhere" }}
    >
      <h3 id="audio-response-heading">Recorded response</h3>
      {!policy ? (
        <p role="alert">
          This task has no usable published recording policy. Recording is
          unavailable.
        </p>
      ) : (
        <p>
          Published recording limits: {policy.max_recording_seconds} seconds,{" "}
          {policy.max_recording_attempts} attempts, {policy.max_upload_bytes}{" "}
          bytes. Only an attached response may be submitted.
        </p>
      )}
      <p>Recording state: {phase}</p>
      {session && (
        <p>
          Recording attempt: {session.attempt_number} of{" "}
          {policy?.max_recording_attempts ?? "Unavailable"}
        </p>
      )}
      {remaining !== null && (
        <p>Server recording time remaining: {remaining} seconds</p>
      )}
      {attemptLimit && (
        <p>
          The published recording attempt limit has been reached. Existing
          evidence remains available.
        </p>
      )}
      {message && (
        <p
          role={phase === "FAILED" || phase === "CONFLICT" ? "alert" : "status"}
        >
          {message}
        </p>
      )}
      {metadataError && <p role="alert">{metadataError}</p>}
      <p>
        Unuploaded audio is held in this page memory only. Reloading or signing
        out clears it.
      </p>
      <button
        type="button"
        disabled={
          !editable ||
          !policy ||
          busy ||
          active ||
          attemptLimit ||
          Boolean(metadataError)
        }
        onClick={() => void preflight()}
      >
        Check microphone
      </button>{" "}
      <button
        type="button"
        disabled={
          !editable ||
          !policy ||
          !permission ||
          busy ||
          active ||
          attemptLimit ||
          Boolean(metadataError)
        }
        onClick={() => void start()}
      >
        Start recording
      </button>{" "}
      <button
        type="button"
        disabled={phase !== "RECORDING"}
        onClick={stopRecording}
      >
        Stop and save recording
      </button>{" "}
      {phase === "FAILED" && retained.current && session && (
        <button
          type="button"
          onClick={() =>
            context && void upload(session, retained.current!, context)
          }
        >
          Retry upload
        </button>
      )}
      {asset &&
        session?.status === "READY" &&
        asset.attachment_status !== "ATTACHED" &&
        editable &&
        !busy && (
          <button
            type="button"
            onClick={() => context && void attach(asset, context)}
          >
            Attach uploaded audio
          </button>
        )}
      {session &&
        editable &&
        !terminal &&
        phase !== "RECORDING" &&
        !busy &&
        phase !== "ATTACHED" && (
          <>
            <button type="button" onClick={() => void recover()}>
              Recover stored upload
            </button>{" "}
            <button type="button" onClick={() => void cancel()}>
              Cancel unfinished recording
            </button>
          </>
        )}
      <p>
        <button
          type="button"
          disabled={phase === "RECORDING" || busy}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Refresh recording evidence
        </button>
      </p>
      {asset && (
        <dl>
          <dt>Audio checksum</dt>
          <dd style={{ overflowWrap: "anywhere" }}>{asset.sha256}</dd>
          <dt>Verified duration</dt>
          <dd>{asset.duration_ms} ms</dd>
          <dt>Attachment</dt>
          <dd>{asset.attachment_status}</dd>
        </dl>
      )}
      {playbackId && policy && (
        <PrivateAudioPlayer
          key={playbackId}
          path={`/api/v1/candidate/audio-assets/${playbackId}/stream`}
          maximumBytes={policy.max_upload_bytes}
        />
      )}
    </section>
  );
}
