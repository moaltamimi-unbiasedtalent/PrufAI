"use client";

import Link from "next/link";
import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import formCss from "../../../institution/assessment.module.css";
import css from "../../control-center.module.css";
import type { EvaluationPage } from "../../evaluation/evaluation-api";
import { exactPin } from "../../evaluation/regressions/regression-api";
import { RegressionComparisonDetails } from "../../evaluation/regressions/regression-details";
import { ErrorNotice, MetadataTable, Pagination } from "../../metadata";
import { feedbackApi, pinOf, type Pin, type Proposal } from "../feedback-api";
import { formValue } from "../feedback-forms";
import {
  candidateApi,
  type ChangeCandidate,
  type ReadinessDecision,
} from "./candidate-api";

const notReady: ReadinessDecision = "NOT_READY";
const nonproduction: ReadinessDecision = "APPROVE_FOR_NONPRODUCTION_VALIDATION";

function ExactPin({ value }: { value: Pin }) {
  return (
    <code style={{ overflowWrap: "anywhere" }}>
      {value.id} · {value.checksum}
    </code>
  );
}

function errorMessage(cause: unknown) {
  return cause instanceof Error
    ? cause.message
    : "Change candidate data could not be loaded.";
}

export function CandidateDetails({
  candidate,
}: {
  candidate: ChangeCandidate;
}) {
  const { evaluation, comparison } = candidate;
  return (
    <>
      <dl className={css.health}>
        <dt>Exact change candidate</dt>
        <dd>
          <ExactPin value={candidate} />
        </dd>
        <dt>Current receipt</dt>
        <dd>
          <ExactPin value={candidate.receipt} />
        </dd>
        <dt>Readiness status</dt>
        <dd>{candidate.status}</dd>
        <dt>Production readiness</dt>
        <dd>{candidate.production_readiness}</dd>
        <dt>Approved improvement proposal</dt>
        <dd>
          <ExactPin value={candidate.proposal} />
        </dd>
        <dt>Proposal approval receipt</dt>
        <dd>
          <ExactPin value={candidate.approval} />
        </dd>
        <dt>Confirmed feedback pattern</dt>
        <dd>
          <ExactPin value={candidate.pattern} />
        </dd>
        <dt>Source feedback</dt>
        <dd>
          <ul>
            {candidate.source_feedback.map((item) => (
              <li key={item.id}>
                <ExactPin value={item} />
              </li>
            ))}
          </ul>
        </dd>
        <dt>Target PromptVersion</dt>
        <dd>
          {candidate.target.version_label} ·{" "}
          <ExactPin value={candidate.target} />
        </dd>
        <dt>Draft PromptVersion</dt>
        <dd>
          {candidate.draft.version_label} · <ExactPin value={candidate.draft} />
        </dd>
        <dt>Controlled change</dt>
        <dd>
          {candidate.change_code} · {candidate.change_category}
        </dd>
        <dt>Risk classification</dt>
        <dd>{candidate.risk}</dd>
        <dt>Published regression suite</dt>
        <dd>
          <ExactPin value={candidate.suite_version} />
        </dd>
        <dt>Pinned baseline run</dt>
        <dd>
          <ExactPin value={candidate.baseline_run} />
        </dd>
        <dt>Approved reference evidence</dt>
        <dd>{candidate.evaluation_requirements.reference_evidence}</dd>
        <dt>Required regression checks</dt>
        <dd>{candidate.evaluation_requirements.required_checks.join(", ")}</dd>
        <dt>Technical evaluation</dt>
        <dd>{evaluation?.technical_status ?? "Awaiting evaluation"}</dd>
        <dt>Empirical quality gate</dt>
        <dd>{evaluation?.empirical_status ?? "Not evaluated"}</dd>
        {evaluation && (
          <>
            <dt>Candidate run</dt>
            <dd>
              <ExactPin value={evaluation.candidate_run} />
            </dd>
            <dt>Regression comparison</dt>
            <dd>
              <ExactPin value={evaluation.comparison_run} />
            </dd>
            <dt>Comparison eligibility</dt>
            <dd>{evaluation.comparison_status}</dd>
            <dt>Missing case count</dt>
            <dd>{evaluation.missing_case_count}</dd>
          </>
        )}
      </dl>
      <p className={css.notice}>
        Production readiness remains NOT_READY. Approval for nonproduction
        validation leaves the prompt draft unpublished.
      </p>
      {candidate.proposal_authority_current === false && (
        <p role="status" className={css.notice}>
          The proposal, its confirmed feedback, or the target prompt is no
          longer current. This candidate remains available as history.
        </p>
      )}
      {evaluation && evaluation.case_differences.length > 0 && (
        <MetadataTable
          caption="Case failure changes"
          columns={[
            ["case_id", "Case"],
            ["new_failures", "New failures"],
            ["resolved_failures", "Resolved failures"],
            ["unchanged_failures", "Unchanged failures"],
          ]}
          rows={evaluation.case_differences}
        />
      )}
      {comparison && (
        <RegressionComparisonDetails report={comparison.regression} />
      )}
      {candidate.history.length > 0 && (
        <MetadataTable
          caption="Readiness decision history"
          columns={[
            ["revision", "Revision"],
            ["action", "Action"],
            ["status", "Status"],
            ["decision", "Human decision"],
            ["rationale", "Rationale"],
            ["actor_id", "Actor reference"],
            ["created_at", "Recorded at"],
            ["id", "Receipt reference"],
            ["checksum", "Receipt checksum"],
          ]}
          rows={candidate.history.map((receipt) => ({ ...receipt }))}
        />
      )}
    </>
  );
}

function CandidatePinFields({
  name,
  label,
  value,
}: {
  name: string;
  label: string;
  value?: Pin | null;
}) {
  return (
    <>
      <label>
        {label} ID{" "}
        <input name={`${name}Id`} defaultValue={value?.id} required />
      </label>
      <label>
        {label} checksum{" "}
        <input
          name={`${name}Checksum`}
          defaultValue={value?.checksum}
          required
        />
      </label>
    </>
  );
}

export default function CandidateWorkbench() {
  const { user, loading } = useAuth();
  const permissions = user?.platform_permissions ?? [];
  const allowed =
    permissions.includes("platform.control_center.read") &&
    permissions.includes("standards.evaluation.read");
  const manage =
    permissions.includes("ai.manage") &&
    permissions.includes("standards.evaluation.run");
  const [page, setPage] = useState<EvaluationPage<ChangeCandidate> | null>(
    null,
  );
  const [proposalId, setProposalId] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [selected, setSelected] = useState<ChangeCandidate | null>(null);
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const operations = useRef(new Map<string, string>());

  useEffect(() => {
    setProposalId(
      new URLSearchParams(window.location.search).get("proposal") ?? "",
    );
  }, []);

  useEffect(() => {
    if (!allowed) return;
    let active = true;
    setPage(null);
    setListError(null);
    void candidateApi
      .list(offset)
      .then((value) => {
        if (active) setPage(value);
      })
      .catch((cause: unknown) => {
        if (active) setListError(errorMessage(cause));
      });
    return () => {
      active = false;
    };
  }, [allowed, user?.id, offset, reload]);

  function execute(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setActionError(null);
    void action()
      .catch((cause: unknown) => setActionError(errorMessage(cause)))
      .finally(() => {
        locked.current = false;
        setBusy(false);
      });
  }

  function operation(path: string, data: object) {
    const key = JSON.stringify([path, data]);
    const id = operations.current.get(key) ?? crypto.randomUUID();
    operations.current.set(key, id);
    return id;
  }

  function saved(candidate: ChangeCandidate) {
    setSelected(candidate);
    setReload((value) => value + 1);
  }

  function load(id: string) {
    execute(async () => {
      setSelected(null);
      setSelected(await candidateApi.detail(id));
    });
  }

  const eligibleProposal =
    proposal?.status === "APPROVED_FOR_EVALUATION" &&
    proposal.target.type === "PROMPT_VERSION" &&
    proposal.hypothesis_code === "PREVENT_RECORDED_FAILURE" &&
    proposal.evaluation_requirements.reference_evidence ===
      "NO_ADDITIONAL_REFERENCE_REQUIRED" &&
    ["PROMPT_REVISION", "VALIDATION_GUARD"].includes(proposal.change_category)
      ? proposal
      : null;
  const canApproveNonproduction =
    selected?.status === "HUMAN_REVIEW_REQUIRED" &&
    selected.evaluation?.comparison_status === "COMPARABLE" &&
    selected.evaluation.technical_status === "PASS" &&
    selected.evaluation.missing_case_count === 0;
  return (
    <>
      <p>
        <Link href="/control-center/feedback">
          ← Feedback &amp; Improvements
        </Link>
      </p>
      <h1>Change candidates</h1>
      {loading ? (
        <p role="status">Loading change candidate access…</p>
      ) : !allowed ? (
        <p className={css.error} role="alert">
          Platform evaluation read permission is required.
        </p>
      ) : (
        <>
          <p className={css.intro}>
            Inspect exact PromptVersion drafts, measured regression evidence and
            independent readiness decisions.
          </p>
          {actionError && (
            <p className={css.error} role="alert">
              {actionError}
            </p>
          )}
          {busy && (
            <p role="status">Processing the change candidate request…</p>
          )}
          {manage && (
            <section
              className={css.card}
              aria-label="Create a prompt change candidate"
            >
              <h2>Create a prompt change candidate</h2>
              <form
                className={formCss.form}
                onSubmit={(event) => {
                  event.preventDefault();
                  execute(async () => {
                    setProposal(null);
                    setProposal(
                      await feedbackApi.detail("proposals", proposalId.trim()),
                    );
                  });
                }}
              >
                <label>
                  Approved proposal version ID
                  <input
                    value={proposalId}
                    onChange={(event) => {
                      setProposalId(event.target.value);
                      setProposal(null);
                    }}
                    required
                    disabled={busy}
                  />
                </label>
                <button className={css.secondary} disabled={busy}>
                  Load approved proposal
                </button>
              </form>
              {proposal && !eligibleProposal && (
                <p role="alert">
                  Use a PromptVersion proposal approved for evaluation to
                  prevent a recorded failure, with no additional approved
                  reference requirement.
                </p>
              )}
              {eligibleProposal && (
                <>
                  <dl className={css.health}>
                    <dt>Proposal status</dt>
                    <dd>{eligibleProposal.status}</dd>
                    <dt>Proposal version</dt>
                    <dd>
                      <ExactPin value={eligibleProposal} />
                    </dd>
                    <dt>Exact approval receipt</dt>
                    <dd>
                      <ExactPin value={eligibleProposal.receipt} />
                    </dd>
                    <dt>Baseline PromptVersion</dt>
                    <dd>
                      <ExactPin value={eligibleProposal.target} />
                    </dd>
                  </dl>
                  <p>
                    The controlled change clears legacy optional instructions
                    and creates an unpublished successor.
                  </p>
                  <form
                    key={eligibleProposal.receipt.checksum}
                    className={formCss.form}
                    onSubmit={(event) => {
                      event.preventDefault();
                      const fields = new FormData(event.currentTarget);
                      execute(async () => {
                        const pinned = (name: string) =>
                          exactPin(
                            formValue(fields, `${name}Id`),
                            formValue(fields, `${name}Checksum`),
                          );
                        const data = {
                          proposal: pinOf(eligibleProposal),
                          approval: pinOf(eligibleProposal.receipt),
                          target: {
                            ...pinOf(eligibleProposal.target),
                            type: "PROMPT_VERSION" as const,
                          },
                          suite_version: pinned("suite"),
                          baseline_run: pinned("baseline"),
                        };
                        saved(
                          await candidateApi.create({
                            ...data,
                            operation_id: operation("create", data),
                          }),
                        );
                      });
                    }}
                  >
                    <CandidatePinFields
                      name="suite"
                      label="Published regression suite"
                      value={eligibleProposal.links.suite_version}
                    />
                    <CandidatePinFields
                      name="baseline"
                      label="Exact baseline run"
                      value={eligibleProposal.links.baseline_run}
                    />
                    <button className={css.button} disabled={busy}>
                      Create draft change candidate
                    </button>
                  </form>
                </>
              )}
            </section>
          )}
          <form
            className={formCss.form}
            onSubmit={(event) => {
              event.preventDefault();
              load(formValue(new FormData(event.currentTarget), "candidateId"));
            }}
          >
            <label>
              Exact change candidate ID <input name="candidateId" required />
            </label>
            <button className={css.secondary} disabled={busy}>
              Load change candidate
            </button>
          </form>
          {listError ? (
            <ErrorNotice
              message={listError}
              retry={() => setReload((value) => value + 1)}
            />
          ) : !page ? (
            <p role="status">Loading change candidates…</p>
          ) : !page.items.length ? (
            <p>No change candidates are visible in this scope.</p>
          ) : (
            <>
              <ul aria-label="Change candidates">
                {page.items.map((candidate) => (
                  <li key={candidate.id}>
                    <button
                      className={css.secondary}
                      disabled={busy}
                      onClick={() => load(candidate.id)}
                    >
                      Inspect {candidate.id}
                    </button>{" "}
                    {candidate.target.version_label} →{" "}
                    {candidate.draft.version_label} · {candidate.status}
                  </li>
                ))}
              </ul>
              <Pagination
                page={{ ...page, items: page.items.map(({ id }) => ({ id })) }}
                setOffset={setOffset}
              />
            </>
          )}
          {selected && (
            <section
              className={css.card}
              aria-label="Exact change candidate evidence"
            >
              <h2>Exact change candidate evidence</h2>
              <CandidateDetails candidate={selected} />
              {selected.allowed_actions.includes("EVALUATE") && (
                <button
                  className={css.button}
                  disabled={busy}
                  onClick={() =>
                    execute(async () => {
                      const data = {
                        expected_checksum: selected.receipt.checksum,
                      };
                      await candidateApi.evaluate(selected.id, {
                        ...data,
                        operation_id: operation(
                          `evaluate:${selected.id}`,
                          data,
                        ),
                      });
                      saved(await candidateApi.detail(selected.id));
                    })
                  }
                >
                  Evaluate pinned prompt candidate
                </button>
              )}
              {selected.allowed_actions.includes("REVIEW") && (
                <form
                  key={selected.receipt.checksum}
                  className={formCss.form}
                  onSubmit={(event) => {
                    event.preventDefault();
                    const fields = new FormData(event.currentTarget);
                    execute(async () => {
                      const rationale = formValue(fields, "rationale");
                      if (rationale.length < 8 || rationale.length > 500)
                        throw new Error(
                          "Enter an explicit rationale of 8–500 characters.",
                        );
                      const decision =
                        canApproveNonproduction &&
                        fields.get("decision") === nonproduction
                          ? nonproduction
                          : notReady;
                      const data = {
                        expected_checksum: selected.receipt.checksum,
                        decision,
                        rationale,
                      };
                      await candidateApi.review(selected.id, {
                        ...data,
                        operation_id: operation(`review:${selected.id}`, data),
                      });
                      saved(await candidateApi.detail(selected.id));
                    });
                  }}
                >
                  <label>
                    Readiness decision
                    <select name="decision" defaultValue={notReady}>
                      <option value={notReady}>Not ready</option>
                      {canApproveNonproduction && (
                        <option value={nonproduction}>
                          Approve for nonproduction validation
                        </option>
                      )}
                    </select>
                  </label>
                  <label>
                    Readiness rationale
                    <textarea
                      name="rationale"
                      minLength={8}
                      maxLength={500}
                      required
                    />
                  </label>
                  <button className={css.button} disabled={busy}>
                    Record readiness decision
                  </button>
                </form>
              )}
            </section>
          )}
        </>
      )}
    </>
  );
}
