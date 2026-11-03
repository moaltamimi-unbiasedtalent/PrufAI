import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  candidateApi,
  type AudioRecordingPolicy,
  type AudioRecordingSession,
} from "./app/candidate/api";
import {
  audioAttachmentMessage,
  recordingFilename,
  recordingPolicy,
  selectRecordingMime,
} from "./app/candidate/audio-contract";
import {
  anchorRecordingClock,
  hasUnresolvedAudio,
  recordingRemainingMs,
} from "./app/candidate/audio-timing";
import { AudioResponseControl } from "./app/candidate/exams/[attemptId]/audio-response-control";
import {
  speakingReviewApi,
  type AudioEvidenceAsset,
} from "./app/institution/speaking-reviews/review-api";
import { AudioEvidenceView } from "./app/institution/speaking-reviews/audio-evidence-view";
import { AudioUrlLease, fetchPrivateAudio } from "./lib/private-audio";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import { canViewRoute } from "./lib/access";

vi.mock("./lib/auth", () => ({
  useAuth: () => ({
    user: { id: "candidate-a" },
    organisationId: "org-a",
    permissions: ["subjective.read"],
  }),
}));
const policy: AudioRecordingPolicy = {
  max_recording_seconds: 30,
  max_recording_attempts: 2,
  max_upload_bytes: 2 * 1024 * 1024,
  supported_mime_types: [
    "audio/webm;codecs=opus",
    "audio/mp4;codecs=mp4a.40.2",
  ],
  submission_behavior: "REQUIRE_ATTACHED",
  upload_grace_seconds: 120,
};
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function recordingSession(
  change: Partial<AudioRecordingSession> = {},
): AudioRecordingSession {
  return {
    recording_session_id: "recording-a",
    status: "RECORDING",
    recording_started_at: "2026-11-03T12:00:00Z",
    recording_expires_at: "2026-11-03T12:00:30Z",
    server_now: "2026-11-03T12:00:00Z",
    recording_remaining_ms: 30_000,
    upload_remaining_ms: 150_000,
    upload_expires_at: "2026-11-03T12:02:30Z",
    attempt_number: 1,
    policy,
    recoverable: false,
    last_error_category: null,
    asset: null,
    ...change,
  };
}

describe("server-authoritative monotonic recording timing and recovery", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setSessionToken(null);
  });

  it.each([-60_000, 0, 60_000])(
    "ignores local wall-clock skew of %s ms without granting more than the server allowance",
    (offset) => {
      vi.spyOn(Date, "now").mockReturnValue(
        Date.parse("2026-11-03T12:00:00Z") + offset,
      );
      const timed = anchorRecordingClock(recordingSession(), 1000, 1200);
      expect(recordingRemainingMs(timed, 1200)).toBe(29_800);
      expect(recordingRemainingMs(timed, 6200)).toBe(24_800);
      expect(recordingRemainingMs(timed, 31_000)).toBe(0);
    },
  );

  it("uses all elapsed monotonic time after a delayed event or background-tab resume", () => {
    const timed = anchorRecordingClock(recordingSession(), 1000, 1100);
    expect(recordingRemainingMs(timed, 1100)).toBe(29_900);
    // A timer callback that arrives 40 seconds late receives no fresh allowance.
    expect(recordingRemainingMs(timed, 41_100)).toBe(0);
  });

  it("fails closed when server timing is unknown, exceeds policy, or the monotonic clock is inconsistent", () => {
    for (const change of [
      { server_now: "invalid" },
      { recording_remaining_ms: 30_001 },
      { recording_remaining_ms: -1 },
      { recording_remaining_ms: NaN },
    ]) {
      const timed = anchorRecordingClock(recordingSession(change), 1000, 1100);
      expect(recordingRemainingMs(timed, 1100)).toBeNull();
    }
    const timed = anchorRecordingClock(recordingSession(), 1000, 1100);
    expect(recordingRemainingMs(timed, 1099)).toBeNull();
    expect(recordingRemainingMs(recordingSession(), 1100)).toBeNull();
  });

  it("honors an already expired server recording and a fresh allowance after metadata reload", () => {
    const expired = anchorRecordingClock(
      recordingSession({ recording_remaining_ms: 0 }),
      1000,
      1100,
    );
    expect(recordingRemainingMs(expired, 1100)).toBe(0);
    const restored = anchorRecordingClock(
      recordingSession({ recording_remaining_ms: 5000 }),
      50_000,
      50_300,
    );
    expect(recordingRemainingMs(restored, 50_300)).toBe(4700);
    expect(recordingRemainingMs(restored, 55_000)).toBe(0);
  });

  it("keeps a reloaded staged upload unresolved through failure until attachment or server cancellation", () => {
    const staged = recordingSession({
      status: "UPLOADING",
      recoverable: true,
      recording_remaining_ms: 0,
      last_error_category: "UPLOAD_RECOVERY_REQUIRED",
    });
    expect(hasUnresolvedAudio(staged)).toBe(true);
    // A 503 does not change the persisted receipt or require a retained Blob.
    expect(hasUnresolvedAudio({ ...staged })).toBe(true);
    const asset = {
      media_asset_id: "asset-a",
      status: "READY",
      sha256: "digest",
      byte_size: 100,
      duration_ms: 1000,
      content_type: "audio/webm;codecs=opus",
      validation_provenance: {},
      saved_response_revision: null,
      candidate_response_revision_id: null,
      attachment_status: "UPLOADED_NOT_ATTACHED" as const,
    };
    expect(hasUnresolvedAudio({ ...staged, status: "READY", asset })).toBe(
      true,
    );
    expect(
      hasUnresolvedAudio({
        ...staged,
        status: "READY",
        asset: {
          ...asset,
          attachment_status: "ATTACHED",
          saved_response_revision: 1,
        },
      }),
    ).toBe(false);
    expect(hasUnresolvedAudio({ ...staged, status: "CANCELLED" })).toBe(false);
    expect(hasUnresolvedAudio({ ...staged, status: "EXPIRED" })).toBe(false);
  });

  it("anchors actual API session metadata at receipt and conservatively deducts the full request round trip", async () => {
    setSessionToken("private-bearer");
    setOrganisationContext("org-a");
    vi.spyOn(performance, "now")
      .mockReturnValueOnce(1000)
      .mockReturnValueOnce(1800);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(recordingSession())));
    const session = await candidateApi.startAudioRecording("a", "b", "op");
    expect(recordingRemainingMs(session, 1800)).toBe(29_200);
    expect(recordingRemainingMs(session, 31_000)).toBe(0);
  });
});

describe("published audio recording contract", () => {
  beforeEach(() => {
    setSessionToken("private-bearer");
    setOrganisationContext("org-a");
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("fails closed for missing legacy or invalid limits and never supplies a frontend default policy", () => {
    expect(recordingPolicy(undefined)).toBeNull();
    expect(recordingPolicy({})).toBeNull();
    for (const change of [
      { max_recording_seconds: 0 },
      { max_recording_attempts: -1 },
      { max_upload_bytes: Infinity },
      { supported_mime_types: [] },
      { submission_behavior: "OPTIONAL" },
      { upload_grace_seconds: 301 },
    ])
      expect(recordingPolicy({ ...policy, ...change })).toBeNull();
    expect(recordingPolicy(policy)).toBe(policy);
  });

  it("negotiates only the server's offered MIME types and chooses the actual container filename", () => {
    const supported = vi.fn(
      (mime: string) => mime === "audio/mp4;codecs=mp4a.40.2",
    );
    expect(selectRecordingMime(policy, supported)).toBe(
      "audio/mp4;codecs=mp4a.40.2",
    );
    expect(supported.mock.calls.map(([mime]) => mime)).toEqual(
      policy.supported_mime_types,
    );
    expect(selectRecordingMime(policy, () => false)).toBeNull();
    expect(recordingFilename("audio/mp4;codecs=mp4a.40.2")).toBe(
      "candidate-audio.mp4",
    );
    expect(recordingFilename("audio/webm;codecs=opus")).toBe(
      "candidate-audio.webm",
    );
    expect(() => recordingFilename("audio/unknown")).toThrow("not supported");
  });

  it("reuses the caller's stable start operation and authenticated exact item route on transport retries", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(async () =>
        json({ recording_session_id: "session-a" }),
      );
    vi.stubGlobal("fetch", fetch);
    const abort = new AbortController();
    await candidateApi.startAudioRecording(
      "attempt-a",
      "binding-a",
      "stable-operation",
      abort.signal,
    );
    await candidateApi.startAudioRecording(
      "attempt-a",
      "binding-a",
      "stable-operation",
      abort.signal,
    );
    for (const [path, init] of fetch.mock.calls) {
      expect(path).toBe(
        "/api/v1/candidate/attempts/attempt-a/items/binding-a/audio-recordings",
      );
      expect(JSON.parse(init.body)).toEqual({
        operation_id: "stable-operation",
      });
      expect(init.signal).toBe(abort.signal);
      expect(new Headers(init.headers).get("Authorization")).toBe(
        "Bearer private-bearer",
      );
      expect(new Headers(init.headers).get("X-PrufAI-Organisation-ID")).toBe(
        "org-a",
      );
    }
  });

  it("keeps codec-qualified upload headers and binary bytes with a cancellable request", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(json({ media_asset_id: "asset-a" }));
    vi.stubGlobal("fetch", fetch);
    const blob = new Blob(["container bytes"], {
      type: "audio/webm;codecs=opus",
    });
    const abort = new AbortController();
    await candidateApi.finalizeAudioRecording(
      "session-a",
      blob,
      "candidate-audio.webm",
      abort.signal,
    );
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/api/v1/candidate/audio-recordings/session-a/finalize");
    expect(init.body).toBe(blob);
    expect(new Headers(init.headers).get("Content-Type")).toBe(
      "audio/webm;codecs=opus",
    );
    expect(init.signal).toBe(abort.signal);
  });

  it("uses metadata, durable recovery and cancellation without inserting assessment or score rows", async () => {
    const fetch = vi.fn().mockImplementation(async () => json({}));
    vi.stubGlobal("fetch", fetch);
    await candidateApi.audioEvidence("attempt-a", "binding-a");
    await candidateApi.recoverAudioRecording("session-a");
    await candidateApi.cancelAudioRecording("session-a");
    expect(
      fetch.mock.calls.map(([path, init]) => [path, init.method ?? "GET"]),
    ).toEqual([
      [
        "/api/v1/candidate/attempts/attempt-a/items/binding-a/audio-evidence",
        "GET",
      ],
      ["/api/v1/candidate/audio-recordings/session-a/recover", "POST"],
      ["/api/v1/candidate/audio-recordings/session-a/cancel", "POST"],
    ]);
  });

  it("reports saved only for the exact authoritative asset and positive response revision", () => {
    expect(
      audioAttachmentMessage(
        { status: "ATTACHED", media_asset_id: "asset-a", revision: 2 },
        "asset-a",
      ),
    ).toBe("Audio saved to this response.");
    expect(
      audioAttachmentMessage({ status: "QUEUED_RESPONSE" }, "asset-a"),
    ).toContain("not yet saved");
    expect(() =>
      audioAttachmentMessage(
        { status: "ATTACHED", media_asset_id: "asset-b", revision: 2 },
        "asset-a",
      ),
    ).toThrow("exact audio attachment");
    expect(() =>
      audioAttachmentMessage(
        { status: "ATTACHED", media_asset_id: "asset-a", revision: 0 },
        "asset-a",
      ),
    ).toThrow("exact audio attachment");
  });

  it("shows unsupported policy and recording limits honestly without an invented successful save", () => {
    const props = {
      attemptId: "a",
      bindingId: "b",
      editable: true,
      onSaved: vi.fn(),
      onAttachmentConfirmed: vi.fn(),
      onActivityChange: vi.fn(),
    };
    expect(
      renderToStaticMarkup(<AudioResponseControl {...props} policy={null} />),
    ).toContain("no usable published recording policy");
    const html = renderToStaticMarkup(
      <AudioResponseControl {...props} policy={policy} />,
    );
    expect(html).toContain("30 seconds, 2 attempts, 2097152 bytes");
    expect(html).toContain("page memory only");
    expect(html).not.toContain("Audio saved to this response.");
  });
});

describe("private buffered audio playback", () => {
  beforeEach(() => {
    setSessionToken("private-bearer");
    setOrganisationContext("org-a");
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("authenticates binary retrieval without a token, storage key or private object URL in the media path", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(new Blob(["OggS test bytes"], { type: "audio/ogg" }), {
        headers: { "Content-Type": "audio/ogg" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const path = "/api/v1/candidate/audio-assets/asset-a/stream";
    expect(
      (await fetchPrivateAudio(path, new AbortController().signal, 100)).size,
    ).toBeGreaterThan(0);
    expect(fetch.mock.calls[0][0]).toBe(path);
    expect(
      new Headers(fetch.mock.calls[0][1].headers).get("Authorization"),
    ).toBe("Bearer private-bearer");
    expect(fetch.mock.calls[0][1].credentials).toBe("same-origin");
    await expect(
      fetchPrivateAudio(
        "https://storage.invalid/private?token=secret",
        new AbortController().signal,
        100,
      ),
    ).rejects.toThrow("same-origin");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([403, 404, 503])(
    "preserves HTTP %s playback failure and never returns an audio Blob",
    async (status) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({}, status)));
      await expect(
        fetchPrivateAudio(
          "/api/v1/candidate/audio-assets/a/stream",
          new AbortController().signal,
          100,
        ),
      ).rejects.toMatchObject({ status });
    },
  );

  it("rejects non-audio, oversize and aborted playback instead of exposing a stale object URL", async () => {
    const path = "/api/v1/candidate/audio-assets/a/stream";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({})));
    await expect(
      fetchPrivateAudio(path, new AbortController().signal, 100),
    ).rejects.toThrow("did not return an audio");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("oversize", {
          headers: { "Content-Type": "audio/ogg" },
        }),
      ),
    );
    await expect(
      fetchPrivateAudio(path, new AbortController().signal, 2),
    ).rejects.toThrow("size");
    const abort = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: new Headers({ "Content-Type": "audio/ogg" }),
        blob: async () => {
          abort.abort();
          return new Blob(["audio"]);
        },
      }),
    );
    await expect(
      fetchPrivateAudio(path, abort.signal, 100),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  it("revokes every replaced or cleared playback URL exactly once", () => {
    const create = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValueOnce("blob:one")
      .mockReturnValueOnce("blob:two");
    const revoke = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => undefined);
    const lease = new AudioUrlLease();
    expect(lease.replace(new Blob(["one"]))).toBe("blob:one");
    expect(lease.replace(new Blob(["two"]))).toBe("blob:two");
    lease.clear();
    lease.clear();
    expect(create).toHaveBeenCalledTimes(2);
    expect(revoke.mock.calls).toEqual([["blob:one"], ["blob:two"]]);
  });

  it("loads actual bound evidence through selected-organisation reviewer metadata without a fake assessment", async () => {
    const fetch = vi.fn().mockImplementation(async () => json({}));
    vi.stubGlobal("fetch", fetch);
    await speakingReviewApi.audioList("org-a");
    await speakingReviewApi.audioDetail("org-a", "asset-a");
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/organisations/org-a/audio-evidence",
      "/api/v1/organisations/org-a/audio-evidence/asset-a",
    ]);
    const member = {
      organisation_id: "org-a",
      organisation_name: "School",
      status: "ACTIVE",
      role_keys: ["EXAMINER"],
      permissions: [],
    };
    expect(
      canViewRoute("/institution/speaking-reviews/audio/asset-a", member),
    ).toBe(false);
    expect(
      canViewRoute("/institution/speaking-reviews/audio/asset-a", {
        ...member,
        permissions: ["subjective.read"],
      }),
    ).toBe(true);
    const item = {
      media_asset_id: "asset-a",
      attempt_id: "attempt-a",
      binding_id: "binding-a",
      item_version_id: "item-a",
      byte_size: 12,
      duration_ms: 900,
      content_type: "audio/ogg",
      sha256: "checksum",
      validation_provenance: { private_storage_key: "PRIVATE_OBJECT_KEY" },
      rationale: "PRIVATE_RATIONALE",
    } as unknown as AudioEvidenceAsset;
    const html = renderToStaticMarkup(
      <AudioEvidenceView item={item} organisationId="org-a" />,
    );
    expect(html).toContain("authenticated playback only");
    expect(html).not.toMatch(
      /PRIVATE_OBJECT_KEY|PRIVATE_RATIONALE|human-approve|scoring/,
    );
    expect(html).not.toContain("src=");
  });
});
