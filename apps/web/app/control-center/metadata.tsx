"use client";

import Link from "next/link";
import React, { FormEvent, useEffect, useState } from "react";
import { useAuth } from "../../lib/auth";
import {
  controlApi,
  displayMetadata,
  type MetadataPage,
  type MetadataRow,
} from "./control-api";
import css from "./control-center.module.css";
import { usePlatformOverview } from "./shell";

type Column = readonly [key: string, label: string];
type View = { label: string; permission: string; columns: readonly Column[] };
type Section = {
  title: string;
  description: string;
  views: Record<string, View>;
};

export const metadataSections: Record<string, Section> = {
  organisations: {
    title: "Organisations",
    description:
      "Read-only organisation identities and membership totals where your platform permissions allow them. Tenant data is never opened through impersonation.",
    views: {
      default: {
        label: "Organisations",
        permission: "platform.organisation.read",
        columns: [
          ["name", "Organisation"],
          ["id", "Reference"],
          ["slug", "Slug"],
          ["organisation_type", "Type"],
          ["status", "Status"],
          ["created_at", "Created"],
          ["membership_count", "Memberships"],
        ],
      },
    },
  },
  standards: {
    title: "Standards & Standard Packs",
    description:
      "Inspect governed frameworks, pack versions and source bindings. Provider alignment is metadata and never grants certification authority. Integration knowledge such as LiD has no CEFR level.",
    views: {
      versions: {
        label: "Pack versions",
        permission: "standard_pack.read",
        columns: [
          ["code", "Code"],
          ["name", "Standard Pack"],
          ["id", "Version reference"],
          ["version_label", "Version"],
          ["status", "Status"],
          ["assessment_family", "Assessment family"],
          ["language", "Language"],
          ["supported_levels", "CEFR levels where applicable"],
          ["allowed_classifications", "Classifications"],
          ["effective_from", "Effective from"],
          ["effective_until", "Effective until"],
          ["configuration_checksum", "Checksum"],
        ],
      },
      packs: {
        label: "Standard Packs",
        permission: "standard_pack.read",
        columns: [
          ["code", "Code"],
          ["name", "Name"],
          ["id", "Reference"],
          ["authority_id", "Authority reference"],
          ["assessment_family", "Family"],
          ["language", "Language"],
          ["status", "Status"],
        ],
      },
      authorities: {
        label: "Authorities & frameworks",
        permission: "standards.authority.read",
        columns: [
          ["code", "Code"],
          ["name", "Name"],
          ["authority_type", "Type"],
          ["jurisdiction", "Jurisdiction"],
          ["status", "Status"],
          ["id", "Reference"],
        ],
      },
      bindings: {
        label: "Source bindings",
        permission: "standard_pack.read",
        columns: [
          ["id", "Binding reference"],
          ["standard_pack_version_id", "Pack version"],
          ["knowledge_source_id", "Source"],
          ["document_version_id", "Document version"],
          ["binding_type", "Binding type"],
          ["required", "Required"],
          ["document_checksum", "Document checksum"],
        ],
      },
    },
  },
  "source-registry": {
    title: "Source Registry",
    description:
      "Source provenance and rights metadata only. Copyrighted source text, downloads and ingestion controls are not exposed here.",
    views: {
      sources: {
        label: "Sources",
        permission: "standards.source.read",
        columns: [
          ["title", "Title"],
          ["source_code", "Code"],
          ["id", "Reference"],
          ["authority_id", "Authority"],
          ["document_type", "Document type"],
          ["assessment_family", "Family"],
          ["language", "Language"],
          ["cefr_level", "CEFR where applicable"],
          ["status", "Status"],
        ],
      },
      documents: {
        label: "Document versions & rights",
        permission: "standards.document.read",
        columns: [
          ["source_title", "Source"],
          ["id", "Document version"],
          ["version_label", "Version"],
          ["status", "Status"],
          ["copyright_status", "Copyright status"],
          ["licence_type", "Licence"],
          ["legal_review_status", "Legal review"],
          ["ai_processing_allowed", "AI processing allowed"],
          ["redistribution_allowed", "Redistribution allowed"],
          ["effective_from", "Effective from"],
          ["effective_until", "Effective until"],
          ["sha256", "Checksum"],
        ],
      },
      ingestions: {
        label: "Ingestion records",
        permission: "standards.document.read",
        columns: [
          ["id", "Ingestion reference"],
          ["document_version_id", "Document version"],
          ["status", "Status"],
          ["pages_detected", "Pages detected"],
          ["created_at", "Created"],
          ["started_at", "Started"],
          ["finished_at", "Finished"],
        ],
      },
    },
  },
  "question-bank": {
    title: "Question Bank governance",
    description:
      "Inspect stable item and version metadata, publication lifecycle, origin and rights. Question text, answer keys and candidate responses are excluded.",
    views: {
      default: {
        label: "Item versions",
        permission: "question_bank.read",
        columns: [
          ["code", "Code"],
          ["item_id", "Stable item reference"],
          ["id", "Version reference"],
          ["version_label", "Version"],
          ["scope_type", "Ownership"],
          ["organisation_id", "Organisation reference"],
          ["skill", "Skill"],
          ["task_type", "Task type"],
          ["assessment_family", "Assessment family"],
          ["rights_status", "Rights"],
          ["origin_type", "Origin"],
          ["status", "Publication status"],
          ["checksum", "Checksum"],
        ],
      },
    },
  },
  exams: {
    title: "Exam governance",
    description:
      "Read-only exam-version provenance and lifecycle. Exam Studio remains the separate institution authoring workspace; this view does not activate or publish exams.",
    views: {
      default: {
        label: "Exam versions",
        permission: "exam.read",
        columns: [
          ["code", "Code"],
          ["exam_id", "Exam reference"],
          ["id", "Version reference"],
          ["version_label", "Version"],
          ["classification", "Classification"],
          ["standard_pack_version_id", "Standard Pack version"],
          ["scope_type", "Ownership"],
          ["organisation_id", "Organisation reference"],
          ["status", "Publication status"],
          ["origin_type", "Origin"],
          ["published_at", "Published"],
          ["checksum", "Checksum"],
        ],
      },
    },
  },
  "ai-operations": {
    title: "AI operations",
    description:
      "Registry and execution metadata only. Registered workflow definitions and recorded runs do not prove a live executor is connected. Prompts, outputs and candidate evidence are excluded.",
    views: {
      transcriptions: {
        label: "Transcription jobs",
        permission: "ai.read",
        columns: [
          ["id", "Execution job"],
          ["organisation_id", "Organisation"],
          ["status", "State"],
          ["provider", "Provider"],
          ["model_name", "Model"],
          ["adapter_version", "Adapter version"],
          ["profile_version", "Pinned profile version"],
          ["test_only", "TEST ONLY"],
          ["attempt_count", "Execution attempts"],
          ["created_at", "Requested"],
          ["started_at", "Started"],
          ["completed_at", "Completed"],
          ["duration_ms", "Execution duration (ms)"],
          ["failure_category", "Safe failure classification"],
        ],
      },
      models: {
        label: "Model registry",
        permission: "ai.read",
        columns: [
          ["id", "Reference"],
          ["code", "Code"],
          ["name", "Name"],
          ["provider", "Provider"],
          ["model_name", "Model"],
          ["status", "Status"],
          ["supports_structured_output", "Structured output"],
        ],
      },
      workflows: {
        label: "Workflows",
        permission: "ai.read",
        columns: [
          ["id", "Reference"],
          ["workflow_definition_id", "Workflow definition"],
          ["code", "Code"],
          ["name", "Name"],
          ["version_label", "Version"],
          ["status", "Status"],
          ["checksum", "Checksum"],
        ],
      },
      runs: {
        label: "Runs",
        permission: "ai.read",
        columns: [
          ["id", "Run reference"],
          ["workflow_version_id", "Workflow version"],
          ["agent_version_id", "Agent version"],
          ["organisation_id", "Organisation"],
          ["status", "State"],
          ["created_at", "Created"],
          ["started_at", "Started"],
          ["completed_at", "Completed"],
          ["request_id", "Request reference"],
        ],
      },
      reviews: {
        label: "Human review",
        permission: "ai.read",
        columns: [
          ["id", "Review reference"],
          ["ai_run_id", "Run"],
          ["review_type", "Type"],
          ["entity_type", "Entity type"],
          ["entity_id", "Entity reference"],
          ["status", "State"],
          ["requested_at", "Requested"],
          ["decided_at", "Decided"],
        ],
      },
      checkpoints: {
        label: "Checkpoints",
        permission: "ai.read",
        columns: [
          ["id", "Checkpoint reference"],
          ["run_id", "Run"],
          ["workflow_version_id", "Workflow version"],
          ["organisation_id", "Organisation"],
          ["status", "State"],
          ["created_at", "Created"],
          ["updated_at", "Updated"],
          ["expires_at", "Expires"],
        ],
      },
    },
  },
  certification: {
    title: "Certification oversight",
    description:
      "Read-only authorization and certificate metadata. OFFICIAL_CERTIFICATION remains denied. Synthetic test authorization is never a real provider entitlement, and platform access does not grant issuance authority.",
    views: {
      authorizations: {
        label: "Authorizations",
        permission: "certification.read",
        columns: [
          ["id", "Reference"],
          ["organisation_id", "Organisation"],
          ["assessment_scope", "Assessment scope"],
          ["provider_scope", "Provider scope"],
          ["standard_pack_version_id", "Exact pack version"],
          ["classification", "Classification"],
          ["authorization_kind", "Authorization kind"],
          ["official_entitlement", "Official entitlement"],
          ["version", "Authorization version"],
          ["status", "Recorded status"],
          ["effective_status", "Effective status"],
          ["currently_valid", "Currently valid"],
          ["effective_from", "Effective from"],
          ["effective_until", "Effective until"],
        ],
      },
      certificates: {
        label: "Certificate records",
        permission: "certification.read",
        columns: [
          ["id", "Reference"],
          ["organisation_id", "Organisation"],
          ["release_id", "Release reference"],
          ["result_version_id", "Result version"],
          ["authorization_id", "Authorization"],
          ["authorization_version", "Authorization version"],
          ["status", "Recorded status"],
          ["effective_status", "Current validity"],
          ["currently_valid", "Currently valid"],
          ["official_entitlement", "Official entitlement"],
          ["issued_at", "Issued"],
        ],
      },
    },
  },
};

export function MetadataBrowser({ section }: { section: string }) {
  const config = metadataSections[section];
  const { user } = useAuth();
  const { overview } = usePlatformOverview();
  const [kind, setKind] = useState(Object.keys(config.views)[0]);
  const [page, setPage] = useState<MetadataPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [scopeInput, setScopeInput] = useState("");
  const [scope, setScope] = useState("");
  const scoped = section === "question-bank" || section === "exams";
  const canScope = Boolean(
    user?.platform_permissions.includes("platform.organisation.read"),
  );
  const availableViews = Object.entries(config.views).filter(([, option]) =>
    user?.platform_permissions.includes(option.permission),
  );
  const selectedKind = availableViews.some(([key]) => key === kind)
    ? kind
    : (availableViews[0]?.[0] ?? kind);
  const view = config.views[selectedKind];
  const allowed = Boolean(user?.platform_permissions.includes(view.permission));
  useEffect(() => {
    if (!scoped || !canScope) return;
    const selected = new URLSearchParams(window.location.search).get(
      "organisation_id",
    );
    if (
      selected &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        selected,
      )
    ) {
      setScope(selected);
      setScopeInput(selected);
    }
  }, [scoped, canScope]);
  useEffect(() => {
    let cancelled = false;
    setPage(null);
    setError(null);
    if (!allowed) return;
    void controlApi
      .page(section, {
        kind: selectedKind === "default" ? undefined : selectedKind,
        offset,
        limit: 20,
        ...(scoped && scope && canScope ? { organisation_id: scope } : {}),
      })
      .then((value) => {
        if (!cancelled) setPage(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Metadata could not be loaded.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, canScope, selectedKind, offset, reload, scope, scoped, section]);
  function changeScope(event: FormEvent) {
    event.preventDefault();
    setScope(scopeInput.trim());
    setOffset(0);
  }
  return (
    <>
      <p className={css.eyebrow}>Read-only platform governance</p>
      <h1>{config.title}</h1>
      <p className={css.intro}>{config.description}</p>
      {Object.keys(config.views).length > 1 && (
        <label className={css.filters}>
          Metadata view{" "}
          <select
            value={selectedKind}
            onChange={(event) => {
              setKind(event.target.value);
              setOffset(0);
            }}
          >
            {availableViews.map(([key, option]) => (
              <option key={key} value={key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {scoped && (
        <p className={css.notice}>
          {scope
            ? `Organisation metadata scope: ${scope}. This is a read-only metadata filter, not tenant impersonation.`
            : "Shared platform metadata only. Organisation-private items are excluded from this default view."}
        </p>
      )}
      {scoped && canScope && (
        <form className={css.filters} onSubmit={changeScope}>
          <label>
            Organisation reference (optional)
            <input
              value={scopeInput}
              onChange={(event) => setScopeInput(event.target.value)}
              placeholder="UUID; blank for platform shared"
              pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
            />
          </label>
          <button className={css.button}>Apply metadata scope</button>
          {scope && (
            <button
              className={css.secondary}
              type="button"
              onClick={() => {
                setScope("");
                setScopeInput("");
                setOffset(0);
              }}
            >
              Show platform shared
            </button>
          )}
        </form>
      )}
      {!allowed ? (
        <p className={css.error} role="alert">
          Your platform permissions do not allow this metadata view.
        </p>
      ) : error ? (
        <ErrorNotice
          message={error}
          retry={() => setReload((value) => value + 1)}
        />
      ) : !page ? (
        <p role="status">Loading {view.label.toLowerCase()}…</p>
      ) : (
        <>
          {!page.items.length ? (
            <p className={css.notice}>
              No {view.label.toLowerCase()} are available in the authorized
              scope. No data is simulated.
            </p>
          ) : (
            <MetadataTable
              caption={view.label}
              columns={view.columns}
              rows={page.items}
              organisationLinks={section === "organisations"}
            />
          )}
          <Pagination page={page} setOffset={setOffset} />
        </>
      )}
      {section === "source-registry" && selectedKind === "ingestions" && (
        <p className={css.intro}>
          These are persisted ingestion records. This read-only view does not
          run ingestion or verify document storage availability.
        </p>
      )}
      {section === "ai-operations" && (
        <p className={css.notice}>
          Executor status:{" "}
          {overview?.service_health?.ai_executor.status ??
            "Not available in this view"}
          .{" "}
          {user?.platform_permissions.includes("platform.system.read") && (
            <>
              <Link href="/control-center/system">
                Inspect actual executor configuration in System health
              </Link>
              .{" "}
            </>
          )}
          No processing is simulated by this view.
          <br />
          Speech transcription executor:{" "}
          {overview?.service_health?.speech_transcription_executor?.status ??
            "NOT VERIFIED"}
          .{" "}
          {overview?.service_health?.speech_transcription_executor?.description}
        </p>
      )}
    </>
  );
}

export function ErrorNotice({
  message,
  retry,
}: {
  message: string;
  retry: () => void;
}) {
  return (
    <div className={css.error} role="alert">
      <p>{message}</p>
      <button className={css.secondary} onClick={retry}>
        Retry loading
      </button>
    </div>
  );
}

export function MetadataTable({
  caption,
  columns,
  rows,
  organisationLinks = false,
}: {
  caption: string;
  columns: readonly Column[];
  rows: MetadataRow[];
  organisationLinks?: boolean;
}) {
  const { user } = useAuth();
  return (
    <div
      className={css.tableRegion}
      role="region"
      aria-label={`${caption} scrollable table`}
      tabIndex={0}
    >
      <table className={css.table}>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map(([key, label]) => (
              <th scope="col" key={key}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.id ?? index)}>
              {columns.map(([key], columnIndex) => (
                <td key={key}>
                  {displayMetadata(row[key])}
                  {organisationLinks && columnIndex === 0 && (
                    <>
                      {user?.platform_permissions.includes(
                        "question_bank.read",
                      ) && (
                        <p>
                          <Link
                            href={`/control-center/question-bank?organisation_id=${encodeURIComponent(String(row.id))}`}
                          >
                            Question metadata
                          </Link>
                        </p>
                      )}
                      {user?.platform_permissions.includes("exam.read") && (
                        <p>
                          <Link
                            href={`/control-center/exams?organisation_id=${encodeURIComponent(String(row.id))}`}
                          >
                            Exam metadata
                          </Link>
                        </p>
                      )}
                    </>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({
  page,
  setOffset,
}: {
  page: MetadataPage;
  setOffset: (value: number) => void;
}) {
  return (
    <div className={css.paging}>
      <p role="status">
        {page.total
          ? `${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} of ${page.total} records`
          : "0 records"}
      </p>
      <div>
        <button
          className={css.secondary}
          disabled={page.offset === 0}
          onClick={() => setOffset(Math.max(0, page.offset - page.limit))}
        >
          Previous page
        </button>
        <button
          className={css.secondary}
          disabled={page.offset + page.limit >= page.total}
          onClick={() => setOffset(page.offset + page.limit)}
        >
          Next page
        </button>
      </div>
    </div>
  );
}
