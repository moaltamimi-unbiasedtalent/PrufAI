"use client";

import React, { useEffect, useRef, useState } from "react";
import formCss from "../../../institution/assessment.module.css";
import css from "../../control-center.module.css";
import { ErrorNotice, MetadataTable } from "../../metadata";
import { pinOf, type Pin } from "../feedback-api";
import { formValue } from "../feedback-forms";
import {
  nonproductionEnvironment,
  promotionApi,
  type PromotionView,
} from "./promotion-api";

function pinLabel(pin?: Pin | null) {
  return pin ? `${pin.id} · ${pin.checksum}` : "None";
}

export default function PromotionPanel({ id }: { id: string }) {
  const [view, setView] = useState<PromotionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const operations = useRef(new Map<string, string>());

  function message(cause: unknown) {
    return cause instanceof Error
      ? cause.message
      : "Promotion evidence is unavailable.";
  }

  useEffect(() => {
    let current = true;
    setError(null);
    setLoading(true);
    void promotionApi
      .read(id)
      .then((value) => {
        if (current) setView(value);
      })
      .catch((cause: unknown) => {
        if (current) setError(message(cause));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [id, reload]);

  function operation(action: string, data: object) {
    const key = JSON.stringify([action, data]);
    const existing = operations.current.get(key) ?? crypto.randomUUID();
    operations.current.set(key, existing);
    return {
      operation_id: existing,
      environment: nonproductionEnvironment,
    } as const;
  }

  function execute(action: () => Promise<unknown>) {
    if (pending.current || loading) return;
    pending.current = true;
    setError(null);
    setBusy(true);
    void action()
      .then(async () => {
        setView(await promotionApi.read(id));
      })
      .catch((cause: unknown) => setError(message(cause)))
      .finally(() => {
        pending.current = false;
        setBusy(false);
      });
  }

  const disabled = busy || loading;
  const selection = view?.current_selection;
  const readiness = view?.readiness;
  const canSelect =
    readiness &&
    selection?.selected?.candidate.id !== id &&
    view.allowed_actions.includes("AUTHORIZE_AND_SELECT");
  const canRevert = selection && view.allowed_actions.includes("REVERT");
  const canClear =
    selection && view.allowed_actions.includes("CLEAR_SELECTION");
  const canValidate = selection && view.allowed_actions.includes("VALIDATE");

  function rationale(
    event: React.FormEvent<HTMLFormElement>,
    action: "select" | "revert" | "clear",
  ) {
    event.preventDefault();
    const value = formValue(new FormData(event.currentTarget), "rationale");
    if (value.length < 8 || value.length > 500) {
      setError("Enter an explicit rationale of 8–500 characters.");
      return;
    }
    if (
      selection &&
      ((action === "revert" && canRevert) || (action === "clear" && canClear))
    ) {
      const data = {
        expected_selection: pinOf(selection),
        rationale: value,
        ...(action === "clear" ? { clear_selection: true } : {}),
      };
      execute(() =>
        promotionApi.revert(id, { ...data, ...operation(action, data) }),
      );
    } else if (action === "select" && canSelect) {
      const data = {
        readiness: pinOf(readiness),
        expected_selection: selection ? pinOf(selection) : null,
        rationale: value,
      };
      execute(() =>
        promotionApi.select(id, { ...data, ...operation("select", data) }),
      );
    }
  }

  return (
    <section className={css.card} aria-label="Nonproduction prompt promotion">
      <h3>Nonproduction prompt promotion</h3>
      {error && (
        <ErrorNotice
          message={error}
          retry={() => setReload((value) => value + 1)}
        />
      )}
      {loading && <p role="status">Loading environment-scoped selection…</p>}
      {busy && <p role="status">Recording the promotion action…</p>}
      {view && (
        <>
          <dl className={css.health}>
            <dt>Environment</dt>
            <dd>{view.environment}</dd>
            <dt>Prompt definition</dt>
            <dd>{view.prompt_definition_id}</dd>
            <dt>Pinned published baseline</dt>
            <dd>{pinLabel(view.baseline)}</dd>
            <dt>Human readiness receipt</dt>
            <dd>{pinLabel(readiness)}</dd>
            <dt>Current selection receipt</dt>
            <dd>{pinLabel(selection)}</dd>
            <dt>Selected nonproduction PromptVersion</dt>
            <dd>
              {selection?.selected
                ? pinLabel(selection.selected.prompt)
                : "No prompt selected for isolated validation"}
            </dd>
            <dt>Production readiness</dt>
            <dd>{view.production_readiness}</dd>
            <dt>Production blocker</dt>
            <dd>{view.production_blocker}</dd>
          </dl>
          {view.blockers.length > 0 && (
            <ul aria-label="Promotion blockers">
              {view.blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          )}
          {!selection?.selected && !view.history.length && (
            <p>No nonproduction selection has been recorded.</p>
          )}
          {(
            [
              [
                "select",
                canSelect,
                "Promotion rationale",
                "Authorize and select for nonproduction",
              ],
              [
                "revert",
                canRevert,
                "Reversion rationale",
                "Revert nonproduction selection",
              ],
              [
                "clear",
                canClear,
                "Clear-selection rationale",
                "Clear nonproduction selection",
              ],
            ] as const
          )
            .filter(([, allowed]) => allowed)
            .map(([action, , label, button]) => (
              <form
                key={`${action}:${selection?.checksum ?? readiness?.checksum}`}
                className={formCss.form}
                onSubmit={(event) => rationale(event, action)}
              >
                {action === "clear" && (
                  <p>
                    The previous prompt cannot be restored. Clearing leaves no
                    prompt selected for isolated validation.
                  </p>
                )}
                <label>
                  {label}
                  <textarea
                    name="rationale"
                    minLength={8}
                    maxLength={500}
                    required
                    disabled={disabled}
                  />
                </label>
                <button className={css.button} disabled={disabled}>
                  {button}
                </button>
              </form>
            ))}
          {canValidate && (
            <button
              className={css.button}
              disabled={disabled}
              onClick={() => {
                const data = { selection: pinOf(selection) };
                execute(() =>
                  promotionApi.validate(id, {
                    ...data,
                    ...operation("validate", data),
                  }),
                );
              }}
            >
              Run isolated Writing validation
            </button>
          )}
          {view.history.length > 0 && (
            <MetadataTable
              caption="Nonproduction selection history"
              columns={[
                ["revision", "Revision"],
                ["status", "Status"],
                ["environment", "Environment"],
                ["selected", "Selected prompt"],
                ["previous", "Previous prompt"],
                ["actor_id", "Human actor"],
                ["created_at", "Recorded at"],
                ["receipt", "Receipt"],
                ["reason_code", "Decision reason"],
                ["reversion_mode", "Reversion mode"],
              ]}
              rows={view.history.map((item) => ({
                ...item,
                selected: pinLabel(item.selected?.prompt),
                previous: pinLabel(item.previous?.prompt),
                receipt: pinLabel(item),
              }))}
            />
          )}
          {view.validations.length > 0 && (
            <MetadataTable
              caption="Isolated Writing validation evidence"
              columns={[
                ["status", "Status"],
                ["execution_class", "Execution classification"],
                ["model_execution", "Model execution"],
                ["prompt", "Exact resolved prompt"],
                ["selection", "Selection receipt"],
                ["case_id", "Case"],
                ["validated_criteria_count", "Validated criteria"],
                ["request_checksum", "Request checksum"],
                ["output_checksum", "Output checksum"],
                ["receipt", "Validation receipt"],
              ]}
              rows={view.validations.flatMap((validation) =>
                validation.cases.map((item) => ({
                  ...item,
                  status: validation.status,
                  execution_class: validation.execution_class,
                  model_execution: validation.model_execution,
                  prompt: pinLabel(validation.prompt),
                  selection: pinLabel(validation.selection),
                  receipt: pinLabel(validation),
                })),
              )}
            />
          )}
        </>
      )}
    </section>
  );
}
