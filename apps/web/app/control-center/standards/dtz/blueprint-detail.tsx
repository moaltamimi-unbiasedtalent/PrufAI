"use client";

import { useState } from "react";
import css from "../../control-center.module.css";
import {
  packPath,
  standardsRequest,
  type FoundationDraft,
} from "../../standards-api";
import {
  dtzPath,
  effectiveDate,
  overallB1Rule,
  type DtzRule,
  type DtzBlueprint,
  type DtzPreview,
  type DtzSection,
} from "./dtz-api";

const previewDomains = [
  ["listening_reading", "Listening / Reading"],
  ["writing", "Writing"],
  ["speaking", "Speaking"],
] as const;
const previewLevels = [
  ["BELOW_A2", "Below A2"],
  ["A2", "A2"],
  ["B1", "B1"],
  ["UNDETERMINED", "Undetermined"],
] as const;

export function sourceLink(
  url: string | null | undefined,
  label = "Official source",
) {
  return url && url.startsWith("https://") ? (
    <a href={url}>{label}</a>
  ) : (
    <span>Source link unavailable</span>
  );
}

function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "Not recorded";
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((item) => typeof item === "string"))
    return value.join(", ");
  return JSON.stringify(value);
}

function SectionDetails({ section }: { section: DtzSection }) {
  const unsupported =
    section.interaction_fidelity === "UNSUPPORTED_FOR_FULL_FIDELITY";
  return (
    <section className={css.card} aria-label={section.skill}>
      <h4>{section.name}</h4>
      {unsupported && (
        <p className={css.notice}>
          UNSUPPORTED_FOR_FULL_FIDELITY · Live examiner or partner interaction
          required.
        </p>
      )}
      <dl className={css.health}>
        <dt>Skill</dt>
        <dd>{section.skill}</dd>
        <dt>Scoring domain</dt>
        <dd>{section.scoring_domain}</dd>
        <dt>Scored items</dt>
        <dd>{section.item_total ?? "Criterion based"}</dd>
        <dt>Draft timing</dt>
        <dd>
          {section.time_limit_seconds === null
            ? "No separate duration established"
            : `${section.time_is_approximate ? "Approx. " : ""}${section.time_limit_seconds / 60} min`}
        </dd>
      </dl>
      {section.groups.map((group) => (
        <details key={group.code}>
          <summary>{group.name}</summary>
          <ul>
            {group.tasks.map((task) => (
              <li key={task.code}>
                <strong>{task.name}</strong> ({task.code})
                <dl className={css.health}>
                  <dt>Task kind</dt>
                  <dd>{task.item_kind}</dd>
                  <dt>Item count</dt>
                  <dd>{task.item_count ?? "Criterion based"}</dd>
                  <dt>Response</dt>
                  <dd>{task.response_mode}</dd>
                  <dt>Interaction</dt>
                  <dd>{task.interaction}</dd>
                  <dt>Evidence</dt>
                  <dd>{task.evidence_mode}</dd>
                  <dt>Interaction fidelity</dt>
                  <dd>{task.fidelity}</dd>
                  {task.task_choice_count != null && (
                    <>
                      <dt>Prompt choices</dt>
                      <dd>{task.task_choice_count}</dd>
                    </>
                  )}
                  {task.synthetic_example && (
                    <>
                      <dt>{task.synthetic_example.label}</dt>
                      <dd>
                        {task.synthetic_example.prompt}
                        {task.synthetic_example.options && (
                          <ul>
                            {task.synthetic_example.options.map((option) => (
                              <li key={option}>{option}</li>
                            ))}
                          </ul>
                        )}
                      </dd>
                    </>
                  )}
                </dl>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function DraftPreview({ draft }: { draft: DtzBlueprint }) {
  const rule = draft.rules.find(
    (candidate) => candidate.code === overallB1Rule,
  );
  const [preview, setPreview] = useState<DtzPreview | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const objective = preview?.objective;

  return (
    <section aria-labelledby="dtz-preview-heading">
      <h3 id="dtz-preview-heading">Technical rule preview</h3>
      <p>Evaluate the pinned draft rule with synthetic domain levels.</p>
      {rule ? (
        <form
          className={css.filters}
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const domains = Object.fromEntries(
              previewDomains.flatMap(([name]) => {
                const level = data.get(name);
                return typeof level === "string" && level
                  ? [[name, level]]
                  : [];
              }),
            );
            const objectiveAnswer = data.get("objective_answer");
            setWorking(true);
            setError("");
            void standardsRequest<DtzPreview>(
              `${dtzPath}/blueprints/${draft.id}/preview`,
              "POST",
              {
                expected_pack_checksum: draft.checksum,
                rule_version_id: rule.id,
                expected_rule_checksum: rule.checksum,
                as_of: data.get("as_of"),
                domains,
                ...(typeof objectiveAnswer === "string" && objectiveAnswer
                  ? { objective_answer: objectiveAnswer }
                  : {}),
              },
            )
              .then(setPreview)
              .catch((cause: unknown) => {
                setPreview(null);
                setError(
                  cause instanceof Error ? cause.message : "Preview failed.",
                );
              })
              .finally(() => setWorking(false));
          }}
        >
          <label>
            Applicability date
            <input name="as_of" type="date" required />
          </label>
          {previewDomains.map(([name, label]) => (
            <label key={name}>
              {label}
              <select name={name}>
                <option value="">Missing judgment</option>
                {previewLevels.map(([value, text]) => (
                  <option key={value} value={value}>
                    {text}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {objective && (
            <label>
              {objective.label}
              <span>{objective.prompt}</span>
              <select
                name="objective_answer"
                aria-label="Synthetic objective answer"
              >
                <option value="">Missing response</option>
                {objective.options.map((option) => (
                  <option key={option.option_id} value={option.option_id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button className={css.button} disabled={working}>
            Run technical preview
          </button>
        </form>
      ) : (
        <p>Review required. This blueprint has no pinned overall B1 rule.</p>
      )}
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      {preview && !working && (
        <>
          <output className={css.notice} aria-label="Technical preview result">
            {preview.execution_class} · {preview.outcome} · {preview.reason}
          </output>
          {objective && (
            <p aria-label="Technical objective result">
              {objective.status} · {objective.awarded_points ?? "No score"} of{" "}
              {objective.maximum_points} points · Combined draft maximum{" "}
              {objective.combined_maximum_points}
            </p>
          )}
        </>
      )}
    </section>
  );
}

function PackInspection({
  draft,
  canPublish,
}: {
  draft: DtzBlueprint;
  canPublish: boolean;
}) {
  const [pack, setPack] = useState<FoundationDraft | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const path = `${packPath}/${draft.id}`;

  async function readPack() {
    setPack(await standardsRequest<FoundationDraft>(path));
  }

  async function work(action: () => Promise<void>) {
    setError("");
    setWorking(true);
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Pack operation failed.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <section aria-labelledby="dtz-pack-heading">
      <h3 id="dtz-pack-heading">Exact Standard Pack</h3>
      <button
        type="button"
        className={css.secondary}
        disabled={working}
        onClick={() => void work(readPack)}
      >
        Open exact Standard Pack version
      </button>
      {pack && (
        <article aria-label="DTZ Standard Pack version">
          <dl className={css.health}>
            <dt>Pack version</dt>
            <dd>{pack.id}</dd>
            <dt>Pack lifecycle</dt>
            <dd>{pack.status}</dd>
            <dt>Pack checksum</dt>
            <dd>{pack.checksum ?? pack.configuration_checksum}</dd>
          </dl>
          {canPublish && (
            <button
              type="button"
              className={css.secondary}
              disabled={working}
              onClick={() =>
                void work(async () => {
                  await standardsRequest(`${path}/publish`, "POST");
                  await readPack();
                })
              }
            >
              Request DTZ pack publication
            </button>
          )}
        </article>
      )}
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
    </section>
  );
}

function ArtifactSources({ sources }: { sources: DtzRule["sources"] }) {
  return (
    <ul>
      {sources.map((source) => (
        <li key={source.source_version_id}>
          {source.reference_kind} · {sourceLink(source.source_url)} ·{" "}
          <code>{source.source_version_id}</code> ·{" "}
          <code>{source.metadata_checksum}</code>
        </li>
      ))}
    </ul>
  );
}

export function BlueprintDetail({
  draft,
  canPublish,
  canPreview,
}: {
  draft: DtzBlueprint;
  canPublish: boolean;
  canPreview: boolean;
}) {
  return (
    <article className={css.card} aria-label="Exact DTZ blueprint version">
      <h2>{draft.version_label}</h2>
      <p className={css.notice}>{draft.notice}</p>
      <dl className={css.health}>
        <dt>Standard Pack version</dt>
        <dd>{draft.id}</dd>
        <dt>Lifecycle</dt>
        <dd>{draft.status}</dd>
        <dt>Review state</dt>
        <dd>{draft.review_status}</dd>
        <dt>Assessment family</dt>
        <dd>{draft.assessment_family}</dd>
        <dt>Exam family</dt>
        <dd>{draft.exam_family}</dd>
        <dt>Language</dt>
        <dd>{draft.language}</dd>
        <dt>Scaled range</dt>
        <dd>{draft.supported_levels.join("–")}</dd>
        <dt>Classification</dt>
        <dd>{draft.allowed_classifications.join(", ")}</dd>
        <dt>Pack checksum</dt>
        <dd>{draft.checksum}</dd>
        <dt>Provenance checksum</dt>
        <dd>{draft.manifest_checksum}</dd>
        <dt>Content maturity</dt>
        <dd>{draft.content_maturity}</dd>
        <dt>Structural validation</dt>
        <dd>{draft.structural_validation}</dd>
        <dt>Regulatory candidate effective from</dt>
        <dd>{effectiveDate(draft.regulatory_candidate_effective_from)}</dd>
      </dl>

      <section aria-labelledby="dtz-skills-heading">
        <h3 id="dtz-skills-heading">Skill blueprint</h3>
        <div className={css.grid}>
          {draft.sections.map((section) => (
            <SectionDetails key={section.skill} section={section} />
          ))}
        </div>
      </section>

      <section aria-labelledby="dtz-claim-heading">
        <h3 id="dtz-claim-heading">Source claim and conflict ledger</h3>
        {draft.claim_ledger.map((group) => (
          <details key={group.code}>
            <summary>
              {group.code} · {group.status}
            </summary>
            <dl className={css.health}>
              <dt>Claim category</dt>
              <dd>{group.category}</dd>
              <dt>Possible interpretation</dt>
              <dd>{group.interpretation ?? "Not recorded"}</dd>
              <dt>Reviewing expert</dt>
              <dd>{group.reviewing_expert ?? "Not assigned"}</dd>
              <dt>Resolution evidence</dt>
              <dd>{evidenceValue(group.resolution_evidence)}</dd>
            </dl>
            <ul>
              {group.claims.map((claim) => (
                <li key={`${claim.source_key}/${claim.locator}`}>
                  <strong>{claim.source_key}</strong> ·{" "}
                  {sourceLink(claim.source_url, "Exact source edition")}
                  {claim.reference_url &&
                    claim.reference_url !== claim.source_url &&
                    claim.reference_url.startsWith("https://") && (
                      <> · {sourceLink(claim.reference_url, "Cited section")}</>
                    )}
                  <dl className={css.health}>
                    <dt>Section reference</dt>
                    <dd>{claim.locator}</dd>
                    <dt>Claimed value</dt>
                    <dd>{evidenceValue(claim.claimed_value)}</dd>
                    <dt>Scope</dt>
                    <dd>{claim.scope}</dd>
                    <dt>Applicable from (cited edition)</dt>
                    <dd>{claim.applicable_from ?? "Not established"}</dd>
                    <dt>Source version</dt>
                    <dd>{claim.source_version_id}</dd>
                    <dt>Source metadata checksum</dt>
                    <dd>{claim.source_metadata_checksum}</dd>
                  </dl>
                </li>
              ))}
            </ul>
          </details>
        ))}
      </section>

      <section aria-labelledby="dtz-bindings-heading">
        <h3 id="dtz-bindings-heading">Source bindings</h3>
        <ul>
          {draft.source_bindings.map((binding) => (
            <li key={binding.document_version_id}>
              <strong>{binding.title}</strong> · {binding.publisher} ·{" "}
              {binding.version_label} · {sourceLink(binding.source_url)}
              <dl className={css.health}>
                <dt>Exact version</dt>
                <dd>{binding.document_version_id}</dd>
                <dt>Metadata checksum</dt>
                <dd>{binding.source_metadata_checksum}</dd>
                <dt>Verified on</dt>
                <dd>{binding.verified_on}</dd>
                <dt>Rights</dt>
                <dd>
                  {binding.legal_review_status} · {binding.rights_status}
                </dd>
                <dt>Retention</dt>
                <dd>{binding.retention_policy ?? "Not recorded"}</dd>
                <dt>Available for the pinned draft</dt>
                <dd>{binding.available ? "Yes" : "No"}</dd>
              </dl>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="dtz-scoring-heading">
        <h3 id="dtz-scoring-heading">Source-pinned draft scoring rules</h3>
        <ul>
          {draft.rules.map((rule) => (
            <li key={rule.id}>
              <details>
                <summary>
                  {rule.name} · {rule.status} · {rule.review_status}
                </summary>
                <dl className={css.health}>
                  <dt>Rule</dt>
                  <dd>
                    {rule.code} · {rule.version_label}
                  </dd>
                  <dt>Version</dt>
                  <dd>{rule.id}</dd>
                  <dt>Checksum</dt>
                  <dd>{rule.checksum}</dd>
                  <dt>Effective from</dt>
                  <dd>{effectiveDate(rule.effective_from)}</dd>
                  <dt>Effective until</dt>
                  <dd>{effectiveDate(rule.effective_until)}</dd>
                  <dt>Rule expression</dt>
                  <dd>
                    <code>{evidenceValue(rule.expression)}</code>
                  </dd>
                </dl>
                <ArtifactSources sources={rule.sources} />
              </details>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="dtz-rubrics-heading">
        <h3 id="dtz-rubrics-heading">Writing and Speaking rubrics</h3>
        <ul>
          {draft.rubrics.map((rubric) => (
            <li key={rubric.id}>
              <details>
                <summary>
                  {rubric.skill} · {rubric.status} · {rubric.review_status}
                </summary>
                <dl className={css.health}>
                  <dt>Version</dt>
                  <dd>{rubric.id}</dd>
                  <dt>Checksum</dt>
                  <dd>{rubric.checksum}</dd>
                  <dt>Assessment mode</dt>
                  <dd>{rubric.assessment_mode}</dd>
                  <dt>Evidence policy</dt>
                  <dd>{evidenceValue(rubric.evidence_policy)}</dd>
                </dl>
                <ArtifactSources sources={rubric.sources} />
              </details>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="dtz-publication-heading">
        <h3 id="dtz-publication-heading">
          Human review and publication blockers
        </h3>
        <ul>
          {draft.publication_blockers.map((blocker) => (
            <li key={blocker.code}>{blocker.message}</li>
          ))}
        </ul>
      </section>

      {canPreview && <DraftPreview draft={draft} />}
      <PackInspection draft={draft} canPublish={canPublish} />
    </article>
  );
}
