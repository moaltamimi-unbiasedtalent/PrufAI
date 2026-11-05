"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import css from "../../control-center.module.css";
import { ErrorNotice, Pagination } from "../../metadata";
import type { EvaluationPage } from "../evaluation-api";
import { regressionApi, type RegressionVersion } from "./regression-api";
import { RegressionVersionDetails } from "./regression-details";

export default function RegressionSummaryView({
  inspectRun,
}: {
  inspectRun: (id: string) => void;
}) {
  const { user } = useAuth();
  const canOpenWorkbench =
    user?.platform_permissions.includes("ai.manage") &&
    (user.platform_permissions.includes("standards.evaluation.run") ||
      user.platform_permissions.includes("ai.review"));
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [page, setPage] = useState<EvaluationPage<RegressionVersion> | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RegressionVersion | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setPage(null);
    setError(null);
    void regressionApi
      .list("suites", offset)
      .then((value) => {
        if (active) setPage(value);
      })
      .catch((cause: unknown) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Regression suites could not be loaded.",
          );
      });
    return () => {
      active = false;
    };
  }, [offset, reload]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setDetail(null);
    setDetailError(null);
    void Promise.resolve()
      .then(() => regressionApi.version("suites", selected))
      .then((value) => {
        if (active) setDetail(value);
      })
      .catch((cause: unknown) => {
        if (active)
          setDetailError(
            cause instanceof Error
              ? cause.message
              : "Exact suite could not be loaded.",
          );
      });
    return () => {
      active = false;
    };
  }, [selected, reload]);

  return (
    <section aria-label="Regression suite governance">
      <p className={css.notice}>
        Inspect governed suite versions and exact case receipts. Open a
        comparison in Evaluation runs to inspect measured technical results and
        empirical coverage.
      </p>
      {canOpenWorkbench && (
        <p>
          <Link href="/control-center/evaluation/regressions">
            Open regression workbench
          </Link>
        </p>
      )}
      {error ? (
        <ErrorNotice
          message={error}
          retry={() => setReload((value) => value + 1)}
        />
      ) : !page ? (
        <p role="status">Loading regression suites…</p>
      ) : (
        <>
          {page.items.length > 0 ? (
            <div
              className={css.tableRegion}
              role="region"
              aria-label="Regression suites scrollable table"
              tabIndex={0}
            >
              <table className={css.table}>
                <caption>Regression suites</caption>
                <thead>
                  <tr>
                    {[
                      "Suite",
                      "Version receipt",
                      "Status",
                      "Scope and split",
                      "Cases",
                      "Evidence maturity",
                      "Independent approval",
                    ].map((label) => (
                      <th scope="col" key={label}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((suite) => (
                    <tr key={suite.id}>
                      <td>
                        <button
                          type="button"
                          className={css.secondary}
                          onClick={() => setSelected(suite.id)}
                        >
                          Inspect suite {suite.code}
                        </button>
                      </td>
                      <td>
                        {suite.version_number} · {suite.id}
                        <p>{suite.checksum}</p>
                      </td>
                      <td>{suite.status}</td>
                      <td>
                        {suite.kind} · {suite.split}
                      </td>
                      <td>{suite.case_count}</td>
                      <td>{suite.evidence_maturity}</td>
                      <td>{suite.approved_by ? "Recorded" : "Pending"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className={css.notice}>
              No governed regression suite versions are available in this scope.
            </p>
          )}
          <Pagination
            page={{ ...page, items: page.items.map(({ id }) => ({ id })) }}
            setOffset={(value) => {
              setOffset(value);
              setSelected(null);
            }}
          />
        </>
      )}
      {selected &&
        (detailError ? (
          <ErrorNotice
            message={detailError}
            retry={() => setReload((value) => value + 1)}
          />
        ) : detail?.id === selected ? (
          <>
            <RegressionVersionDetails version={detail} />
            <h3>Recent comparisons for this version</h3>
            {detail.recent_comparisons?.length ? (
              <ul>
                {detail.recent_comparisons.map((run) => (
                  <li key={run.id}>
                    <button
                      type="button"
                      className={css.secondary}
                      onClick={() => inspectRun(run.id)}
                    >
                      Inspect comparison {run.id}
                    </button>
                    <p>
                      Technical gate {run.hard_gate} · Human evidence review{" "}
                      {run.review_status}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p>
                No completed comparisons are available for this suite version.
              </p>
            )}
          </>
        ) : (
          <p role="status">Loading exact suite version…</p>
        ))}
    </section>
  );
}
