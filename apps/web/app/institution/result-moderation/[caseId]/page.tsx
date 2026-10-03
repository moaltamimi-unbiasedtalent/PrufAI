"use client";

import { useEffect, useState } from "react";

type Props = { params: Promise<{ caseId: string }> };
type ModerationCase = {
  id: string;
  result_version_id: string;
  result_checksum: string;
  status: string;
  reason: string;
  priority: string;
  outcome: string | null;
};

export default function ModerationCasePage({ params }: Props) {
  const [item, setItem] = useState<ModerationCase | null>(null);
  const [message, setMessage] = useState("Loading moderation case…");
  const organisationId = process.env.NEXT_PUBLIC_ORGANISATION_ID ?? "";
  useEffect(() => {
    void params.then(({ caseId }) => {
      fetch(
        `/api/v1/organisations/${organisationId}/result-moderation/${caseId}`,
        { credentials: "include" },
      )
        .then((response) => {
          if (!response.ok) throw new Error("case");
          return response.json() as Promise<ModerationCase>;
        })
        .then((value) => {
          setItem(value);
          setMessage("");
        })
        .catch(() => setMessage("Moderation case could not be loaded."));
    });
  }, [organisationId, params]);
  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution review
      </p>
      <h1>Result moderation case</h1>
      {message && <p role="status">{message}</p>}
      {item && (
        <dl>
          <dt>Status</dt>
          <dd>{item.status}</dd>
          <dt>Reason</dt>
          <dd>{item.reason}</dd>
          <dt>Result version</dt>
          <dd>{item.result_version_id}</dd>
          <dt>Result checksum</dt>
          <dd>{item.result_checksum}</dd>
          <dt>Outcome</dt>
          <dd>{item.outcome ?? "Pending"}</dd>
        </dl>
      )}
    </main>
  );
}
