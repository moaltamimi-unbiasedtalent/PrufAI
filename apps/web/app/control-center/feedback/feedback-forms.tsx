"use client";

import React, { useState } from "react";
import {
  evaluationApi,
  type EvaluationRunDetail,
} from "../evaluation/evaluation-api";
import { exactPin } from "../evaluation/regressions/regression-api";
import css from "../control-center.module.css";
import formCss from "../../institution/assessment.module.css";
import {
  categories,
  changeCategories,
  feedbackApi,
  hypotheses,
  pinOf,
  severities,
  targetTypes,
  type Classification,
  type FeedbackItem,
  type Pattern,
  type Proposal,
} from "./feedback-api";

export function formValue(data: FormData, field: string): string {
  return String(data.get(field) ?? "").trim();
}

export function classification(data: FormData): Classification {
  const category = categories.find((value) => value === data.get("category"));
  const severity = severities.find((value) => value === data.get("severity"));
  if (!category || !severity)
    throw new Error("Select a category and severity.");
  return { category, severity };
}

export function ClassificationFields({
  initial,
}: {
  initial?: Classification;
}) {
  return (
    <>
      <label>
        Feedback category
        <select name="category" required defaultValue={initial?.category ?? ""}>
          <option value="" disabled>
            Select a category
          </option>
          {categories.map((category) => (
            <option key={category}>{category}</option>
          ))}
        </select>
      </label>
      <label>
        Feedback severity
        <select name="severity" required defaultValue={initial?.severity ?? ""}>
          <option value="" disabled>
            Select a severity
          </option>
          {severities.map((severity) => (
            <option key={severity}>{severity}</option>
          ))}
        </select>
      </label>
    </>
  );
}

export type Execute = (action: () => Promise<void>) => void;
export type Operation = (path: string, data: object) => string;

export function MeasuredIntake({
  execute,
  busy,
  operation,
  onSaved,
}: {
  execute: Execute;
  busy: boolean;
  operation: Operation;
  onSaved: (item: FeedbackItem) => void;
}) {
  const [source, setSource] = useState<EvaluationRunDetail | null>(null);
  const measured = (source?.failures ?? []).flatMap((failure) => {
    if (!failure.result_id || !failure.result_checksum) return [];
    const result = { id: failure.result_id, checksum: failure.result_checksum };
    return (failure.failed_checks ?? []).map((check) => ({
      key: `${result.id}:${check}`,
      label: `${failure.case_id} · ${check}`,
      result,
      check,
    }));
  });
  return (
    <section className={css.card} aria-label="Evaluation failure intake">
      <h2>Record a measured evaluation failure</h2>
      <form
        className={formCss.form}
        onSubmit={(event) => {
          event.preventDefault();
          const id = formValue(new FormData(event.currentTarget), "run");
          execute(async () => {
            setSource(null);
            setSource(await evaluationApi.run(id));
          });
        }}
      >
        <label>
          Source evaluation run ID <input name="run" required />
        </label>
        <button className={css.secondary} disabled={busy}>
          Load measured failures
        </button>
      </form>
      {source && (
        <p>
          Run {source.run.id} · {source.run.evidence_class} ·{" "}
          {source.run.execution_class}
        </p>
      )}
      {source && measured.length === 0 && (
        <p>No measured failed checks are available for this run.</p>
      )}
      {source?.run.checksum && measured.length > 0 && (
        <form
          key={source.run.id}
          className={formCss.form}
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            execute(async () => {
              const selected = measured.find(
                ({ key }) => key === data.get("failure"),
              );
              if (!selected || !source.run.checksum)
                throw new Error("Select an exact measured failure.");
              const intake = {
                ...classification(data),
                source: {
                  kind: "EVALUATION_FAILURE" as const,
                  run: { id: source.run.id, checksum: source.run.checksum },
                  result: selected.result,
                  failed_check: selected.check,
                },
              };
              onSaved(
                await feedbackApi.intake({
                  ...intake,
                  operation_id: operation("intake", intake),
                }),
              );
            });
          }}
        >
          <label>
            Measured feedback check
            <select name="failure" required defaultValue="">
              <option value="" disabled>
                Select a failed check
              </option>
              {measured.map(({ key, label }) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <ClassificationFields />
          <button className={css.button} disabled={busy}>
            Record feedback
          </button>
        </form>
      )}
    </section>
  );
}

export function DecisionForm<Action extends string>({
  title,
  actions,
  reasons,
  receipt,
  initialClassification,
  busy,
  execute,
  save,
}: {
  title: string;
  actions: Action[];
  reasons: Record<Action, readonly string[]>;
  receipt: string;
  initialClassification?: Classification;
  busy: boolean;
  execute: Execute;
  save: (
    data: {
      expected_checksum: string;
      action: Action;
      reason_code: string;
    } & Partial<Classification>,
  ) => Promise<void>;
}) {
  const [choice, setChoice] = useState(actions[0]);
  const action = actions.includes(choice) ? choice : actions[0];
  if (!action) return null;
  return (
    <form
      className={formCss.form}
      onSubmit={(event) => {
        event.preventDefault();
        const fields = new FormData(event.currentTarget);
        const reason = formValue(fields, "reason");
        execute(async () => {
          if (!reasons[action].includes(reason))
            throw new Error("Select an allowed reason.");
          await save({
            expected_checksum: receipt,
            action,
            reason_code: reason,
            ...(action === "TRIAGE" && initialClassification
              ? classification(fields)
              : {}),
          });
        });
      }}
    >
      <label>
        {title} action
        <select
          value={action}
          onChange={(event) => {
            const next = actions.find((value) => value === event.target.value);
            if (next) setChoice(next);
          }}
          disabled={busy}
        >
          {actions.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
      </label>
      <label>
        {title} reason
        <select name="reason" required key={action}>
          {reasons[action].map((reason) => (
            <option key={reason}>{reason}</option>
          ))}
        </select>
      </label>
      {action === "TRIAGE" && initialClassification && (
        <ClassificationFields initial={initialClassification} />
      )}
      <button className={css.button} disabled={busy}>
        Record {title.toLowerCase()} decision
      </button>
    </form>
  );
}

function PinFields({
  name,
  label,
  required = false,
}: {
  name: string;
  label: string;
  required?: boolean;
}) {
  return (
    <>
      <label>
        {label} ID <input name={`${name}Id`} required={required} />
      </label>
      <label>
        {label} checksum <input name={`${name}Checksum`} required={required} />
      </label>
    </>
  );
}

function formPin(data: FormData, name: string) {
  const id = formValue(data, `${name}Id`);
  const checksum = formValue(data, `${name}Checksum`);
  return id || checksum ? exactPin(id, checksum) : undefined;
}

export function ProposalForm({
  pattern,
  execute,
  busy,
  operation,
  onSaved,
}: {
  pattern: Pattern;
  execute: Execute;
  busy: boolean;
  operation: Operation;
  onSaved: (proposal: Proposal) => void;
}) {
  const targets = pattern.artifact_pins.filter((pin) =>
    targetTypes.includes(pin.type),
  );
  const pinKey = (pin: (typeof targets)[number]) => `${pin.type}:${pin.id}`;
  if (!targets.length)
    return <p>This pattern has no supported exact target version.</p>;
  return (
    <form
      className={formCss.form}
      onSubmit={(event) => {
        event.preventDefault();
        const fields = new FormData(event.currentTarget);
        execute(async () => {
          const target = targets.find(
            (value) => pinKey(value) === fields.get("target"),
          );
          const change = changeCategories.find(
            (value) => value === fields.get("change"),
          );
          const hypothesis = hypotheses.find(
            (value) => value === fields.get("hypothesis"),
          );
          if (!target || !change || !hypothesis)
            throw new Error(
              "Select a target, change category and improvement hypothesis.",
            );
          const data = {
            pattern: pinOf(pattern),
            target: { type: target.type, ...pinOf(target) },
            change_category: change,
            hypothesis_code: hypothesis,
            suite_version: formPin(fields, "suite"),
            baseline_run: formPin(fields, "baseline"),
            candidate_run: formPin(fields, "candidate"),
            predecessor: formPin(fields, "predecessor"),
          };
          onSaved(
            await feedbackApi.propose({
              ...data,
              operation_id: operation("proposal", data),
            }),
          );
        });
      }}
    >
      <label>
        Exact target artifact
        <select name="target" required defaultValue="">
          <option value="" disabled>
            Select a pinned version
          </option>
          {targets.map((target) => (
            <option key={pinKey(target)} value={pinKey(target)}>
              {target.type} · {target.id} · {target.checksum}
            </option>
          ))}
        </select>
      </label>
      <label>
        Proposed change
        <select name="change">
          {changeCategories.map((change) => (
            <option key={change}>{change}</option>
          ))}
        </select>
      </label>
      <label>
        Improvement hypothesis
        <select name="hypothesis">
          {hypotheses.map((hypothesis) => (
            <option key={hypothesis}>{hypothesis}</option>
          ))}
        </select>
      </label>
      <details>
        <summary>Optional evaluation and predecessor receipts</summary>
        <PinFields name="suite" label="Regression suite version" />
        <PinFields name="baseline" label="Baseline run" />
        <PinFields name="candidate" label="Candidate run" />
        <PinFields name="predecessor" label="Previous proposal version" />
      </details>
      <button className={css.button} disabled={busy}>
        Create improvement proposal
      </button>
    </form>
  );
}
