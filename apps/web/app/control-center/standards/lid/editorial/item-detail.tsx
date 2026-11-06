import { useState, type FormEvent } from "react";
import css from "../../../control-center.module.css";
import { sourceLink } from "../../dtz/blueprint-detail";
import {
  evidenceLabel,
  type EditorialItem,
  type EditorialQualification,
  type ItemRevision,
  type ReviewStage,
} from "./editorial-api";

export type ItemDecision = {
  stage: ReviewStage;
  decision: "APPROVE" | "REJECT";
  qualification_id: string;
  qualification_checksum: string;
  reason: string;
  rights?: {
    basis_reference: string;
    rights_holder_status:
      | "CONFIRMED_RIGHTSHOLDER"
      | "NO_EXCLUSIVE_RIGHTS_ASSERTED";
    rights_holder?: string;
    practice_use_allowed: boolean;
    display_allowed: boolean;
    redistribution_allowed: boolean;
    derivative_use_allowed: boolean;
  };
};

const reviewStages = [
  ["FACTUAL", "Factual review"],
  ["ASSESSMENT", "Assessment review"],
  ["RIGHTS", "Original content rights review"],
] as const;

function ReviewDecisionForm({
  stage,
  label,
  qualifications,
  checklist,
  busy,
  onDecision,
}: {
  stage: ReviewStage;
  label: string;
  qualifications: EditorialQualification[];
  checklist: string[];
  busy: boolean;
  onDecision: (decision: ItemDecision) => void;
}) {
  const [decision, setDecision] = useState<"APPROVE" | "REJECT">("APPROVE");
  const [holder, setHolder] = useState<
    "CONFIRMED_RIGHTSHOLDER" | "NO_EXCLUSIVE_RIGHTS_ASSERTED"
  >("NO_EXCLUSIVE_RIGHTS_ASSERTED");
  const needsRights = stage === "RIGHTS" && decision === "APPROVE";
  const defaultQualification = qualifications[0]?.id ?? "";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const qualification = qualifications.find(
      (row) => row.id === fields.get("qualification"),
    );
    const reason = String(fields.get("reason") ?? "").trim();
    if (!qualification || !reason) return;
    const payload: ItemDecision = {
      stage,
      decision,
      qualification_id: qualification.id,
      qualification_checksum: qualification.checksum,
      reason,
    };
    if (needsRights) {
      payload.rights = {
        basis_reference: String(fields.get("basis_reference") ?? "").trim(),
        rights_holder_status: holder,
        practice_use_allowed: fields.has("practice_use_allowed"),
        display_allowed: fields.has("display_allowed"),
        redistribution_allowed: fields.has("redistribution_allowed"),
        derivative_use_allowed: fields.has("derivative_use_allowed"),
      };
      if (holder === "CONFIRMED_RIGHTSHOLDER") {
        payload.rights.rights_holder = String(
          fields.get("rights_holder") ?? "",
        ).trim();
      }
    }
    onDecision(payload);
  }

  return (
    <form className={css.filters} aria-label={label} onSubmit={submit}>
      <fieldset disabled={busy}>
        <legend>{label}</legend>
        <ul aria-label="Review checklist">
          {checklist.map((instruction) => (
            <li key={instruction}>{instruction}</li>
          ))}
        </ul>
        <label>
          Approved qualification
          <select
            name="qualification"
            defaultValue={defaultQualification}
            required
          >
            {qualifications.map((row) => (
              <option key={row.id} value={row.id}>
                {evidenceLabel(row.evidence_class)} · {row.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Decision
          <select
            value={decision}
            onChange={(event) =>
              setDecision(
                event.target.value === "REJECT" ? "REJECT" : "APPROVE",
              )
            }
          >
            <option value="APPROVE">Approve</option>
            <option value="REJECT">Reject</option>
          </select>
        </label>
        <label>
          Private review reason
          <textarea name="reason" required maxLength={4000} rows={3} />
        </label>
        {needsRights && (
          <>
            <label>
              Rights evidence reference
              <input
                name="basis_reference"
                required
                maxLength={200}
                pattern={"[A-Za-z0-9][A-Za-z0-9_.:\\/\\-]{0,199}"}
                title="Use a stable reference with letters, digits, or _.:/-"
              />
            </label>
            <label>
              Rights holder status
              <select
                value={holder}
                onChange={(event) =>
                  setHolder(
                    event.target.value === "CONFIRMED_RIGHTSHOLDER"
                      ? "CONFIRMED_RIGHTSHOLDER"
                      : "NO_EXCLUSIVE_RIGHTS_ASSERTED",
                  )
                }
              >
                <option value="NO_EXCLUSIVE_RIGHTS_ASSERTED">
                  No exclusive rights asserted
                </option>
                <option value="CONFIRMED_RIGHTSHOLDER">
                  Confirmed rights holder
                </option>
              </select>
            </label>
            {holder === "CONFIRMED_RIGHTSHOLDER" && (
              <label>
                Confirmed rights holder
                <input name="rights_holder" required maxLength={240} />
              </label>
            )}
            <label>
              <input name="practice_use_allowed" type="checkbox" required />{" "}
              Practice use allowed
            </label>
            <label>
              <input name="display_allowed" type="checkbox" required /> Display
              allowed
            </label>
            <label>
              <input name="redistribution_allowed" type="checkbox" />{" "}
              Redistribution allowed
            </label>
            <label>
              <input name="derivative_use_allowed" type="checkbox" /> Derivative
              use allowed
            </label>
          </>
        )}
        <button
          className={css.button}
          type="submit"
          disabled={!defaultQualification}
        >
          Record {stage.toLowerCase()} decision
        </button>
      </fieldset>
    </form>
  );
}

function SuccessorForm({
  item,
  busy,
  onRevise,
}: {
  item: EditorialItem;
  busy: boolean;
  onRevise: (revision: ItemRevision) => void;
}) {
  const [replaceSource, setReplaceSource] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const value = (name: string) => String(fields.get(name) ?? "").trim();
    onRevise({
      prompt: value("prompt"),
      options: fields.getAll("option").map((value) => String(value).trim()),
      correct_index: Number(fields.get("correct_index")),
      rationale: value("rationale"),
      ...(replaceSource && {
        replacement_source: {
          version_id: value("source_version_id"),
          metadata_checksum: value("source_checksum"),
          reference_url: value("reference_url"),
          locator: value("locator"),
          fact: value("fact"),
          last_verified: value("last_verified"),
          recheck_after: value("recheck_after"),
        },
      }),
    });
  }

  return (
    <form
      className={css.filters}
      aria-label="Create revised editorial draft"
      onSubmit={submit}
    >
      <fieldset disabled={busy}>
        <legend>Revise rejected item</legend>
        <p>
          A successor starts a new review cycle with no inherited decisions.
        </p>
        <label>
          Revised question
          <textarea
            name="prompt"
            defaultValue={item.prompt}
            required
            minLength={10}
            maxLength={700}
            rows={3}
          />
        </label>
        {item.options.map(({ option_id, display_text }, index) => (
          <label key={option_id}>
            Option {index + 1}
            <input
              name="option"
              defaultValue={display_text}
              required
              maxLength={250}
            />
          </label>
        ))}
        <label>
          Proposed correct option
          <select
            name="correct_index"
            defaultValue={item.options.findIndex(
              ({ option_id }) => option_id === item.correct_option_id,
            )}
          >
            {item.options.map(({ option_id }, index) => (
              <option key={option_id} value={index}>
                Option {index + 1}
              </option>
            ))}
          </select>
        </label>
        <label>
          Revised answer rationale
          <textarea
            name="rationale"
            defaultValue={item.rationale}
            required
            minLength={10}
            maxLength={1500}
            rows={3}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={replaceSource}
            onChange={(event) => setReplaceSource(event.target.checked)}
          />
          Cite a new reviewed edition of this source
        </label>
        {replaceSource && (
          <>
            <p>
              Use the exact reviewed version and checksum from the Source
              Registry.
            </p>
            <label>
              Reviewed source version ID
              <input name="source_version_id" required maxLength={36} />
            </label>
            <label>
              Source metadata checksum
              <input name="source_checksum" required pattern="[a-f0-9]{64}" />
            </label>
            <label>
              Factual reference URL
              <input name="reference_url" type="url" required />
            </label>
            <label>
              Citation locator
              <input name="locator" required />
            </label>
            <label>
              Supported fact
              <textarea name="fact" required rows={3} />
            </label>
            <label>
              Last verified
              <input name="last_verified" type="date" required />
            </label>
            <label>
              Recheck after
              <input name="recheck_after" type="date" required />
            </label>
          </>
        )}
        <button type="submit" className={css.button}>
          Create revised editorial draft
        </button>
      </fieldset>
    </form>
  );
}

export function EditorialItemDetail({
  item,
  busy,
  onSubmit,
  onDecision,
  onRevise,
  onInspect,
}: {
  item: EditorialItem;
  busy: boolean;
  onSubmit: () => void;
  onDecision: (decision: ItemDecision) => void;
  onRevise: (revision: ItemRevision) => void;
  onInspect: (version: string) => void;
}) {
  const { source, claim } = item;

  return (
    <article className={css.card} aria-label="Exact editorial item">
      <h3>
        {item.code} · {item.version_label}
      </h3>
      <p>{item.prompt}</p>
      <ol aria-label="Draft answer options">
        {item.options.map(({ option_id, display_text }) => (
          <li key={option_id}>
            {display_text}
            {option_id === item.correct_option_id && (
              <>
                {" · "}
                <strong>Proposed answer</strong>
              </>
            )}
          </li>
        ))}
      </ol>
      <p>{item.rationale}</p>
      <dl className={css.health}>
        <dt>Draft provenance</dt>
        <dd>{item.authorship}</dd>
        <dt>Review state</dt>
        <dd>{evidenceLabel(item.maturity)}</dd>
        <dt>Exact item version</dt>
        <dd>{item.item_version_id}</dd>
        <dt>Content checksum</dt>
        <dd>{item.checksum}</dd>
        <dt>Provenance checksum</dt>
        <dd>{item.provenance_checksum}</dd>
        <dt>Answer key checksum</dt>
        <dd>{item.key_checksum}</dd>
        <dt>Original content rights</dt>
        <dd>{evidenceLabel(item.rights_status)}</dd>
        <dt>Jurisdiction</dt>
        <dd>
          {item.scope === "GENERAL_DE"
            ? "Germany · general pool"
            : item.jurisdiction}
        </dd>
      </dl>
      <section aria-label="Factual source evidence">
        <h4>Factual source evidence</h4>
        <dl className={css.health}>
          <dt>Publisher</dt>
          <dd>{source.publisher}</dd>
          <dt>Reference</dt>
          <dd>
            {sourceLink(claim.reference_url, source.title)} · {claim.locator}
          </dd>
          <dt>Source version</dt>
          <dd>
            {source.version_label} · {source.version_id}
          </dd>
          <dt>Source checksum</dt>
          <dd>{source.metadata_checksum}</dd>
          <dt>Claim</dt>
          <dd>
            {claim.claim_code} · {claim.fact}
          </dd>
          <dt>Answer support</dt>
          <dd>{item.rationale}</dd>
          <dt>Source rights decision</dt>
          <dd>{evidenceLabel(source.rights_status)}</dd>
          {source.rights_decision_checksum && (
            <>
              <dt>Source rights decision checksum</dt>
              <dd>{source.rights_decision_checksum}</dd>
            </>
          )}
          <dt>Publication date</dt>
          <dd>{source.publication_date ?? "No publication date recorded"}</dd>
          <dt>Effective date</dt>
          <dd>
            {claim.legal_effective_from ??
              source.effective_from ??
              "No effective date recorded"}
          </dd>
          {claim.effective_date_basis && (
            <>
              <dt>Effective date basis</dt>
              <dd>
                {claim.effective_date_basis}
                {claim.effective_date_reference && (
                  <>
                    {" "}
                    ·{" "}
                    {sourceLink(
                      claim.effective_date_reference,
                      "Effective date source",
                    )}
                  </>
                )}
              </dd>
            </>
          )}
          <dt>Retrieved</dt>
          <dd>{source.retrieved_at}</dd>
          <dt>Last verified</dt>
          <dd>{claim.last_verified}</dd>
          <dt>Recheck</dt>
          <dd>
            {claim.recheck_after} · {claim.recheck_trigger}
          </dd>
        </dl>
      </section>
      <section aria-label="Item review blockers">
        <h4>Item review blockers</h4>
        {item.blockers.length ? (
          <ul>
            {item.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        ) : (
          <p>No blockers recorded for this exact item version.</p>
        )}
      </section>
      {item.allowed_actions.includes("SUBMIT") && (
        <button
          className={css.button}
          disabled={busy}
          onClick={onSubmit}
          type="button"
        >
          Submit item for factual review
        </button>
      )}
      {reviewStages.map(([stage, label]) => {
        if (!item.allowed_actions.includes(`REVIEW_${stage}`)) return null;
        const qualifications = item.qualifications.filter(
          (row) => row.stage === stage,
        );
        if (!qualifications.length) return null;
        return (
          <ReviewDecisionForm
            key={`${item.item_version_id}:${stage}`}
            stage={stage}
            label={label}
            qualifications={qualifications}
            checklist={item.review_protocol[stage]}
            busy={busy}
            onDecision={onDecision}
          />
        );
      })}
      {item.allowed_actions.includes("REVISE") && (
        <SuccessorForm
          key={item.item_version_id}
          item={item}
          busy={busy}
          onRevise={onRevise}
        />
      )}
      <section aria-label="Immutable editorial review receipts">
        <h4>Immutable editorial review receipts</h4>
        {item.review_receipts.length ? (
          <ul>
            {item.review_receipts.map((receipt) => (
              <li key={receipt.id}>
                {evidenceLabel(receipt.evidence_class)} ·{" "}
                {evidenceLabel(receipt.action)}
                {receipt.decision
                  ? ` · ${evidenceLabel(receipt.decision)}`
                  : ""}
                {" · "}
                {receipt.recorded_at} · {receipt.authority} ·{" "}
                {receipt.actor_user_id} · {receipt.id} · {receipt.checksum}
              </li>
            ))}
          </ul>
        ) : (
          <p>No review receipts recorded.</p>
        )}
      </section>
      <section aria-label="Editorial item version history">
        <h4>Version history</h4>
        <ul>
          {item.history.map((version) => (
            <li key={version.item_version_id}>
              <button
                className={css.secondary}
                disabled={
                  busy || item.item_version_id === version.item_version_id
                }
                onClick={() => onInspect(version.item_version_id)}
                type="button"
              >
                Inspect {version.version_label}
              </button>
              {" · "}
              {version.item_version_id} · {version.checksum}
            </li>
          ))}
        </ul>
      </section>
    </article>
  );
}
