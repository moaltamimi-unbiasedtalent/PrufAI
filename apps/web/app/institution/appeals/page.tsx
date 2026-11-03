"use client";

import { useAuth } from "../../../lib/auth";

import Link from "next/link";
import { useEffect, useState } from "react";
import { appealApi, type AppealSummary } from "./appeal-api";

export default function AppealsQueuePage() {
  const [appeals, setAppeals] = useState<AppealSummary[]>([]);
  const [message, setMessage] = useState("Loading appeals…");
  const organisationId = useAuth().organisationId ?? "";
  useEffect(() => {
    if (!organisationId) {
      setMessage("Organisation context is required.");
      return;
    }
    appealApi
      .list(organisationId)
      .then((items) => {
        setAppeals(items);
        setMessage("");
      })
      .catch(() => setMessage("Appeal queue could not be loaded."));
  }, [organisationId]);
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution
      </p>
      <h1>Appeals queue</h1>
      <p>
        Reviewers see safe result references and can open an exact historical
        appeal.
      </p>
      {message && <p role="status">{message}</p>}
      <table>
        <caption className="sr-only">Candidate result appeals</caption>
        <thead>
          <tr>
            <th scope="col">Appeal</th>
            <th scope="col">Status</th>
            <th scope="col">Result version</th>
          </tr>
        </thead>
        <tbody>
          {!message && appeals.length === 0 && (
            <tr>
              <td colSpan={3}>
                No appeals are available for review in this organisation.
              </td>
            </tr>
          )}
          {appeals.map((appeal) => (
            <tr key={appeal.id}>
              <td>
                <Link href={`/institution/appeals/${appeal.id}`}>
                  {appeal.id}
                </Link>
              </td>
              <td>{appeal.status}</td>
              <td>{appeal.challenged_result_version_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
