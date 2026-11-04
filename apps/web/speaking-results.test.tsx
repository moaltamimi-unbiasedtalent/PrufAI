import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  canScoreAttempt,
  type ScoringReadiness,
} from "./app/institution/assessment-api";
import {
  AssessmentEvidence,
  type AssessmentComponent,
} from "./app/institution/assessment-evidence";

const ready: ScoringReadiness = {
  state: "READY",
  has_speaking: true,
  can_score: true,
  reasons: [],
  components: [
    {
      binding_id: "speaking-binding",
      skill: "SPEAKING",
      status: "APPROVED",
      assessment_version_id: "speaking-approved-v2",
      checksum: "approved-checksum",
    },
  ],
};
const submitted = {
  status: "SUBMITTED",
  result: null,
  scoring_readiness: ready,
};

describe("server-authorized Speaking scoring readiness", () => {
  it("requires a ready submitted attempt and the scoring permission", () => {
    expect(canScoreAttempt(submitted, ["scoring.run"])).toBe(true);
    expect(
      canScoreAttempt(submitted, ["result.read", "subjective.review"]),
    ).toBe(false);
    for (const status of ["PREPARED", "IN_PROGRESS", "VOIDED", "EXPIRED"])
      expect(canScoreAttempt({ ...submitted, status }, ["scoring.run"])).toBe(
        false,
      );
  });

  it("does not offer current Speaking scoring for missing, pending or rejected human evidence", () => {
    for (const status of [
      "MISSING",
      "REVIEW_REQUIRED",
      "IN_REVIEW",
      "REJECTED",
      "CHANGES_REQUESTED",
    ])
      expect(
        canScoreAttempt(
          {
            ...submitted,
            scoring_readiness: {
              ...ready,
              state: "INCOMPLETE",
              can_score: false,
              components: [{ ...ready.components[0]!, status }],
            },
          },
          ["scoring.run"],
        ),
      ).toBe(false);
  });

  it("fails closed when readiness state or authority disagree", () => {
    for (const state of ["INCOMPLETE", "CONFLICT"] as const)
      expect(
        canScoreAttempt(
          { ...submitted, scoring_readiness: { ...ready, state } },
          ["scoring.run"],
        ),
      ).toBe(false);
    expect(
      canScoreAttempt(
        { ...submitted, scoring_readiness: { ...ready, can_score: false } },
        ["scoring.run"],
      ),
    ).toBe(false);
  });

  it("blocks a mixed attempt while any required subjective component remains unresolved", () => {
    expect(
      canScoreAttempt(
        {
          ...submitted,
          scoring_readiness: {
            ...ready,
            state: "INCOMPLETE",
            can_score: false,
            reasons: [
              {
                code: "PENDING_ASSESSMENT",
                skill: "WRITING",
                message: "Writing approval is pending.",
              },
            ],
            components: [
              ...ready.components,
              { binding_id: "writing", skill: "WRITING", status: "IN_REVIEW" },
            ],
          },
        },
        ["scoring.run"],
      ),
    ).toBe(false);
  });

  it("allows incomplete-result recovery without directly rescoring authoritative historical results", () => {
    expect(
      canScoreAttempt({ ...submitted, result: { status: "INCOMPLETE" } }, [
        "scoring.run",
      ]),
    ).toBe(true);
    for (const status of [
      "REVIEW_REQUIRED",
      "APPROVED",
      "RELEASED",
      "SUPERSEDED",
    ])
      expect(
        canScoreAttempt({ ...submitted, result: { status } }, ["scoring.run"]),
      ).toBe(false);
  });

  it("preserves the established objective and Writing incomplete-score workflow", () => {
    expect(
      canScoreAttempt({ status: "SUBMITTED", result: null }, ["scoring.run"]),
    ).toBe(true);
    expect(
      canScoreAttempt(
        {
          ...submitted,
          scoring_readiness: {
            ...ready,
            has_speaking: false,
            state: "INCOMPLETE",
            can_score: false,
          },
        },
        ["scoring.run"],
      ),
    ).toBe(true);
  });
});

const speaking: AssessmentComponent = {
  skill: "SPEAKING",
  assessment_origin: "HUMAN",
  assessment_mode: "HUMAN_ONLY",
  evidence_mode: "AUDIO",
  subjective_assessment_version_id: "speaking-approved-v2",
  subjective_assessment_checksum: "approved-speaking-checksum",
  rubric_version_id: "published-speaking-rubric",
  rubric_checksum: "rubric-checksum",
  candidate_response_revision_id: "submitted-audio-revision",
  response_checksum: "submitted-checksum",
  task_spec_id: "original-speaking-task",
  approved_score: "5",
  maximum_points: "6",
};

describe("candidate-safe Speaking result provenance", () => {
  it("shows the exact approved HUMAN_ONLY/AUDIO component without reviewer or private media payloads", () => {
    const html = renderToStaticMarkup(
      <AssessmentEvidence
        components={[
          {
            ...speaking,
            rationale: "PRIVATE RATIONALE",
            evidence: [
              { start_ms: 0, end_ms: 1000, comment: "PRIVATE COMMENT" },
            ],
            storage_key: "PRIVATE OBJECT KEY",
            audio_sha256: "PRIVATE AUDIO CHECKSUM",
            private_url: "https://private.invalid/object",
            transcript: "FABRICATED TRANSCRIPT",
            ai_proposal: "FABRICATED AI",
          } as AssessmentComponent,
        ]}
      />,
    );
    expect(html).toContain("Speaking assessment");
    expect(html).toContain("Assessment origin: HUMAN");
    expect(html).toContain("Assessment mode: HUMAN_ONLY");
    expect(html).toContain("Evidence mode: AUDIO");
    expect(html).toContain("5 / 6");
    expect(html).toContain("speaking-approved-v2");
    expect(html).toContain("submitted-audio-revision");
    expect(html).not.toMatch(
      /PRIVATE|FABRICATED|private.invalid|start_ms|end_ms/,
    );
  });

  it("keeps both required Writing and Speaking components visible", () => {
    const html = renderToStaticMarkup(
      <AssessmentEvidence
        components={[
          {
            ...speaking,
            skill: "WRITING",
            subjective_assessment_version_id: "writing-approved",
            evidence_mode: "TEXT",
          },
          speaking,
        ]}
      />,
    );
    expect(html).toContain("Writing assessment");
    expect(html).toContain("Speaking assessment");
    expect(html).toContain("writing-approved");
    expect(html).toContain("speaking-approved-v2");
  });

  it("does not manufacture mode or human provenance for historical records", () => {
    const html = renderToStaticMarkup(
      <AssessmentEvidence
        components={[
          {
            ...speaking,
            assessment_mode: undefined,
            evidence_mode: undefined,
            assessment_origin: "UNSPECIFIED",
          },
        ]}
      />,
    );
    expect(html).toContain("Speaking assessment");
    expect(html).toContain("UNSPECIFIED");
    expect(html).not.toMatch(/HUMAN_ONLY|Evidence mode|Human-only assessment/);
  });
});
