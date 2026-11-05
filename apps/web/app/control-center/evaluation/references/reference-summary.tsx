"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import css from "../../control-center.module.css";
import { ErrorNotice, Pagination } from "../../metadata";
import type { EvaluationPage } from "../evaluation-api";
import { referenceApi, type ReferenceSummary } from "./reference-api";

export function ReferenceTable({ items }: { items: ReferenceSummary[] }) {
  return (
    <div
      className={css.tableRegion}
      role="region"
      aria-label="Reference governance scrollable table"
      tabIndex={0}
    >
      <table className={css.table}>
        <caption>Reference governance metadata</caption>
        <thead>
          <tr>
            {[
              "Exact reference",
              "Evidence class",
              "Source approval",
              "Kind / split",
              "Judgment completeness",
              "Adjudication",
              "Derived versions",
              "Release gates",
            ].map((label) => (
              <th key={label} scope="col">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                {item.id}
                <p>{item.checksum}</p>
                <Link
                  href={`/control-center/evaluation/references?reference=${encodeURIComponent(item.id)}`}
                >
                  Open restricted reference workbench
                </Link>
              </td>
              <td>
                {item.evidence_class}
                <p>
                  {item.evidence_class === "SYNTHETIC_TEST"
                    ? "Technical test only; no genuine expert calibration."
                    : "Approved source provenance; expert labels and empirical calibration require separate evidence."}
                </p>
              </td>
              <td>{item.status}</td>
              <td>
                {item.kind}
                <br />
                {item.split}
              </td>
              <td>
                {item.judgment_count} / {item.required_judgment_count}
                <p>
                  Independent submissions do not automatically establish
                  consensus.
                </p>
              </td>
              <td>
                {item.adjudication_status}
                <br />
                {item.adjudication_count} immutable version(s)
              </td>
              <td>
                {item.dataset_version_ids.length
                  ? item.dataset_version_ids.join(", ")
                  : "No dataset version derived"}
              </td>
              <td>
                Empirical thresholds: {item.empirical_thresholds}
                <br />
                Production activation:{" "}
                {item.production_activation
                  ? "Unavailable configuration"
                  : "Not authorized"}
                {item.limitations?.length ? (
                  <details>
                    <summary>Recorded quality limitations</summary>
                    <ul>
                      {item.limitations.map((limitation, index) => (
                        <li key={index}>{limitation}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ReferenceSummaryView() {
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [page, setPage] = useState<EvaluationPage<ReferenceSummary> | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setPage(null);
    setError(null);
    void referenceApi
      .summaries(offset)
      .then((value) => {
        if (!cancelled) setPage(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Reference metadata is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [offset, reload]);
  return (
    <section aria-label="Reference governance">
      <p className={css.notice}>
        This view contains safe governance metadata. Private source evidence,
        examiner judgments and adjudication rationale require separate
        authorized access. Agreement and model comparisons are measured in
        exact-version evaluation runs; no empirical threshold or production
        activation is configured.
      </p>
      {error ? (
        <ErrorNotice
          message={error}
          retry={() => setReload((value) => value + 1)}
        />
      ) : !page ? (
        <p role="status">Loading reference governance…</p>
      ) : (
        <>
          {page.items.length ? (
            <ReferenceTable items={page.items} />
          ) : (
            <p className={css.notice}>
              No governed references are available. Expert reference calibration
              is NOT VERIFIED.
            </p>
          )}
          <Pagination page={page} setOffset={setOffset} />
        </>
      )}
    </section>
  );
}
