import { authenticatedFetch } from "./api";

export class AudioPlaybackError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function fetchPrivateAudio(
  path: string,
  signal: AbortSignal,
  maximumBytes: number,
): Promise<Blob> {
  const response = await authenticatedFetch(path, { signal });
  if (!response.ok) {
    throw new AudioPlaybackError(
      response.status,
      response.status === 401
        ? "Your session expired. Sign in again to load the recording."
        : response.status === 403
          ? "Your account is not permitted to play this recording."
          : response.status === 404
            ? "This recording is unavailable to your account."
            : "The stored recording could not be loaded. Retry the connection.",
    );
  }
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("audio/"))
    throw new AudioPlaybackError(
      502,
      "The server did not return an audio recording.",
    );
  const blob = await response.blob();
  if (!blob.size || blob.size > maximumBytes)
    throw new AudioPlaybackError(
      502,
      "The recording size does not match the permitted evidence bounds.",
    );
  if (signal.aborted)
    throw new DOMException("Audio loading cancelled", "AbortError");
  return blob;
}

export class AudioUrlLease {
  private url: string | null = null;
  replace(blob: Blob): string {
    this.clear();
    this.url = URL.createObjectURL(blob);
    return this.url;
  }
  clear(): void {
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
  }
}
