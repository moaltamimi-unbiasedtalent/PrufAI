"use client";

import { useState, type FormEvent } from "react";
import { lidOutcomeLabel, lidPurposeLabel } from "../../../../lib/lid-practice";
import css from "../../control-center.module.css";
import {
  packPath,
  standardsRequest,
  type FoundationDraft,
} from "../../standards-api";
import { effectiveDate } from "../dtz/dtz-api";
import { sourceLink } from "../dtz/blueprint-detail";
import { lidPath, type LidBlueprint, type LidPreview } from "./lid-api";

export function LidBlueprintDetail({
  draft,
  canPreview,
  canPublish,
}: {
  draft: LidBlueprint;
  canPreview: boolean;
  canPublish: boolean;
}) {
  const [rawCorrect, setRawCorrect] = useState("");
  const [asOf, setAsOf] = useState("");
  const [preview, setPreview] = useState<LidPreview | null>(null);
  const [pack, setPack] = useState<FoundationDraft | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  async function work(action: () => Promise<void>) {
    setWorking(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "LiD operation failed.",
      );
    } finally {
      setWorking(false);
    }
  }

  function previewRules(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPreview(null);
    void work(async () => {
      setPreview(
        await standardsRequest<LidPreview>(
          `${lidPath}/blueprints/${draft.id}/preview`,
          "POST",
          {
            expected_pack_checksum: draft.checksum,
            as_of: asOf,
            raw_correct: Number(rawCorrect),
          },
        ),
      );
    });
  }

  const { structure } = draft;
  const fixtureJurisdictions = draft.synthetic_fixture_jurisdictions ?? [];
  return (
    <article className={css.card} aria-label="Exact LiD blueprint version">
      <h2>LiD blueprint {draft.version_label}</h2>
      <p className={css.notice}>{draft.notice}</p>
      <dl className={css.health}>
        <dt>Assessment family</dt>
        <dd>{draft.assessment_family}</dd>
        <dt>Exam family</dt>
        <dd>{draft.exam_family}</dd>
        <dt>Language</dt>
        <dd>{draft.language}</dd>
        <dt>CEFR level</dt>
        <dd>{draft.cefr_level ?? "Not applicable"}</dd>
        <dt>Classifications</dt>
        <dd>{draft.allowed_classifications.join(", ")}</dd>
        <dt>Content maturity</dt>
        <dd>{draft.content_maturity}</dd>
        <dt>Review</dt>
        <dd>{draft.review_status}</dd>
        <dt>Standard Pack status</dt>
        <dd>{pack?.status ?? draft.status}</dd>
        <dt>Official provider authorization</dt>
        <dd>
          {draft.official_provider_authorization
            ? "Established"
            : "Not established"}
        </dd>
        <dt>Exact version</dt>
        <dd>
          <code>{draft.id}</code>
        </dd>
        <dt>Checksum</dt>
        <dd>
          <code>{draft.checksum}</code>
        </dd>
      </dl>
      <section aria-label="LiD practice form">
        <h3>State-aware practice form</h3>
        <p>
          {structure.total_items} questions · {structure.general_items} general
          · {structure.state_items} selected-state ·{" "}
          {structure.options_per_item} options per question ·{" "}
          {structure.correct_options_per_item} correct option ·{" "}
          {structure.time_limit_seconds / 60} minutes
        </p>
        <p>Selection policy: {draft.selection_policy_version}</p>
        <h4>Separate question pools</h4>
        <ul>
          {draft.question_pools.map((pool) => (
            <li key={pool.scope}>
              <code>{pool.scope}</code> · {pool.scope_label} ·{" "}
              {pool.eligible_catalogue_count} reference catalogue entries
            </li>
          ))}
        </ul>
        <p>
          Reference catalogue counts describe the published structure. The
          synthetic practice pool is separate.
        </p>
        {fixtureJurisdictions.length > 0 && (
          <p>
            TEST ONLY practice fixture coverage:{" "}
            {fixtureJurisdictions
              .map((state) => `${state.name} (${state.code})`)
              .join(", ")}
          </p>
        )}
        <details>
          <summary>Bundesland mapping ({draft.jurisdictions.length})</summary>
          <ul aria-label="Bundesland codes">
            {draft.jurisdictions.map((state) => (
              <li key={state.code}>
                <code>{state.code}</code> · {state.name}
              </li>
            ))}
          </ul>
        </details>
      </section>
      <section aria-label="LiD legal claim ledger">
        <h3>Source claim and conflict ledger</h3>
        {draft.claim_ledger.map((group) => (
          <details key={group.code}>
            <summary>
              {group.code} · {group.status}
            </summary>
            {group.interpretation && <p>{group.interpretation}</p>}
            <ul>
              {group.claims.map((claim) => (
                <li
                  key={`${claim.source_version_id}:${claim.locator}:${claim.scope}`}
                >
                  <p>
                    {sourceLink(
                      claim.reference_url ?? claim.source_url,
                      claim.locator,
                    )}{" "}
                    · {claim.scope}
                  </p>
                  <p>Recorded value: {JSON.stringify(claim.claimed_value)}</p>
                  <p>
                    Applicable from:{" "}
                    {claim.applicable_from ?? "Not established"}
                  </p>
                  <p>
                    Source version: <code>{claim.source_version_id}</code>
                  </p>
                  <p>
                    Metadata checksum:{" "}
                    <code>{claim.source_metadata_checksum}</code>
                  </p>
                </li>
              ))}
            </ul>
          </details>
        ))}
      </section>
      <section aria-label="LiD source pins and rights">
        <h3>Exact source bindings and rights</h3>
        <ul>
          {draft.source_bindings.map((binding) => (
            <li key={binding.key}>
              <h4>{binding.title}</h4>
              <p>
                {binding.rights_status} ·{" "}
                {binding.available ? "Current" : "Unavailable"}
              </p>
              <p>
                Ingestion {binding.ingestion_allowed ? "allowed" : "restricted"}{" "}
                · Redistribution{" "}
                {binding.redistribution_allowed ? "allowed" : "restricted"} ·{" "}
                Question reuse{" "}
                {binding.question_reuse_allowed ? "allowed" : "restricted"} ·{" "}
                Image reuse{" "}
                {binding.image_reuse_allowed ? "allowed" : "restricted"}
              </p>
              <p>
                <code>{binding.document_version_id}</code> ·{" "}
                <code>{binding.source_metadata_checksum}</code>
              </p>
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="LiD legal rule previews">
        <h3>Separate draft rule purposes</h3>
        <ul>
          {draft.rules.map((rule) => (
            <li key={rule.id}>
              <h4>{lidPurposeLabel[rule.purpose]}</h4>
              <p>
                {rule.threshold}/{structure.total_items} · {rule.status} ·{" "}
                {rule.review_status}
              </p>
              <p>
                {rule.name} · {rule.version_label} ·{" "}
                {effectiveDate(rule.legal_effective_from)}
              </p>
              <p>
                <code>{rule.id}</code> · <code>{rule.checksum}</code>
              </p>
            </li>
          ))}
        </ul>
        {canPreview && (
          <form className={css.filters} onSubmit={previewRules}>
            <label>
              Synthetic correct count
              <input
                required
                type="number"
                min={0}
                max={structure.total_items}
                value={rawCorrect}
                disabled={working}
                onChange={(event) => {
                  setPreview(null);
                  setRawCorrect(event.target.value);
                }}
              />
            </label>
            <label>
              Legal as-of date
              <input
                required
                type="date"
                value={asOf}
                disabled={working}
                onChange={(event) => {
                  setPreview(null);
                  setAsOf(event.target.value);
                }}
              />
            </label>
            <button
              type="submit"
              className={css.button}
              disabled={working || rawCorrect === "" || asOf === ""}
            >
              Run LiD numerical preview
            </button>
          </form>
        )}
        {preview && (
          <div role="status" aria-label="LiD numerical preview result">
            {preview.contexts.map((context) => (
              <div key={context.purpose}>
                <p>
                  {lidPurposeLabel[context.purpose]} · {context.threshold}/
                  {structure.total_items} ·{" "}
                  {lidOutcomeLabel[context.numerical_outcome]}
                </p>
                <p>
                  Rule: {context.rule_version_label ?? "Unavailable"} · Legal
                  effective date: {effectiveDate(context.legal_effective_from)}
                </p>
                <ul aria-label="Exact rule citations">
                  {context.citations.map((citation) => (
                    <li
                      key={`${citation.source_version_id}:${citation.locator}`}
                    >
                      {sourceLink(
                        citation.reference_url ?? citation.source_url,
                        citation.locator,
                      )}{" "}
                      · <code>{citation.source_version_id}</code>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <p>Official outcome: {preview.official_outcome}</p>
            <p>
              Administrative recognition: {preview.administrative_recognition}
            </p>
          </div>
        )}
      </section>
      <section aria-label="LiD publication blockers">
        <h3>Expert review and publication blockers</h3>
        <ul>
          {draft.publication_blockers.map((blocker) => (
            <li key={blocker.code}>
              <strong>{blocker.code}</strong> · {blocker.message}
            </li>
          ))}
        </ul>
        <button
          type="button"
          className={css.secondary}
          disabled={working}
          onClick={() =>
            void work(async () => {
              setPack(
                await standardsRequest<FoundationDraft>(
                  `${packPath}/${draft.id}`,
                ),
              );
            })
          }
        >
          Open exact LiD Standard Pack version
        </button>
        {canPublish && (
          <button
            type="button"
            className={css.secondary}
            disabled={working}
            onClick={() =>
              void work(async () => {
                setPack(
                  await standardsRequest<FoundationDraft>(
                    `${packPath}/${draft.id}/publish`,
                    "POST",
                  ),
                );
              })
            }
          >
            Request LiD pack publication
          </button>
        )}
        {pack && (
          <p role="status">
            Standard Pack {pack.id} · {pack.status}
          </p>
        )}
      </section>
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
    </article>
  );
}
