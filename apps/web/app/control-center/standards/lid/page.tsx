"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import css from "../../control-center.module.css";
import {
  sourcePath,
  standardsRequest,
  type SourceVersion,
} from "../../standards-api";
import { effectiveDate } from "../dtz/dtz-api";
import { sourceLink } from "../dtz/blueprint-detail";
import { LidBlueprintDetail } from "./blueprint-detail";
import {
  lidPath,
  type LidBlueprint,
  type LidBlueprintSummary,
  type LidSource,
} from "./lid-api";

const sourceList = `${lidPath}/sources`;
const blueprintList = `${lidPath}/blueprints`;

export default function LidStandardsPage() {
  const { user } = useAuth();
  const permissions = user?.platform_permissions ?? [];
  const canRead = [
    "standard_pack.read",
    "standards.source.read",
    "standards.document.read",
  ].every((permission) => permissions.includes(permission));
  const canRegister = [
    "standards.authority.manage",
    "standards.source.manage",
    "standards.document.manage",
  ].every((permission) => permissions.includes(permission));
  const canManage = ["standard_pack.manage", "rules.manage"].every(
    (permission) => permissions.includes(permission),
  );
  const [sources, setSources] = useState<LidSource[]>([]);
  const [versions, setVersions] = useState<LidBlueprintSummary[] | null>(null);
  const [sourceKey, setSourceKey] = useState("");
  const [exact, setExact] = useState<SourceVersion | null>(null);
  const [draft, setDraft] = useState<LidBlueprint | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!canRead) return;
    let active = true;
    setWorking(true);
    setError("");
    void Promise.all([
      standardsRequest<LidSource[]>(sourceList),
      standardsRequest<LidBlueprintSummary[]>(blueprintList),
    ])
      .then(([registered, history]) => {
        if (!active) return;
        setSources(registered);
        setVersions(history);
      })
      .catch((cause: unknown) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "LiD evidence is unavailable.",
          );
      })
      .finally(() => {
        if (active) setWorking(false);
      });
    return () => {
      active = false;
    };
  }, [canRead, reload, user?.id]);

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

  const source = sources.find((entry) => entry.key === sourceKey);
  const pins = Object.fromEntries(
    sources.flatMap(({ key, registered }) =>
      registered?.reviewed
        ? [
            [
              key,
              {
                version_id: registered.id,
                metadata_checksum: registered.metadata_checksum,
              },
            ],
          ]
        : [],
    ),
  );
  const readyCount = Object.keys(pins).length;

  return (
    <>
      <p>
        <Link href="/control-center/standards">
          Back to Standards &amp; packs
        </Link>
      </p>
      <h1>Leben in Deutschland blueprint</h1>
      {canRead && (
        <p>
          <Link href="/control-center/standards/lid/editorial">
            Original Berlin LiD editorial set
          </Link>
        </p>
      )}
      <p className={css.notice}>
        Integration knowledge · PRACTICE · NOT OFFICIAL · NOT CERTIFIED
      </p>
      {!canRead ? (
        <p role="alert" className={css.error}>
          Platform Standards permission is required to inspect LiD drafts.
        </p>
      ) : (
        <>
          {error && (
            <p role="alert" className={css.error}>
              {error}
            </p>
          )}
          {working && <p role="status">Loading governed LiD evidence…</p>}
          <section aria-labelledby="lid-sources-heading">
            <h2 id="lid-sources-heading">LiD source register</h2>
            <div className={css.filters}>
              <label>
                LiD source
                <select
                  value={sourceKey}
                  disabled={working || !sources.length}
                  onChange={(event) => {
                    setSourceKey(event.target.value);
                    setExact(null);
                  }}
                >
                  <option value="">Select a source</option>
                  {sources.map((entry) => (
                    <option key={entry.key} value={entry.key}>
                      {entry.title}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className={css.secondary}
                disabled={working}
                onClick={() => {
                  setExact(null);
                  setReload((value) => value + 1);
                }}
              >
                Refresh exact sources and versions
              </button>
            </div>
            {source && (
              <article className={css.card} aria-label="LiD source metadata">
                <h3>{source.title}</h3>
                <dl className={css.health}>
                  <dt>Publisher</dt>
                  <dd>{source.publisher}</dd>
                  <dt>Edition</dt>
                  <dd>{source.version_label}</dd>
                  <dt>Publication</dt>
                  <dd>{source.publication_label}</dd>
                  <dt>Verified on</dt>
                  <dd>{source.verified_on}</dd>
                  <dt>Retrieved at</dt>
                  <dd>{source.retrieved_at ?? "Not recorded"}</dd>
                  <dt>Effective from</dt>
                  <dd>{effectiveDate(source.effective_from)}</dd>
                  <dt>Source</dt>
                  <dd>{sourceLink(source.edition_url ?? source.source_url)}</dd>
                  <dt>Rights holder</dt>
                  <dd>{source.rights_holder}</dd>
                  <dt>Reuse scope</dt>
                  <dd>{source.rights_note}</dd>
                  <dt>Source version</dt>
                  <dd>{source.registered?.id ?? "Unregistered"}</dd>
                  <dt>Metadata checksum</dt>
                  <dd>
                    {source.registered?.metadata_checksum ?? "Not recorded"}
                  </dd>
                  <dt>Bibliographic rights review</dt>
                  <dd>
                    {source.registered?.legal_review_status ??
                      "Pending registration"}
                  </dd>
                </dl>
                {!source.registered && canRegister && (
                  <button
                    type="button"
                    className={css.secondary}
                    disabled={working}
                    onClick={() =>
                      void work(async () => {
                        const next = await standardsRequest<LidSource>(
                          `${sourceList}/${encodeURIComponent(source.key)}/register`,
                          "POST",
                        );
                        setSources((items) =>
                          items.map((item) =>
                            item.key === next.key ? next : item,
                          ),
                        );
                      })
                    }
                  >
                    Register LiD source metadata
                  </button>
                )}
                {source.registered && (
                  <button
                    type="button"
                    className={css.secondary}
                    disabled={working}
                    onClick={() =>
                      void work(async () => {
                        setExact(
                          await standardsRequest<SourceVersion>(
                            `${sourcePath}/versions/${source.registered?.id}`,
                          ),
                        );
                      })
                    }
                  >
                    Inspect exact LiD source version
                  </button>
                )}
                {source.registered && !source.registered.reviewed && (
                  <p>
                    Check the independent bibliographic rights decision in the{" "}
                    <Link href="/control-center/source-registry">
                      Source Registry
                    </Link>
                    .
                  </p>
                )}
                {exact && exact.id === source.registered?.id && (
                  <section aria-label="Exact LiD source rights">
                    <h4>Recorded rights decision</h4>
                    <dl className={css.health}>
                      <dt>Content mode</dt>
                      <dd>{exact.content_mode ?? "Not recorded"}</dd>
                      <dt>Usage rights</dt>
                      <dd>{exact.usage_rights ?? "Not recorded"}</dd>
                      <dt>Ingestion</dt>
                      <dd>
                        {exact.ingestion_allowed ? "Allowed" : "Restricted"}
                      </dd>
                      <dt>Derivatives</dt>
                      <dd>
                        {exact.derivative_use_allowed
                          ? "Allowed"
                          : "Restricted"}
                      </dd>
                      <dt>Redistribution</dt>
                      <dd>
                        {exact.redistribution_allowed
                          ? "Allowed"
                          : "Restricted"}
                      </dd>
                      <dt>Review actor</dt>
                      <dd>
                        {exact.legal_reviewed_by_user_id ?? "Not recorded"}
                      </dd>
                      <dt>Decision checksum</dt>
                      <dd>{exact.decision_checksum ?? "Not recorded"}</dd>
                    </dl>
                  </section>
                )}
              </article>
            )}
          </section>
          <section aria-labelledby="lid-versions-heading">
            <h2 id="lid-versions-heading">LiD blueprint version history</h2>
            {sources.length > 0 && (
              <p>
                {readyCount} of {sources.length} exact source versions have a
                current bibliographic rights decision.
              </p>
            )}
            {canManage && (
              <button
                type="button"
                className={css.button}
                disabled={
                  working || !sources.length || readyCount !== sources.length
                }
                onClick={() =>
                  void work(async () => {
                    setDraft(
                      await standardsRequest<LidBlueprint>(
                        blueprintList,
                        "POST",
                        { sources: pins },
                      ),
                    );
                    setVersions(
                      await standardsRequest<LidBlueprintSummary[]>(
                        blueprintList,
                      ),
                    );
                  })
                }
              >
                Create or inspect LiD draft
              </button>
            )}
            {versions?.length === 0 && (
              <p>No LiD draft versions have been created.</p>
            )}
            {versions && versions.length > 0 && (
              <ul>
                {versions.map((version) => (
                  <li key={version.id}>
                    {version.version_label} · {version.status} ·{" "}
                    <code>{version.checksum}</code>{" "}
                    <button
                      type="button"
                      className={css.secondary}
                      disabled={working}
                      onClick={() =>
                        void work(async () => {
                          setDraft(
                            await standardsRequest<LidBlueprint>(
                              `${blueprintList}/${version.id}`,
                            ),
                          );
                        })
                      }
                    >
                      Inspect LiD version {version.version_label}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {draft && (
            <LidBlueprintDetail
              key={draft.id}
              draft={draft}
              canPreview={permissions.includes("rules.evaluate")}
              canPublish={permissions.includes("standard_pack.publish")}
            />
          )}
        </>
      )}
    </>
  );
}
