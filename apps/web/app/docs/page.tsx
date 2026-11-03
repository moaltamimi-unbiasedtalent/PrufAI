import Link from "next/link";
import { notFound } from "next/navigation";
import { developmentDemoEnabled } from "../../lib/demo";
import { OfflinePrivacyCheck } from "./offline-check";

export default function DemoDocsPage() {
  if (
    !developmentDemoEnabled(
      process.env.NODE_ENV,
      process.env.PRUFAI_DEMO_ENABLED,
    )
  )
    notFound();
  return (
    <main
      style={{
        maxWidth: 800,
        margin: "3rem auto",
        padding: "1.5rem",
        fontFamily: "Arial, sans-serif",
      }}
    >
      <p>Development operator guide</p>
      <h1>Local walkthrough guide</h1>
      <p style={{ maxWidth: "720px", lineHeight: 1.6 }}>
        Use synthetic accounts to complete an original objective practice exam
        and inspect real candidate, exam and result records as institution
        staff. The full setup guide is in <code>docs/local-demo.md</code>.
      </p>
      <section>
        <h2>Demo accounts</h2>
        <ul>
          <li>
            <code>candidate@demo.prufai.local</code> — candidate runtime
          </li>
          <li>
            <code>reviewer@demo.prufai.local</code> — institution inspection
          </li>
          <li>
            <code>admin@demo.prufai.local</code> — institution administration
            and inspection
          </li>
        </ul>
        <p>
          Password for all local-only accounts: <code>Demo-PrufAI-2026!</code>
        </p>
        <p>
          <strong>Safety:</strong> synthetic data only; never use these
          credentials outside development.
        </p>
      </section>
      <p>
        The data contains no fabricated Writing or Speaking assessment. Local
        audio playback, transcription and AI proposals are unavailable in this
        demonstration. Full Control Centre is deferred to a separate milestone.
      </p>
      <OfflinePrivacyCheck />
      <p>
        <Link href="/">← Back to workspace</Link>
      </p>
    </main>
  );
}
