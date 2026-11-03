import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { canScoreAttempt } from "./app/institution/assessment-api";
import {
  AssessmentEvidence,
  type AssessmentComponent,
} from "./app/institution/assessment-evidence";
import {
  exactReleasedScore,
  statementMatchesRelease,
  statementProvenanceFields,
  type ReleaseArtifact,
  type ReleaseDetail,
} from "./app/institution/releases/release-api";

const component: AssessmentComponent = {
  skill: "WRITING",
  assessment_origin: "HUMAN",
  subjective_assessment_version_id: "human-approved-v1",
  subjective_assessment_checksum: "assessment-checksum",
  rubric_version_id: "published-rubric-v1",
  rubric_checksum: "rubric-checksum",
  candidate_response_revision_id: "submitted-revision-2",
  response_checksum: "response-checksum",
  task_spec_id: "writing-task",
  approved_score: "5",
  maximum_points: "6",
};
const release: ReleaseDetail = {
  id: "release-v1",
  result_version_id: "result-v2",
  scoring_run_id: "run-after-approval",
  standard_pack_version_id: "pack-v1",
  attempt_id: "submitted-attempt",
  candidate_user_id: "candidate-a",
  classification: "PRACTICE",
  status: "RELEASED",
  released_at: "2026-11-03T10:00:00Z",
  checksum: "release-checksum",
  result_checksum: "result-checksum",
  result: {
    result_version_id: "result-v2",
    result_checksum: "result-checksum",
    raw_awarded_points: "5",
    raw_maximum_points: "6",
    outcome: "UNDETERMINED",
    assessment_components: [component],
  },
};
const statement: ReleaseArtifact = {
  id: "statement-v1",
  status: "ISSUED",
  issued_at: "2026-11-03T10:01:00Z",
  checksum: "statement-checksum",
  payload: {
    statement_type: "Statement of Results",
    classification: "PRACTICE",
    issued_by_user_id: "institution-issuer",
    result_release_id: release.id,
    result_version_id: release.result_version_id,
    scoring_run_id: release.scoring_run_id,
    standard_pack_version_id: release.standard_pack_version_id,
    attempt_id: release.attempt_id,
    candidate_user_id: release.candidate_user_id,
    release_checksum: release.checksum,
    result_checksum: release.result_checksum,
    outcome: "UNDETERMINED",
    assessment_components: [component],
  },
};

describe("submitted Writing scoring handoff", () => {
  it("permits recovery from an incomplete result without offering rescoring for authoritative reviewed or released results", () => {
    const submitted = { status: "SUBMITTED", result: null };
    expect(canScoreAttempt(submitted, ["scoring.run"])).toBe(true);
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
    expect(
      canScoreAttempt(submitted, ["result.read", "subjective.review"]),
    ).toBe(false);
    for (const status of ["IN_PROGRESS", "PREPARED", "EXPIRED", "VOIDED"])
      expect(canScoreAttempt({ status, result: null }, ["scoring.run"])).toBe(
        false,
      );
  });
});

describe("exact released Writing evidence", () => {
  it("uses the released version and checksum rather than a different or latest result", () => {
    expect(exactReleasedScore(release)?.assessment_components).toEqual([
      component,
    ]);
    expect(
      exactReleasedScore({ ...release, result_version_id: "another-version" }),
    ).toBeNull();
    expect(
      exactReleasedScore({
        ...release,
        result_checksum: "changed-result-checksum",
      }),
    ).toBeNull();
  });

  it("renders honest HUMAN provenance and marks while excluding reviewer-private or AI metadata", () => {
    const html = renderToStaticMarkup(
      <AssessmentEvidence
        components={[
          {
            ...component,
            rationale: "PRIVATE RATIONALE",
            evidence: [{ quote: "PRIVATE WRITING QUOTE" }],
            confidence: "FAKE CONFIDENCE",
          } as AssessmentComponent,
        ]}
      />,
    );
    expect(html).toContain("Writing assessment");
    expect(html).toContain("Assessment origin: HUMAN");
    expect(html).toContain(
      "Human-only assessment; no AI proposal or model attribution.",
    );
    expect(html).toContain("5 / 6");
    expect(html).toContain("submitted-revision-2");
    expect(html).not.toMatch(
      /PRIVATE RATIONALE|PRIVATE WRITING QUOTE|FAKE CONFIDENCE/,
    );
  });

  it("does not invent a HUMAN assessment for objective or historical unspecified records", () => {
    expect(renderToStaticMarkup(<AssessmentEvidence />)).toBe("");
    expect(renderToStaticMarkup(<AssessmentEvidence components={[]} />)).toBe(
      "",
    );
    const html = renderToStaticMarkup(
      <AssessmentEvidence
        components={[{ ...component, assessment_origin: "UNSPECIFIED" }]}
      />,
    );
    expect(html).toContain("UNSPECIFIED");
    expect(html).not.toContain("Human-only assessment");
  });

  it("requires statement provenance to match every supplied exact release pin", () => {
    expect(statementMatchesRelease(statement, release)).toBe(true);
    for (const key of [
      "result_release_id",
      "result_version_id",
      "scoring_run_id",
      "standard_pack_version_id",
      "attempt_id",
      "candidate_user_id",
      "release_checksum",
      "result_checksum",
    ])
      expect(
        statementMatchesRelease(
          {
            ...statement,
            payload: { ...statement.payload, [key]: "different" },
          },
          release,
        ),
      ).toBe(false);
    expect(
      statementMatchesRelease({ ...statement, payload: undefined }, release),
    ).toBe(false);
  });

  it("displays only public scalar statement fields even if a response contains additional internal data", () => {
    const fields = statementProvenanceFields({
      ...statement,
      payload: {
        ...statement.payload,
        reviewer_private_notes: "PRIVATE NOTES",
        rationale: "PRIVATE RATIONALE",
        candidate_text: "PRIVATE WRITING",
        ai_run: { prompt: "PRIVATE PROMPT" },
        raw_awarded_points: { unsafe: "PRIVATE NESTED FIELD" },
      },
    });
    const shown = JSON.stringify(fields);
    expect(shown).toContain("PRACTICE");
    expect(shown).toContain("run-after-approval");
    expect(shown).toContain("UNDETERMINED");
    expect(shown).not.toMatch(
      /PRIVATE|rationale|candidate text|assessment components|ai run/,
    );
    // Historical objective statements remain readable without invented Writing provenance.
    expect(
      statementProvenanceFields({
        id: "historical",
        status: "ISSUED",
        payload: { classification: "PRACTICE", scoring_run_id: "old-run" },
      }),
    ).toEqual([
      { label: "classification", value: "PRACTICE" },
      { label: "scoring run id", value: "old-run" },
    ]);
  });
});
