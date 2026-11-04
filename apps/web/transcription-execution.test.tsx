import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import type { AudioEvidenceAsset } from "./app/institution/speaking-reviews/review-api";
import {
  exactTranscriptionSource,
  exactTranscriptVersion,
  transcriptionApi,
  type TranscriptionJob,
  type TranscriptVersion,
} from "./app/institution/speaking-reviews/transcription-api";
import {
  DerivedTranscriptView,
  TranscriptionEvidencePanel,
  TranscriptionJobView,
} from "./app/institution/speaking-reviews/transcription-evidence";
import { metadataSections, MetadataTable } from "./app/control-center/metadata";

let actor = {
  user: { id: "reviewer-a" },
  organisationId: "org-a",
  permissions: ["speech_transcription.read"],
};
vi.mock("./lib/auth", () => ({ useAuth: () => actor }));

const audio = {
  media_asset_id: "asset-a",
  sha256: "a".repeat(64),
  duration_ms: 1500,
  candidate_response_revision_id: "response-r1",
} as AudioEvidenceAsset;
const source = {
  candidate_media_asset_id: audio.media_asset_id,
  candidate_response_revision_id: "response-r1",
  audio_sha256: audio.sha256,
  verified_duration_ms: audio.duration_ms,
};
const job: TranscriptionJob = {
  id: "job-a",
  transcription_id: "transcription-a",
  asset_id: audio.media_asset_id,
  candidate_response_revision_id: "response-r1",
  audio_sha256: audio.sha256,
  verified_duration_ms: audio.duration_ms,
  status: "READY",
  profile_id: "profile-a",
  profile_version: "immutable-profile-digest",
  provider: "DETERMINISTIC_TEST",
  model_name: "fixture-transcriber",
  adapter_version: "1",
  language: "de",
  test_only: true,
  attempt_count: 1,
  max_attempts: 3,
  created_at: "2026-11-04T12:00:00Z",
  started_at: "2026-11-04T12:00:01Z",
  completed_at: "2026-11-04T12:00:02Z",
  failure_category: null,
  transcription_version_id: "version-a",
  can_retry: false,
  can_cancel: false,
  state_history: [
    { status: "QUEUED", at: "2026-11-04T12:00:00Z", attempt_count: 0 },
    { status: "RUNNING", at: "2026-11-04T12:00:01Z", attempt_count: 1 },
    { status: "READY", at: "2026-11-04T12:00:02Z", attempt_count: 1 },
  ],
};
const version: TranscriptVersion = {
  id: "version-a",
  transcription_id: "transcription-a",
  version_number: 1,
  status: "READY",
  checksum: "transcript-digest",
  candidate_media_asset_id: audio.media_asset_id,
  candidate_response_revision_id: "response-r1",
  audio_sha256: audio.sha256,
  execution_job_id: job.id,
  profile_version: job.profile_version,
  provider: job.provider,
  model_name: job.model_name,
  adapter_version: job.adapter_version,
  language: "de",
  test_only: true,
  transcript: "TEST ONLY PRIVATE TRANSCRIPT <script>untrusted()</script>",
  segments: [],
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("governed transcription institution boundary", () => {
  beforeEach(() => {
    setSessionToken("reviewer-bearer");
    setOrganisationContext("org-a");
    actor = {
      user: { id: "reviewer-a" },
      organisationId: "org-a",
      permissions: ["speech_transcription.read"],
    };
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });

  it("sends the exact submitted revision and audio digest with an operation identity using authenticated tenant requests", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(json(job));
    vi.stubGlobal("fetch", fetch);
    await transcriptionApi.request("org-a", source, "profile-a", "de", "op-a");
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/candidate-media/asset-a/transcriptions",
    );
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      profile_id: "profile-a",
      language: "de",
      operation_id: "op-a",
      candidate_response_revision_id: "response-r1",
      audio_sha256: audio.sha256,
    });
    const headers = new Headers(fetch.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer reviewer-bearer");
    expect(headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
    expect(fetch.mock.calls[0][1]?.body).not.toMatch(
      /storage_key|private_url|credentials|transcript|audio_bytes/,
    );
  });

  it.each([401, 403, 409, 503])(
    "preserves failed HTTP %s and never displays raw provider errors or secrets",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            json(
              { detail: "PRIVATE PROVIDER SECRET https://private/audio" },
              status,
            ),
          ),
      );
      await expect(
        transcriptionApi.history("org-a", "asset-a"),
      ).rejects.toMatchObject({ status });
      await expect(
        transcriptionApi.history("org-a", "asset-a"),
      ).rejects.not.toThrow(/PRIVATE PROVIDER SECRET|private\/audio/);
    },
  );

  it("rejects mismatched submitted response, checksum and duration before treating options as exact evidence", () => {
    expect(exactTranscriptionSource(audio, source)).toBe(true);
    for (const change of [
      { candidate_media_asset_id: "asset-b" },
      { candidate_response_revision_id: "response-r2" },
      { audio_sha256: "b".repeat(64) },
      { verified_duration_ms: 1501 },
    ])
      expect(exactTranscriptionSource(audio, { ...source, ...change })).toBe(
        false,
      );
  });

  it("withholds transcript text for another asset, revision, execution, profile or downgraded TEST ONLY provenance", () => {
    expect(exactTranscriptVersion(audio, version, job)).toBe(true);
    for (const change of [
      { candidate_media_asset_id: "asset-b" },
      { candidate_response_revision_id: "response-r2" },
      { audio_sha256: "b".repeat(64) },
      { execution_job_id: "job-b" },
      { profile_version: "a-new-profile" },
      { test_only: false },
      { status: "FAILED" },
    ]) {
      const changed = { ...version, ...change };
      expect(exactTranscriptVersion(audio, changed, job)).toBe(false);
      const html = renderToStaticMarkup(
        <DerivedTranscriptView item={audio} version={changed} job={job} />,
      );
      expect(html).toContain("not displayed");
      expect(html).not.toContain("PRIVATE TRANSCRIPT");
    }
    expect(exactTranscriptVersion(audio, version, undefined)).toBe(false);
  });

  it("labels test output as derived, escapes provider text, shows immutable identity and invents no segments", () => {
    const html = renderToStaticMarkup(
      <DerivedTranscriptView item={audio} version={version} job={job} />,
    );
    expect(html).toContain("Machine-derived transcript");
    expect(html).toContain("original recording remains primary evidence");
    expect(html).toContain("TEST ONLY TRANSCRIPT");
    expect(html).toContain("not recognition of the recorded speech");
    expect(html).toContain("immutable-profile-digest");
    expect(html).toContain("response-r1");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("Provider transcript segments");
    expect(html).not.toContain("Approve");
  });

  it("keeps a superseded immutable version inspectable against its original completed job while refusing unpinned legacy history", () => {
    const historical = { ...version, status: "SUPERSEDED" };
    expect(exactTranscriptVersion(audio, historical, job)).toBe(true);
    const html = renderToStaticMarkup(
      <DerivedTranscriptView item={audio} version={historical} job={job} />,
    );
    expect(html).toContain("PRIVATE TRANSCRIPT");
    expect(html).toContain("immutable-profile-digest");
    expect(
      exactTranscriptVersion(audio, historical, { ...job, status: "RUNNING" }),
    ).toBe(false);
    expect(
      exactTranscriptVersion(
        audio,
        { ...historical, candidate_response_revision_id: null },
        job,
      ),
    ).toBe(false);
  });

  it("renders actual job transitions and a failed state without presenting a completed transcript", () => {
    const html = renderToStaticMarkup(
      <TranscriptionJobView
        job={{
          ...job,
          status: "FAILED",
          failure_category: "PROVIDER_TIMEOUT",
          transcription_version_id: null,
        }}
      />,
    );
    expect(html).toContain("Transcription status: FAILED");
    expect(html).toContain("PROVIDER_TIMEOUT");
    expect(html).toContain("QUEUED");
    expect(html).toContain("RUNNING");
    expect(html).toContain("TEST ONLY");
    expect(html).not.toContain("PRIVATE TRANSCRIPT");
  });

  it("reveals no content or execution controls after organisation or permission changes", () => {
    actor.organisationId = "org-b";
    const html = renderToStaticMarkup(
      <TranscriptionEvidencePanel item={audio} organisationId="org-a" />,
    );
    expect(html).toContain("read permission is required");
    expect(html).not.toContain("Request transcription");
    expect(html).not.toContain("PRIVATE TRANSCRIPT");
  });

  it("renders platform transcription jobs through a metadata allowlist that excludes content and storage details", () => {
    const view = metadataSections["ai-operations"].views.transcriptions;
    const html = renderToStaticMarkup(
      <MetadataTable
        caption={view.label}
        columns={view.columns}
        rows={[
          {
            id: job.id,
            status: "READY",
            test_only: true,
            transcript: "PRIVATE TRANSCRIPT",
            speech: "PRIVATE SPEECH",
            storage_key: "PRIVATE OBJECT KEY",
            private_url: "https://private/audio",
            credentials: "PRIVATE SECRET",
          },
        ]}
      />,
    );
    expect(html).toContain("job-a");
    expect(html).toContain("READY");
    expect(html).not.toMatch(/PRIVATE|https:\/\/private/);
  });
});
