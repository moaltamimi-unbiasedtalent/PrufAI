"use client";

import { useAuth } from "../../../lib/auth";

import Link from "next/link";
import { useEffect, useState } from "react";
import { authenticatedFetch } from "../../../lib/api";

type ModerationCase = {
  id: string;
  result_version_id: string;
  result_checksum: string;
  status: string;
  reason: string;
  priority: string;
};

export default function ResultModerationPage() {
  const [cases, setCases] = useState<ModerationCase[]>([]);
  const [message, setMessage] = useState("Loading moderation queue…");
  const organisationId = useAuth().organisationId ?? "";
  useEffect(() => {
    if (!organisationId) {
      setMessage("Organisation context is required.");
      return;
    }
    authenticatedFetch(
      `/api/v1/organisations/${organisationId}/result-moderation`,
    )
      .then((response) => {
        if (!response.ok) throw new Error("queue");
        return response.json() as Promise<ModerationCase[]>;
      })
      .then((items) => {
        setCases(items);
        setMessage("");
      })
      .catch(() => setMessage("Moderation queue could not be loaded."));
  }, [organisationId]);
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution
      </p>
      <h1>Result moderation</h1>
      <p>
        Moderation references exact result versions and never rewrites
        historical scores.
      </p>
      {message && <p role="status">{message}</p>}
      <table>
        <caption className="sr-only">Result moderation cases</caption>
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">Status</th>
            <th scope="col">Priority</th>
            <th scope="col">Reason</th>
          </tr>
        </thead>
        <tbody>
          {!message && cases.length === 0 && (
            <tr>
              <td colSpan={4}>
                No result moderation cases are available for this organisation.
              </td>
            </tr>
          )}
          {cases.map((item) => (
            <tr key={item.id}>
              <td>
                <Link href={`/institution/result-moderation/${item.id}`}>
                  {item.id}
                </Link>
              </td>
              <td>{item.status}</td>
              <td>{item.priority}</td>
              <td>{item.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
