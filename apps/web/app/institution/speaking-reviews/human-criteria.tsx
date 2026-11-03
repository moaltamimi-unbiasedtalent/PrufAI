"use client";

import React from "react";
import type {
  RubricCriterion,
  RubricLevel,
} from "../writing-reviews/governance-ui";
import css from "../assessment.module.css";

export type AudioSpanEntry = { startMs: string; endMs: string };
export type HumanAudioCriterionEntry = {
  points: string;
  level: string;
  rationale: string;
  spans: AudioSpanEntry[];
};

export function HumanAudioCriterionFields({
  criteria,
  levels,
  durationMs,
  values,
  disabled,
  onChange,
}: {
  criteria: RubricCriterion[];
  levels: RubricLevel[];
  durationMs: number;
  values: Record<string, HumanAudioCriterionEntry>;
  disabled: boolean;
  onChange: (code: string, value: HumanAudioCriterionEntry) => void;
}) {
  return (
    <>
      {criteria.map((criterion) => {
        const value = values[criterion.code] ?? {
          points: "",
          level: "",
          rationale: "",
          spans: [{ startMs: "", endMs: "" }],
        };
        const update = (patch: Partial<HumanAudioCriterionEntry>) =>
          onChange(criterion.code, { ...value, ...patch });
        return (
          <fieldset
            key={criterion.code}
            disabled={disabled}
            className={css.card}
          >
            <legend>{criterion.code} · human audio judgment</legend>
            <label>
              Points for {criterion.code}
              <input
                required
                type="number"
                min="0"
                max={criterion.max_points}
                step="any"
                value={value.points}
                onChange={(event) => update({ points: event.target.value })}
              />
            </label>
            <label>
              Performance level for {criterion.code}
              <select
                required
                value={value.level}
                onChange={(event) => update({ level: event.target.value })}
              >
                <option value="">Select a published rubric level</option>
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
            <p>
              Cite the original recording using milliseconds from its verified
              beginning. The verified duration is {durationMs} ms. Each end must
              follow its start and stay within this duration.
            </p>
            {value.spans.map((span, index) => (
              <div key={index} className={css.card}>
                <label>
                  Audio start ms for {criterion.code} span {index + 1}
                  <input
                    required
                    type="number"
                    min="0"
                    max={durationMs}
                    step="1"
                    value={span.startMs}
                    onChange={(event) =>
                      update({
                        spans: value.spans.map((item, position) =>
                          position === index
                            ? { ...item, startMs: event.target.value }
                            : item,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Audio end ms for {criterion.code} span {index + 1}
                  <input
                    required
                    type="number"
                    min="1"
                    max={durationMs}
                    step="1"
                    value={span.endMs}
                    onChange={(event) =>
                      update({
                        spans: value.spans.map((item, position) =>
                          position === index
                            ? { ...item, endMs: event.target.value }
                            : item,
                        ),
                      })
                    }
                  />
                </label>
                {value.spans.length > 1 && (
                  <button
                    type="button"
                    onClick={() =>
                      update({
                        spans: value.spans.filter(
                          (_, position) => position !== index,
                        ),
                      })
                    }
                  >
                    Remove {criterion.code} span {index + 1}
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              onClick={() =>
                update({ spans: [...value.spans, { startMs: "", endMs: "" }] })
              }
            >
              Add audio span for {criterion.code}
            </button>
            <label>
              Private human rationale for {criterion.code}
              <textarea
                required
                rows={3}
                maxLength={2000}
                value={value.rationale}
                onChange={(event) => update({ rationale: event.target.value })}
              />
            </label>
          </fieldset>
        );
      })}
    </>
  );
}
