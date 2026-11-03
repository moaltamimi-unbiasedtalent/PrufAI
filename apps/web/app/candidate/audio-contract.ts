import type { AudioRecordingPolicy } from "./api";

export type AudioAttachmentAck =
  | { status: "ATTACHED"; media_asset_id: string; revision: number }
  | { status: "QUEUED_RESPONSE" };

export function recordingPolicy(value: unknown): AudioRecordingPolicy | null {
  if (!value || typeof value !== "object") return null;
  const policy = value as Record<string, unknown>;
  return [
    "max_recording_seconds",
    "max_recording_attempts",
    "max_upload_bytes",
  ].every(
    (key) => Number.isSafeInteger(policy[key]) && Number(policy[key]) > 0,
  ) &&
    Array.isArray(policy.supported_mime_types) &&
    policy.supported_mime_types.length > 0 &&
    policy.supported_mime_types.every(
      (type) => typeof type === "string" && type.startsWith("audio/"),
    ) &&
    policy.submission_behavior === "REQUIRE_ATTACHED" &&
    Number.isSafeInteger(policy.upload_grace_seconds) &&
    Number(policy.upload_grace_seconds) >= 0 &&
    Number(policy.upload_grace_seconds) <= 300
    ? (value as AudioRecordingPolicy)
    : null;
}

export function selectRecordingMime(
  policy: AudioRecordingPolicy,
  supported: (mime: string) => boolean,
): string | null {
  return policy.supported_mime_types.find((mime) => supported(mime)) ?? null;
}

export function recordingFilename(mime: string): string {
  const container = mime.split(";", 1)[0].trim().toLowerCase();
  const extension: Record<string, string> = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/wav": "wav",
  };
  if (!extension[container])
    throw new Error(
      "This recording container is not supported by the published policy.",
    );
  return `candidate-audio.${extension[container]}`;
}

export function audioAttachmentMessage(
  ack: AudioAttachmentAck,
  assetId: string,
): string {
  if (ack.status === "QUEUED_RESPONSE")
    return "Audio uploaded; response attachment is queued for this account. It is not yet saved to the response.";
  if (
    ack.media_asset_id !== assetId ||
    !Number.isInteger(ack.revision) ||
    ack.revision < 1
  )
    throw new Error(
      "The server did not confirm this exact audio attachment. Refresh before retrying.",
    );
  return "Audio saved to this response.";
}
