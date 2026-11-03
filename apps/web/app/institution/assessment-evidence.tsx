import React from "react";
import css from "./assessment.module.css";

export type AssessmentComponent = {
  skill: string;
  assessment_origin: string;
  subjective_assessment_version_id: string;
  subjective_assessment_checksum: string;
  rubric_version_id: string;
  rubric_checksum: string;
  candidate_response_revision_id: string | null;
  response_checksum: string;
  task_spec_id: string | null;
  approved_score: string;
  maximum_points: string;
};

export function AssessmentEvidence({
  components,
}: {
  components?: AssessmentComponent[];
}) {
  if (!components?.length) return null;
  return (
    <section className={css.card} aria-label="Assessment provenance">
      {components.map((component) => (
        <article key={component.subjective_assessment_version_id}>
          <h2>
            {component.skill === "WRITING"
              ? "Writing assessment"
              : `${component.skill} assessment`}
          </h2>
          <p>Assessment origin: {component.assessment_origin}</p>
          {component.assessment_origin === "HUMAN" && (
            <p>Human-only assessment; no AI proposal or model attribution.</p>
          )}
          <p>
            Approved component score: {component.approved_score} /{" "}
            {component.maximum_points}
          </p>
          <details>
            <summary>Exact approved assessment provenance</summary>
            <dl className={css.provenance}>
              <dt>Subjective assessment version</dt>
              <dd>{component.subjective_assessment_version_id}</dd>
              <dt>Assessment checksum</dt>
              <dd>{component.subjective_assessment_checksum}</dd>
              <dt>Published rubric version</dt>
              <dd>{component.rubric_version_id}</dd>
              <dt>Rubric checksum</dt>
              <dd>{component.rubric_checksum}</dd>
              <dt>Submitted response revision</dt>
              <dd>
                {component.candidate_response_revision_id ?? "Unavailable"}
              </dd>
              <dt>Response checksum</dt>
              <dd>{component.response_checksum}</dd>
              <dt>Task specification</dt>
              <dd>{component.task_spec_id ?? "Unavailable"}</dd>
            </dl>
          </details>
        </article>
      ))}
    </section>
  );
}
