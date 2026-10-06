"use client";

import Link from "next/link";
import { useAuth, styles } from "../lib/auth";

const journeys = [
  [
    "Candidate",
    "Open an assignment, resume a governed attempt, and inspect released results.",
    "/candidate",
  ],
  [
    "Institution workspace",
    "Move from Exam Studio to moderation, appeals, and reviewer queues.",
    "/exam-studio",
  ],
  [
    "Evidence review",
    "Inspect writing and speaking evidence with the original-audio policy visible.",
    "/institution/speaking-reviews",
  ],
] as const;

export default function Home() {
  const { user } = useAuth();
  return (
    <div>
      <p style={styles.kicker}>Governed assessment workspace</p>
      <h1 style={{ fontSize: "2.7rem", marginBottom: "0.5rem" }}>
        Welcome to PrüfAI{user?.display_name ? `, ${user.display_name}` : ""}
      </h1>
      <p style={{ maxWidth: "700px", fontSize: "1.1rem", lineHeight: 1.6 }}>
        A clickable local walkthrough of standards, exam delivery, candidate
        evidence, review, moderation, and appeals.
      </p>
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))",
          gap: "1rem",
          marginTop: "2rem",
        }}
      >
        {journeys.map(([title, text, href]) => (
          <article key={href} style={styles.card}>
            <h2 style={{ marginTop: 0 }}>{title}</h2>
            <p style={{ lineHeight: 1.5 }}>{text}</p>
            <Link style={styles.primary} href={href}>
              Start journey →
            </Link>
          </article>
        ))}
      </section>
      <section style={{ ...styles.card, marginTop: "2rem", maxWidth: "none" }}>
        <h2>Demo map</h2>
        <p>
          Use the navigation above to jump between the candidate runtime, Exam
          Studio, writing and speaking review, result moderation, and appeals.
          API loading states are intentional when the local seed or an optional
          service is unavailable.
        </p>
        <p>
          <strong>Current account:</strong> {user?.email}
        </p>
      </section>
    </div>
  );
}
