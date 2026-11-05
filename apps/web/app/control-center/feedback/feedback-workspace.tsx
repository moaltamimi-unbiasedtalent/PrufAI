"use client";

import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../lib/auth";
import css from "../control-center.module.css";
import { ErrorNotice, Pagination } from "../metadata";
import type { EvaluationPage } from "../evaluation/evaluation-api";
import formCss from "../../institution/assessment.module.css";
import {
  feedbackApi,
  feedbackReasons,
  pinOf,
  proposalReasons,
  type FeedbackItem,
  type FeedbackSection,
  type Pattern,
  type Pin,
  type Proposal,
} from "./feedback-api";
import {
  DecisionForm,
  MeasuredIntake,
  ProposalForm,
  formValue,
} from "./feedback-forms";

type RecordView = FeedbackItem | Pattern | Proposal;
const sections = [
  ["items", "Feedback items"],
  ["patterns", "Patterns"],
  ["proposals", "Improvement proposals"],
] as const;

function Receipt({ pin }: { pin: Pin }) {
  return (
    <code style={{ overflowWrap: "anywhere" }}>
      {pin.id} · {pin.checksum}
    </code>
  );
}

export function FeedbackItemDetails({ item }: { item: FeedbackItem }) {
  return (
    <dl className={css.health}>
      <dt>Feedback receipt</dt>
      <dd>
        <Receipt pin={item} />
      </dd>
      <dt>Governance status</dt>
      <dd>{item.status}</dd>
      <dt>Category and severity</dt>
      <dd>
        {item.category} · {item.severity}
      </dd>
      <dt>Source provenance</dt>
      <dd>{item.source_kind}</dd>
      <dt>Exact source receipt</dt>
      <dd>
        <Receipt pin={{ id: item.source_id, checksum: item.source_checksum }} />
      </dd>
      {item.source_run && (
        <>
          <dt>Exact evaluation run</dt>
          <dd>
            <Receipt pin={item.source_run} />
          </dd>
        </>
      )}
      {item.failed_check && (
        <>
          <dt>Measured failed check</dt>
          <dd>{item.failed_check}</dd>
        </>
      )}
      {item.method && (
        <>
          <dt>Evidence method</dt>
          <dd>{item.method}</dd>
        </>
      )}
      <dt>Evidence maturity</dt>
      <dd>{item.evidence_class}</dd>
      <dt>Privacy classification</dt>
      <dd>{item.privacy_class}</dd>
      <dt>Execution classification</dt>
      <dd>{item.execution_class ?? "Not applicable"}</dd>
      <dt>Additional approved reference evidence</dt>
      <dd>
        {item.requires_reference
          ? "Required"
          : "Not required for this observation"}
      </dd>
      {item.reason_code && (
        <>
          <dt>Recorded reason</dt>
          <dd>{item.reason_code}</dd>
        </>
      )}
      {item.artifact_pins?.length > 0 && (
        <>
          <dt>Exact artifact pins</dt>
          <dd>
            <ul>
              {item.artifact_pins.map((pin) => (
                <li key={`${pin.type}:${pin.id}`}>
                  {pin.type} · <Receipt pin={pin} />
                </li>
              ))}
            </ul>
          </dd>
        </>
      )}
    </dl>
  );
}

function PatternDetails({ pattern }: { pattern: Pattern }) {
  return (
    <>
      <dl className={css.health}>
        <dt>Exact pattern</dt>
        <dd>
          <Receipt pin={pattern} />
        </dd>
        <dt>Category and evidence maturity</dt>
        <dd>
          {pattern.category} · {pattern.evidence_class}
        </dd>
        <dt>Privacy classification</dt>
        <dd>{pattern.privacy_class}</dd>
        <dt>Pattern status</dt>
        <dd>{pattern.status}</dd>
        <dt>Confirmed feedback members</dt>
        <dd>{pattern.member_count}</dd>
        <dt>Distinct sources</dt>
        <dd>{pattern.distinct_source_count}</dd>
        <dt>Required regression checks</dt>
        <dd>
          {pattern.required_checks.join(", ") || "Define before evaluation"}
        </dd>
        <dt>Additional approved reference evidence</dt>
        <dd>
          {pattern.requires_reference
            ? "Required"
            : "Not required for this observation"}
        </dd>
      </dl>
      {pattern.members.length > 0 && (
        <ul aria-label="Exact pattern members">
          {pattern.members.map((member) => (
            <li key={member.id}>
              <Receipt pin={member} /> · Confirmation{" "}
              {member.confirmation_operation_id}
            </li>
          ))}
        </ul>
      )}
      <p>These counts describe the recorded members of this pattern.</p>
    </>
  );
}

export function ProposalDetails({ proposal }: { proposal: Proposal }) {
  const { evaluation_requirements: evaluation, links } = proposal;
  return (
    <>
      <dl className={css.health}>
        <dt>Proposal version</dt>
        <dd>
          {proposal.version_number} · <Receipt pin={proposal} />
        </dd>
        <dt>Exact pattern</dt>
        <dd>
          <Receipt pin={proposal.pattern} />
        </dd>
        <dt>Exact target artifact</dt>
        <dd>
          {proposal.target.type} · {proposal.target.version_label} ·{" "}
          <Receipt pin={proposal.target} />
        </dd>
        <dt>Proposed change</dt>
        <dd>{proposal.change_category}</dd>
        <dt>Improvement hypothesis</dt>
        <dd>{proposal.hypothesis_code}</dd>
        <dt>Human review state</dt>
        <dd>{proposal.status}</dd>
        {proposal.decision_reason_code && (
          <>
            <dt>Recorded reason</dt>
            <dd>{proposal.decision_reason_code}</dd>
          </>
        )}
        {proposal.reviewed_by && (
          <>
            <dt>Independent reviewer</dt>
            <dd>{proposal.reviewed_by}</dd>
          </>
        )}
        <dt>Evaluation requirements</dt>
        <dd>{evaluation.status}</dd>
        <dt>Required regression checks</dt>
        <dd>
          {evaluation.required_checks.join(", ") || "Define before evaluation"}
        </dd>
        <dt>Reference evidence</dt>
        <dd>{evaluation.reference_evidence}</dd>
        <dt>Linked regression suite</dt>
        <dd>
          {links.suite_version ? (
            <Receipt pin={links.suite_version} />
          ) : (
            "No approved suite linked"
          )}
        </dd>
        <dt>Linked baseline evaluation</dt>
        <dd>
          {links.baseline_run ? (
            <>
              <Receipt pin={links.baseline_run} /> ·{" "}
              {links.baseline_run.hard_gate}
            </>
          ) : (
            "No baseline linked"
          )}
        </dd>
        <dt>Linked candidate evaluation</dt>
        <dd>
          {links.candidate_run ? (
            <>
              <Receipt pin={links.candidate_run} /> ·{" "}
              {links.candidate_run.hard_gate}
            </>
          ) : (
            "No candidate run linked"
          )}
        </dd>
        <dt>Linked quality gate</dt>
        <dd>{links.quality_gate}</dd>
        <dt>Release readiness</dt>
        <dd>{proposal.release_readiness}</dd>
      </dl>
      <p className={css.notice}>
        Approval authorizes evaluation only. The target version remains
        unchanged.
      </p>
    </>
  );
}

function describe(record: RecordView) {
  if ("source_kind" in record)
    return `${record.source_kind} · ${record.category} · ${record.evidence_class}`;
  if ("members" in record)
    return `${record.category} · ${record.member_count} confirmed members`;
  return `${record.target.type} · version ${record.version_number} · ${record.evaluation_requirements.status}`;
}

export default function FeedbackWorkspace() {
  const { user, loading } = useAuth();
  const permissions = user?.platform_permissions ?? [];
  const [section, setSection] = useState<FeedbackSection>("items");
  const [detail, setDetail] = useState<RecordView | null>(null);
  const [page, setPage] = useState<EvaluationPage<RecordView> | null>(null);
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const operations = useRef(new Map<string, string>());
  const allowed =
    permissions.includes("platform.control_center.read") &&
    permissions.includes("standards.evaluation.read");
  const manage =
    permissions.includes("ai.manage") &&
    permissions.includes("standards.evaluation.run");

  useEffect(() => {
    if (!allowed) return;
    let current = true;
    setPage(null);
    setListError(null);
    void feedbackApi
      .list(section, offset)
      .then((value) => {
        if (current) setPage(value);
      })
      .catch((cause: unknown) => {
        if (current)
          setListError(
            cause instanceof Error
              ? cause.message
              : "Feedback could not be loaded.",
          );
      });
    return () => {
      current = false;
    };
  }, [allowed, user?.id, section, offset, reload]);

  function execute(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setActionError(null);
    void action()
      .catch((cause: unknown) => {
        setActionError(
          cause instanceof Error
            ? cause.message
            : "The action could not be completed.",
        );
      })
      .finally(() => {
        locked.current = false;
        setBusy(false);
      });
  }

  function operation(path: string, data: object) {
    const key = JSON.stringify([path, data]);
    const existing = operations.current.get(key);
    if (existing) return existing;
    const id = crypto.randomUUID();
    operations.current.set(key, id);
    return id;
  }

  function saved(kind: FeedbackSection, value: RecordView) {
    if (section !== kind) {
      setSection(kind);
      setOffset(0);
      setPage(null);
    }
    setDetail(value);
    setReload((current) => current + 1);
  }

  function inspect(id: string) {
    execute(async () => {
      setDetail(null);
      setDetail(await feedbackApi.detail(section, id));
    });
  }

  const item = detail && "source_kind" in detail ? detail : null;
  const pattern = detail && "members" in detail ? detail : null;
  const proposal = detail && "target" in detail ? detail : null;
  return (
    <>
      <h1>Feedback &amp; Improvements</h1>
      {loading ? (
        <p role="status">Loading feedback access…</p>
      ) : !allowed ? (
        <p className={css.error} role="alert">
          Platform evaluation read permission is required.
        </p>
      ) : (
        <>
          <p className={css.intro}>
            Inspect recorded observations, confirmed patterns and proposals for
            future evaluation. Feedback actions preserve the published target
            version.
          </p>
          {actionError && (
            <p className={css.error} role="alert">
              {actionError}
            </p>
          )}
          {busy && <p role="status">Processing feedback request…</p>}
          {section === "items" && manage && (
            <MeasuredIntake
              execute={execute}
              busy={busy}
              operation={operation}
              onSaved={(value) => saved("items", value)}
            />
          )}
          <div className={formCss.form}>
            <label>
              Feedback view
              <select
                value={section}
                disabled={busy}
                onChange={(event) => {
                  const next = sections.find(
                    ([kind]) => kind === event.target.value,
                  );
                  if (!next) return;
                  setPage(null);
                  setDetail(null);
                  setActionError(null);
                  setOffset(0);
                  setSection(next[0]);
                }}
              >
                {sections.map(([kind, label]) => (
                  <option key={kind} value={kind}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <form
            key={section}
            className={formCss.form}
            onSubmit={(event) => {
              event.preventDefault();
              inspect(formValue(new FormData(event.currentTarget), "id"));
            }}
          >
            <label>
              Exact feedback, pattern or proposal ID{" "}
              <input name="id" required />
            </label>
            <button className={css.secondary} disabled={busy}>
              Load exact receipt
            </button>
          </form>
          {listError ? (
            <ErrorNotice
              message={listError}
              retry={() => setReload((current) => current + 1)}
            />
          ) : !page ? (
            <p role="status">Loading feedback records…</p>
          ) : page.items.length === 0 ? (
            <p>
              No{" "}
              {sections.find(([kind]) => kind === section)?.[1].toLowerCase()}{" "}
              are visible in this scope.
            </p>
          ) : (
            <>
              <ul aria-label="Feedback records">
                {page.items.map((record) => (
                  <li key={record.id}>
                    <button
                      className={css.secondary}
                      onClick={() => inspect(record.id)}
                      disabled={busy}
                      aria-label={`Inspect ${record.id}`}
                    >
                      {record.id}
                    </button>{" "}
                    {record.status} · {describe(record)}
                  </li>
                ))}
              </ul>
              <Pagination
                page={{ ...page, items: page.items.map(({ id }) => ({ id })) }}
                setOffset={setOffset}
              />
            </>
          )}
          {item && (
            <section className={css.card} aria-label="Exact feedback item">
              <h2>Feedback provenance</h2>
              <FeedbackItemDetails item={item} />
              <DecisionForm
                title="Feedback"
                actions={item.allowed_actions}
                reasons={feedbackReasons}
                receipt={item.checksum}
                initialClassification={item}
                busy={busy}
                execute={execute}
                save={async (data) => {
                  saved(
                    "items",
                    await feedbackApi.decide(item.id, {
                      ...data,
                      operation_id: operation(item.id, data),
                    }),
                  );
                }}
              />
              {manage && item.status === "CONFIRMED" && (
                <button
                  className={css.button}
                  disabled={busy}
                  onClick={() =>
                    execute(async () => {
                      const data = { anchor: pinOf(item) };
                      saved(
                        "patterns",
                        await feedbackApi.pattern({
                          ...data,
                          operation_id: operation("pattern", data),
                        }),
                      );
                    })
                  }
                >
                  Group confirmed feedback
                </button>
              )}
            </section>
          )}
          {pattern && (
            <section className={css.card} aria-label="Exact feedback pattern">
              <h2>Confirmed pattern evidence</h2>
              <PatternDetails pattern={pattern} />
              {manage && (
                <ProposalForm
                  key={pattern.id}
                  pattern={pattern}
                  busy={busy}
                  execute={execute}
                  operation={operation}
                  onSaved={(value) => saved("proposals", value)}
                />
              )}
            </section>
          )}
          {proposal && (
            <section
              className={css.card}
              aria-label="Exact improvement proposal"
            >
              <h2>Improvement proposal review</h2>
              <ProposalDetails proposal={proposal} />
              <DecisionForm
                title="Proposal"
                actions={proposal.allowed_actions}
                reasons={proposalReasons}
                receipt={proposal.receipt.checksum}
                busy={busy}
                execute={execute}
                save={async (data) => {
                  saved(
                    "proposals",
                    await feedbackApi.review(proposal.id, {
                      ...data,
                      operation_id: operation(proposal.id, data),
                    }),
                  );
                }}
              />
            </section>
          )}
        </>
      )}
    </>
  );
}
