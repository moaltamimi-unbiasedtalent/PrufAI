import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";
import type {
  PublishedWritingRubric,
  HumanWritingMutation,
} from "../writing-reviews/foundation-api";
import type { RubricEvidence } from "../writing-reviews/governance-ui";
import type { HumanAudioCriterionEntry } from "./human-criteria";

export type SpeakingSubmission = {
  attempt_id: string;
  exam_version_item_binding_id: string;
  candidate_response_revision_id: string;
  response_checksum: string;
  question_bank_item_version_id: string;
  standard_pack_version_id: string;
  task_spec_id: string | null;
  task_label: string;
  rubric_version_id: string | null;
  candidate_media_asset_id: string;
  audio_sha256: string;
  duration_ms: number;
};
export type PublishedSpeakingRubric = PublishedWritingRubric & {
  ai_assessment_policy?: Record<string, unknown>;
  evidence_policy: Record<string, unknown>;
};
export type HumanSpeakingOptions = {
  submissions: SpeakingSubmission[];
  rubrics: PublishedSpeakingRubric[];
};
export type AudioCriterionPayload = {
  criterion_code: string;
  level: string;
  points: string;
  rationale: string;
  evidence: Array<{
    asset_id: string;
    audio_sha256: string;
    candidate_response_revision_id: string;
    start_ms: number;
    end_ms: number;
    time_unit: "milliseconds";
  }>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* Preserve authoritative HTTP failure. */
    }
    throw new AssessmentApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
const root = (org: string) => `/api/v1/organisations/${org}`;
const action = (
  org: string,
  versionId: string,
  name: string,
  payload: unknown,
) =>
  request<HumanWritingMutation>(
    `${root(org)}/speaking-reviews/${versionId}/${name}`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );

export const speakingFoundationApi = {
  options: (org: string, signal?: AbortSignal) =>
    request<HumanSpeakingOptions>(`${root(org)}/speaking-review-options`, {
      signal,
    }),
  initialize: (
    org: string,
    source: SpeakingSubmission,
    rubric: PublishedSpeakingRubric,
    operationId: string,
  ) =>
    request<HumanWritingMutation>(
      `${root(org)}/subjective-assessments/human-speaking`,
      {
        method: "POST",
        body: JSON.stringify({
          operation_id: operationId,
          attempt_id: source.attempt_id,
          exam_version_item_binding_id: source.exam_version_item_binding_id,
          candidate_response_revision_id: source.candidate_response_revision_id,
          rubric_version_id: rubric.id,
          response_checksum: source.response_checksum,
          rubric_checksum: rubric.checksum,
        }),
      },
    ),
  propose: (
    org: string,
    versionId: string,
    checksum: string,
    criteria: AudioCriterionPayload[],
    operationId: string,
  ) =>
    action(org, versionId, "human-proposal", {
      operation_id: operationId,
      expected_checksum: checksum,
      criteria,
    }),
  approve: (
    org: string,
    versionId: string,
    checksum: string,
    operationId: string,
  ) =>
    action(org, versionId, "human-approve", {
      operation_id: operationId,
      expected_checksum: checksum,
    }),
  decide: (
    org: string,
    versionId: string,
    checksum: string,
    decision: "REJECTED" | "CHANGES_REQUESTED",
    reason: string,
    operationId: string,
  ) =>
    action(org, versionId, "human-decision", {
      operation_id: operationId,
      expected_checksum: checksum,
      decision,
      reason,
    }),
  successor: (
    org: string,
    versionId: string,
    checksum: string,
    reason: string,
    operationId: string,
  ) =>
    action(org, versionId, "human-successor", {
      operation_id: operationId,
      expected_checksum: checksum,
      reason,
    }),
};

export function eligibleSpeakingRubrics(
  source: SpeakingSubmission | undefined,
  rubrics: PublishedSpeakingRubric[],
) {
  return source?.rubric_version_id
    ? rubrics.filter(
        (rubric) =>
          rubric.id === source.rubric_version_id &&
          rubric.skill === "SPEAKING" &&
          rubric.ai_assessment_policy?.mode === "HUMAN_ONLY" &&
          rubric.evidence_policy?.mode === "AUDIO" &&
          rubric.standard_pack_version_id === source.standard_pack_version_id &&
          rubric.task_spec_id === source.task_spec_id &&
          ["PUBLISHED", "ACTIVE"].includes(rubric.status),
      )
    : [];
}

export function buildHumanAudioCriteria(
  rubric: RubricEvidence,
  values: Record<string, HumanAudioCriterionEntry>,
  audio: {
    media_asset_id: string;
    sha256: string;
    duration_ms: number;
    candidate_response_revision_id: string;
  },
): AudioCriterionPayload[] {
  if (
    !Number.isSafeInteger(audio.duration_ms) ||
    audio.duration_ms <= 0 ||
    !audio.candidate_response_revision_id ||
    !audio.sha256
  )
    throw new Error(
      "Verified duration, audio checksum and exact response revision are required.",
    );
  return rubric.criteria.map((criterion) => {
    const value = values[criterion.code];
    if (
      !value ||
      value.points.trim() === "" ||
      !value.level ||
      !value.rationale.trim()
    )
      throw new Error(`Complete the human judgment for ${criterion.code}.`);
    const points = Number(value.points);
    if (
      !Number.isFinite(points) ||
      points < 0 ||
      points > Number(criterion.max_points)
    )
      throw new Error(
        `Points for ${criterion.code} must stay within the published rubric bounds.`,
      );
    if (!rubric.performance_levels.some((level) => level.code === value.level))
      throw new Error(
        `Select a published rubric performance level for ${criterion.code}.`,
      );
    if (!value.spans.length)
      throw new Error(
        `An original audio reference is required for ${criterion.code}.`,
      );
    const evidence = value.spans.map((span) => {
      const start = Number(span.startMs),
        end = Number(span.endMs);
      if (
        !span.startMs.trim() ||
        !span.endMs.trim() ||
        !Number.isSafeInteger(start) ||
        !Number.isSafeInteger(end) ||
        start < 0 ||
        end <= start ||
        end > audio.duration_ms
      )
        throw new Error(
          `Audio spans for ${criterion.code} must use whole milliseconds within the verified recording duration.`,
        );
      return {
        asset_id: audio.media_asset_id,
        audio_sha256: audio.sha256,
        candidate_response_revision_id: audio.candidate_response_revision_id,
        start_ms: start,
        end_ms: end,
        time_unit: "milliseconds" as const,
      };
    });
    return {
      criterion_code: criterion.code,
      level: value.level,
      points: value.points,
      rationale: value.rationale.trim(),
      evidence,
    };
  });
}
