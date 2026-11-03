"use client";

import React from "react";
import css from "../assessment.module.css";

export type RubricCriterion = {
  code: string;
  max_points: string | number;
  weight?: string | number;
  descriptors?: Record<string, unknown>;
};
export type RubricLevel = {
  code: string;
  name?: string;
  label?: string;
  description?: string;
};
export type RubricEvidence = {
  id: string;
  checksum: string;
  criteria: RubricCriterion[];
  performance_levels: RubricLevel[];
  aggregation_policy: Record<string, unknown>;
  evidence_policy: Record<string, unknown>;
};
export type HumanCriterionEntry = {
  points: string;
  level: string;
  evidence: string;
  reason: string;
};

export function GovernanceError({
  error,
  retry,
}: {
  error: string;
  retry: () => void;
}) {
  return (
    <div className={css.error} role="alert">
      <p>{error}</p>
      <button type="button" onClick={retry}>
        Refresh governed evidence
      </button>
    </div>
  );
}

export function RubricEvidenceView({ rubric }: { rubric: RubricEvidence }) {
  return (
    <section className={css.card} aria-labelledby="rubric-evidence-heading">
      <h2 id="rubric-evidence-heading">Exact rubric and criteria</h2>
      <dl className={css.provenance}>
        <dt>Rubric version</dt>
        <dd>{rubric.id}</dd>
        <dt>Rubric checksum</dt>
        <dd>{rubric.checksum}</dd>
      </dl>
      {rubric.criteria.map((criterion) => (
        <article key={criterion.code} className={css.card}>
          <h3>{criterion.code}</h3>
          <p>Maximum points: {criterion.max_points}</p>
          {criterion.weight !== undefined && (
            <p>Declared weight: {criterion.weight}</p>
          )}
          <dl className={css.provenance}>
            {Object.entries(criterion.descriptors ?? {}).map(
              ([level, descriptor]) => (
                <React.Fragment key={level}>
                  <dt>{level}</dt>
                  <dd>
                    {typeof descriptor === "object"
                      ? JSON.stringify(descriptor)
                      : String(descriptor)}
                  </dd>
                </React.Fragment>
              ),
            )}
          </dl>
        </article>
      ))}
      <h3>Allowed performance levels</h3>
      <ul>
        {rubric.performance_levels.map((level) => (
          <li key={level.code}>
            {level.code}
            {level.label || level.name ? ` — ${level.label ?? level.name}` : ""}
            {level.description ? `: ${level.description}` : ""}
          </li>
        ))}
      </ul>
      <details>
        <summary>Declared aggregation and evidence policy</summary>
        <pre className={css.evidence}>
          {JSON.stringify(
            {
              aggregation_policy: rubric.aggregation_policy,
              evidence_policy: rubric.evidence_policy,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}

export function HumanCriterionFields({
  criteria,
  levels,
  values,
  disabled,
  evidenceRequired,
  onChange,
}: {
  criteria: RubricCriterion[];
  levels: RubricLevel[];
  values: Record<string, HumanCriterionEntry>;
  disabled: boolean;
  evidenceRequired: boolean;
  onChange: (code: string, value: HumanCriterionEntry) => void;
}) {
  return (
    <>
      {criteria.map((criterion) => {
        const value = values[criterion.code] ?? {
          points: "",
          level: "",
          evidence: "",
          reason: "",
        };
        const update = (key: keyof HumanCriterionEntry, next: string) =>
          onChange(criterion.code, { ...value, [key]: next });
        return (
          <fieldset
            key={criterion.code}
            disabled={disabled}
            className={css.card}
          >
            <legend>{criterion.code} · human criterion judgment</legend>
            <label>
              Points for {criterion.code}
              <input
                required
                type="number"
                min="0"
                max={criterion.max_points}
                step="any"
                value={value.points}
                onChange={(event) => update("points", event.target.value)}
              />
            </label>
            <label>
              Performance level for {criterion.code}
              <select
                required
                value={value.level}
                onChange={(event) => update("level", event.target.value)}
              >
                <option value="">Select a rubric level</option>
                {levels.map((level) => (
                  <option key={level.code} value={level.code}>
                    {level.code}
                    {level.label || level.name
                      ? ` — ${level.label ?? level.name}`
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Exact response quote for {criterion.code}
              {evidenceRequired ? " (required)" : " (optional)"}
              <textarea
                required={evidenceRequired}
                rows={3}
                maxLength={2000}
                value={value.evidence}
                onChange={(event) => update("evidence", event.target.value)}
              />
            </label>
            <label>
              Human rationale for {criterion.code}
              <textarea
                required
                rows={3}
                maxLength={2000}
                value={value.reason}
                onChange={(event) => update("reason", event.target.value)}
              />
            </label>
          </fieldset>
        );
      })}
    </>
  );
}
