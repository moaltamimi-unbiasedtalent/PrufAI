"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../../../lib/auth";
import css from "../control-center.module.css";
import {
  sourcePath,
  standardsRequest,
  type CatalogueEntry,
  type Citation,
  type Ingestion,
  type Integrity,
  type SourceVersion,
} from "../standards-api";

const checksumPattern = "[a-f0-9]{64}";
const referencePattern = "[0-9a-fA-F-]{36}";
const berlinDate = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeZone: "Europe/Berlin",
});

function effectiveDate(value: string | null): string {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime())
    ? berlinDate.format(date)
    : "—";
}

function fileContents(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () =>
      reject(new Error("The selected file could not be read."));
    reader.onload = () => {
      const encoded = String(reader.result).split(",", 2)[1];
      if (encoded) resolve(encoded);
      else reject(new Error("Select a non-empty file."));
    };
    reader.readAsDataURL(file);
  });
}

export function SourceWorkbench({ inspect }: { inspect: string }) {
  const { user } = useAuth();
  const permissions = user?.platform_permissions ?? [];
  const canCatalogue = permissions.includes("standards.source.read");
  const canRead = permissions.includes("standards.document.read");
  const canRegister =
    permissions.includes("standards.source.manage") &&
    permissions.includes("standards.document.manage");
  const canManage = permissions.includes("standards.document.manage");
  const canReview = permissions.includes("standards.document.review");
  const [catalogue, setCatalogue] = useState<CatalogueEntry[] | null>(null);
  const [catalogueError, setCatalogueError] = useState("");
  const [catalogueRetry, setCatalogueRetry] = useState(0);
  const [entryKey, setEntryKey] = useState("");
  const [versionId, setVersionId] = useState("");
  const [version, setVersion] = useState<SourceVersion | null>(null);
  const [history, setHistory] = useState<
    Pick<SourceVersion, "id" | "version_label" | "status">[] | null
  >(null);
  const [integrity, setIntegrity] = useState<Integrity | null>(null);
  const [citations, setCitations] = useState<Citation[] | null>(null);
  const [indexStatus, setIndexStatus] = useState("");
  const [searchMode, setSearchMode] = useState("");
  const [ingestion, setIngestion] = useState<Ingestion | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const entry =
    catalogue?.find(({ key }) => key === entryKey) ?? catalogue?.[0];

  useEffect(() => {
    if (!canCatalogue) return;
    let current = true;
    void standardsRequest<CatalogueEntry[]>(`${sourcePath}/catalogue`)
      .then((items) => {
        if (current) setCatalogue(items);
      })
      .catch((cause: unknown) => {
        if (current)
          setCatalogueError(
            String(cause instanceof Error ? cause.message : cause),
          );
      });
    return () => {
      current = false;
    };
  }, [canCatalogue, catalogueRetry]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Standards operation failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  function clearEvidence() {
    setVersion(null);
    setIntegrity(null);
    setIngestion(null);
    setCitations(null);
    setIndexStatus("");
    setSearchMode("");
    setFile(null);
  }

  async function load(id: string) {
    const current = await standardsRequest<SourceVersion>(
      `${sourcePath}/versions/${encodeURIComponent(id)}`,
    );
    setVersion(current);
    setIngestion(current.latest_ingestion ?? null);
  }

  useEffect(() => {
    if (!inspect || !canRead) return;
    let current = true;
    clearEvidence();
    setVersionId(inspect);
    setBusy(true);
    setError("");
    void standardsRequest<SourceVersion>(
      `${sourcePath}/versions/${encodeURIComponent(inspect)}`,
    )
      .then((item) => {
        if (current) {
          setVersion(item);
          setIngestion(item.latest_ingestion ?? null);
        }
      })
      .catch((cause: unknown) => {
        if (current)
          setError(
            cause instanceof Error
              ? cause.message
              : "Version could not be loaded.",
          );
      })
      .finally(() => {
        if (current) setBusy(false);
      });
    return () => {
      current = false;
    };
  }, [inspect, canRead]);

  const synthetic = version?.content_mode === "ORIGINAL_SYNTHETIC";
  const retired = version?.status === "RETIRED";
  const contentAvailable = synthetic && !retired && version?.ingestion_allowed;
  const versionPath = version ? `${sourcePath}/versions/${version.id}` : "";
  const sourceId = version?.source_id;
  useEffect(() => {
    setHistory(null);
    if (!sourceId || !canCatalogue) return;
    let current = true;
    void standardsRequest<
      Pick<SourceVersion, "id" | "version_label" | "status">[]
    >(`${sourcePath}/sources/${encodeURIComponent(sourceId)}/versions`)
      .then((items) => {
        if (current) setHistory(items);
      })
      .catch(() => {
        if (current) setError("Source version history could not be loaded.");
      });
    return () => {
      current = false;
    };
  }, [sourceId, canCatalogue]);

  function submitRights(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!version?.metadata_checksum) return;
    const fields = new FormData(event.currentTarget);
    const decision = String(fields.get("decision"));
    void run(async () => {
      await standardsRequest(`${versionPath}/rights-review`, "POST", {
        expected_metadata_checksum: version.metadata_checksum,
        decision,
        basis_reference: String(fields.get("basis_reference")).trim(),
        rights_holder: String(fields.get("rights_holder")).trim(),
        licence_type: String(fields.get("licence_type")).trim(),
        retention_policy: String(fields.get("retention_policy")).trim(),
        ...(decision === "ALLOW_SYNTHETIC_INGESTION"
          ? { expected_sha256: String(fields.get("expected_sha256")).trim() }
          : {}),
      });
      await load(version.id);
      setMessage("Rights decision recorded for this exact source version.");
    });
  }

  return (
    <section id="source-version-workbench" aria-label="Source operations">
      {canCatalogue && (
        <>
          <h2>Verified official source catalogue</h2>
          {catalogueError ? (
            <p role="alert" className={css.error}>
              {catalogueError}{" "}
              <button
                className={css.secondary}
                onClick={() => {
                  setCatalogueError("");
                  setCatalogueRetry((value) => value + 1);
                }}
              >
                Retry catalogue
              </button>
            </p>
          ) : catalogue === null ? (
            <p role="status">Loading official source catalogue…</p>
          ) : !entry ? (
            <p>No verified catalogue entries are available.</p>
          ) : (
            <article className={css.card}>
              <label className={css.filters}>
                Official publication
                <select
                  value={entry.key}
                  onChange={(event) => setEntryKey(event.target.value)}
                >
                  {catalogue.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.title}
                    </option>
                  ))}
                </select>
              </label>
              <h3>{entry.title}</h3>
              <p>
                {entry.publisher} · {entry.publication_label}
              </p>
              <p>
                Verified on {entry.verified_on}
                {entry.effective_from
                  ? ` · Effective ${effectiveDate(entry.effective_from)} (Europe/Berlin)`
                  : ""}
              </p>
              <p>
                Rights holder {entry.rights_holder}. {entry.rights_note}
              </p>
              <p>
                Metadata only. External content rights{" "}
                {entry.external_content_rights}.
              </p>
              <p>
                <a href={entry.edition_url}>Inspect official publication</a>
              </p>
              {canRegister && canRead && (
                <button
                  type="button"
                  className={css.button}
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      clearEvidence();
                      const registered = await standardsRequest<{
                        document_version_id: string;
                      }>(
                        `${sourcePath}/catalogue/${encodeURIComponent(entry.key)}/register`,
                        "POST",
                      );
                      setVersionId(registered.document_version_id);
                      await load(registered.document_version_id);
                      setMessage(
                        "Official source metadata registered. No document bytes were ingested.",
                      );
                    })
                  }
                >
                  Register official metadata
                </button>
              )}
            </article>
          )}
        </>
      )}
      <h2>Exact source version</h2>
      {!canRead ? (
        <p className={css.error} role="alert">
          Your platform permissions do not allow source version inspection.
        </p>
      ) : (
        <form
          className={css.filters}
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              clearEvidence();
              await load(versionId.trim());
            });
          }}
        >
          <label>
            Source version reference
            <input
              value={versionId}
              onChange={(event) => setVersionId(event.target.value)}
              required
              pattern={referencePattern}
            />
          </label>
          <button className={css.button} disabled={busy}>
            Inspect source version
          </button>
        </form>
      )}
      {busy && <p role="status">Working with the source registry…</p>}
      {message && (
        <p role="status" className={css.notice}>
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      {canRead && version && (
        <article className={css.card} aria-label="Source version detail">
          <h3>{version.version_label}</h3>
          <dl>
            <dt>Source version reference</dt>
            <dd>{version.id}</dd>
            <dt>Publisher</dt>
            <dd>{version.publisher}</dd>
            <dt>Publication date</dt>
            <dd>{version.publication_date?.slice(0, 10) ?? "Unavailable"}</dd>
            <dt>Retrieved at</dt>
            <dd>{version.retrieved_at ?? "Unavailable"}</dd>
            <dt>Effective dates (Europe/Berlin)</dt>
            <dd>
              {effectiveDate(version.effective_from)} –{" "}
              {effectiveDate(version.effective_until)}
            </dd>
            <dt>Pinned edition URL</dt>
            <dd>
              {version.source_url?.startsWith("https://") ? (
                <a href={version.source_url}>{version.source_url}</a>
              ) : (
                (version.source_url ?? "Unavailable")
              )}
            </dd>
            <dt>Version status</dt>
            <dd>{version.status}</dd>
            <dt>Content mode</dt>
            <dd>{version.content_mode ?? "Legacy registry entry"}</dd>
            <dt>Metadata checksum</dt>
            <dd>{version.metadata_checksum ?? "Unavailable"}</dd>
            <dt>Rights status</dt>
            <dd>{version.legal_review_status}</dd>
            <dt>Rights holder</dt>
            <dd>{version.rights_holder ?? "Unconfirmed"}</dd>
            <dt>Licence or legal basis type</dt>
            <dd>{version.licence_type ?? "Unconfirmed"}</dd>
            <dt>Retention classification</dt>
            <dd>{version.retention_policy ?? "Unconfirmed"}</dd>
            <dt>Permitted use</dt>
            <dd>{version.usage_rights ?? "Unconfirmed"}</dd>
            <dt>Derivative use</dt>
            <dd>{version.derivative_use_allowed ? "Allowed" : "Denied"}</dd>
            <dt>Redistribution</dt>
            <dd>{version.redistribution_allowed ? "Allowed" : "Denied"}</dd>
            <dt>AI processing</dt>
            <dd>
              {version.ai_processing_allowed
                ? "Allowed for recorded scope"
                : "Denied"}
            </dd>
            <dt>Reviewing human</dt>
            <dd>{version.legal_reviewed_by_user_id ?? "No review recorded"}</dd>
            <dt>Reviewed at</dt>
            <dd>{version.legal_reviewed_at ?? "—"}</dd>
            <dt>Rights decision checksum</dt>
            <dd>{version.decision_checksum ?? "No decision recorded"}</dd>
            <dt>Ingestion permission</dt>
            <dd>
              {retired
                ? "Withdrawn. Prior rights decision retained for history."
                : version.ingestion_allowed
                  ? "Approved for exact synthetic bytes"
                  : "Denied"}
            </dd>
            <dt>File checksum</dt>
            <dd>{version.sha256 ?? "No file registered"}</dd>
            <dt>Ingestion status</dt>
            <dd>
              {ingestion
                ? `${ingestion.status} · ${ingestion.parser_name} ${ingestion.parser_version ?? ""} · ${ingestion.pages_detected ?? 0} pages`
                : "No ingestion recorded"}
            </dd>
          </dl>
          {ingestion?.error_code && (
            <p role="alert" className={css.error}>
              {ingestion.error_code}
            </p>
          )}
          {version.content_mode === "METADATA_ONLY" && (
            <p className={css.notice}>
              This version permits source references only. File upload,
              ingestion and content search are unavailable.
            </p>
          )}
          {canReview &&
            version.metadata_checksum &&
            !retired &&
            (!version.decision_checksum ||
              version.legal_review_status === "APPROVED") && (
              <form
                key={`${version.id}:${version.decision_checksum ?? "pending"}`}
                className={css.filters}
                onSubmit={submitRights}
              >
                <label>
                  Rights decision
                  <select
                    name="decision"
                    defaultValue={
                      version.decision_checksum
                        ? "WITHDRAW"
                        : synthetic
                          ? "ALLOW_SYNTHETIC_INGESTION"
                          : "METADATA_ONLY"
                    }
                  >
                    {version.decision_checksum ? (
                      <option value="WITHDRAW">
                        Withdraw prior permission
                      </option>
                    ) : (
                      <>
                        {synthetic ? (
                          <option value="ALLOW_SYNTHETIC_INGESTION">
                            Allow exact original synthetic file
                          </option>
                        ) : (
                          <option value="METADATA_ONLY">Metadata only</option>
                        )}
                        <option value="DENY">Deny content use</option>
                      </>
                    )}
                  </select>
                </label>
                <label>
                  Rights holder
                  <input
                    name="rights_holder"
                    defaultValue={version.rights_holder ?? ""}
                    required
                    maxLength={200}
                  />
                </label>
                <label>
                  Licence type
                  <input
                    name="licence_type"
                    defaultValue={version.licence_type ?? ""}
                    required
                    maxLength={100}
                  />
                </label>
                <label>
                  Rights basis reference
                  <input name="basis_reference" required maxLength={240} />
                </label>
                <label>
                  Retention policy reference
                  <input name="retention_policy" required maxLength={240} />
                </label>
                {synthetic && !version.decision_checksum && (
                  <label>
                    Approved file SHA-256
                    <input
                      name="expected_sha256"
                      pattern={checksumPattern}
                      defaultValue={version.approved_sha256 ?? ""}
                    />
                  </label>
                )}
                <button className={css.button} disabled={busy}>
                  Record rights decision
                </button>
              </form>
            )}
          {contentAvailable && canManage && (
            <>
              <form
                key={version.id}
                className={css.filters}
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!file) return;
                  void run(async () => {
                    await standardsRequest(`${versionPath}/file`, "POST", {
                      filename: file.name,
                      content_base64: await fileContents(file),
                      declared_mime_type: file.type || null,
                    });
                    await load(version.id);
                    setMessage(
                      "Exact file checksum registered in private storage.",
                    );
                  });
                }}
              >
                <label>
                  Original synthetic document
                  <input
                    type="file"
                    onChange={(event) =>
                      setFile(event.target.files?.[0] ?? null)
                    }
                    required
                  />
                </label>
                <button className={css.button} disabled={busy || !file}>
                  Upload private file
                </button>
              </form>
              {version.sha256 && version.status !== "APPROVED" && (
                <button
                  type="button"
                  className={css.button}
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await standardsRequest<Ingestion>(
                        `${versionPath}/ingest`,
                        "POST",
                      );
                      await load(version.id);
                      setIngestion(result);
                      setMessage(`Ingestion recorded as ${result.status}.`);
                    })
                  }
                >
                  Ingest exact source version
                </button>
              )}
            </>
          )}
          {canReview &&
            contentAvailable &&
            version.sha256 &&
            ingestion?.status === "SUCCEEDED" &&
            version.status !== "APPROVED" && (
              <button
                type="button"
                className={css.button}
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await standardsRequest(`${versionPath}/approve`, "POST", {
                      expected_sha256: version.sha256,
                    });
                    await load(version.id);
                    setMessage(
                      "Exact source version approved for its recorded rights.",
                    );
                  })
                }
              >
                Approve ingested source version
              </button>
            )}
          {contentAvailable && version.sha256 && (
            <div className={css.filters}>
              <button
                type="button"
                className={css.secondary}
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    setIntegrity(
                      await standardsRequest<Integrity>(
                        `${versionPath}/integrity`,
                      ),
                    );
                  })
                }
              >
                Verify private storage integrity
              </button>
              {canManage && version.status === "APPROVED" && (
                <button
                  type="button"
                  className={css.secondary}
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const job = await standardsRequest<{
                        index_status?: string;
                        status: string;
                        chunks_created?: number;
                        execution_class?: string;
                      }>(`${sourcePath}/retrieval/index`, "POST", {
                        document_version_id: version.id,
                        expected_document_checksum: version.sha256,
                      });
                      setIndexStatus(
                        `${job.index_status ?? job.status} · ${job.execution_class ?? "Execution class unavailable"}${job.chunks_created === undefined ? "" : ` · ${job.chunks_created} chunks`}`,
                      );
                    })
                  }
                >
                  Index exact source version
                </button>
              )}
            </div>
          )}
          {integrity && (
            <p role="status" className={css.notice}>
              Private storage integrity {integrity.integrity} ·{" "}
              {integrity.storage_backend} · {integrity.file_size} bytes ·{" "}
              {integrity.sha256}
            </p>
          )}
          {indexStatus && (
            <p role="status" className={css.notice}>
              Index status {indexStatus}
            </p>
          )}
          {contentAvailable && version.status === "APPROVED" && (
            <form
              className={css.filters}
              onSubmit={(event) => {
                event.preventDefault();
                const query = String(
                  new FormData(event.currentTarget).get("query"),
                ).trim();
                void run(async () => {
                  setCitations(null);
                  const found = await standardsRequest<{
                    results: { citation: Citation }[];
                    execution_class: string;
                  }>(`${sourcePath}/retrieval/search`, "POST", {
                    query,
                    mode: "HYBRID",
                    limit: 5,
                    scope: {
                      document_version_ids: [version.id],
                      expected_document_checksum: version.sha256,
                    },
                  });
                  setCitations(found.results.map(({ citation }) => citation));
                  setSearchMode(found.execution_class);
                });
              }}
            >
              <label>
                Search approved exact source
                <input name="query" required maxLength={2000} />
              </label>
              <button className={css.button} disabled={busy}>
                Find exact citations
              </button>
            </form>
          )}
          {citations && (
            <section aria-label="Exact version citations">
              <h4>Exact version citations</h4>
              {searchMode === "TEST_ONLY" && (
                <p>
                  TEST ONLY retrieval. Authentic semantic quality is not
                  verified.
                </p>
              )}
              {!citations.length ? (
                <p>No authorized citations matched.</p>
              ) : (
                <ul>
                  {citations.map((citation) => (
                    <li key={citation.citation_id}>
                      {citation.source_title} · {citation.authority_name} ·{" "}
                      {citation.version_label} · {citation.citation_id} · Source
                      version {citation.document_version_id}
                      {citation.page_number
                        ? ` · Page ${citation.page_number}`
                        : ""}
                      {citation.checksum ? ` · ${citation.checksum}` : ""}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
          {history && (
            <section aria-label="Source version history">
              <h4>Source version history</h4>
              <ul>
                {history.map((item) => (
                  <li key={item.id}>
                    <button
                      className={css.secondary}
                      type="button"
                      disabled={busy || item.id === version.id}
                      onClick={() =>
                        void run(async () => {
                          clearEvidence();
                          setVersionId(item.id);
                          await load(item.id);
                        })
                      }
                    >
                      {item.version_label} · {item.status} · {item.id}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </article>
      )}
    </section>
  );
}
