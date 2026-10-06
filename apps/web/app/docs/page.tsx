import Link from "next/link";
import { styles } from "../../lib/auth";

export default function DemoDocsPage() {
  return (
    <div>
      <p style={styles.kicker}>Operator guide</p>
      <h1>Local walkthrough guide</h1>
      <p style={{ maxWidth: "720px", lineHeight: 1.6 }}>
        Use the synthetic demo accounts to explore candidate delivery,
        institution review, moderation, and appeals. The full setup guide is in{" "}
        <code>docs/local-demo.md</code>.
      </p>
      <section style={{ ...styles.card, maxWidth: "720px" }}>
        <h2>Demo accounts</h2>
        <ul>
          <li>
            <code>candidate@demo.prufai.local</code> — candidate runtime
          </li>
          <li>
            <code>reviewer@demo.prufai.local</code> — writing and speaking
            review
          </li>
          <li>
            <code>admin@demo.prufai.local</code> — moderation and appeals
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
        <Link href="/">← Back to workspace</Link>
      </p>
    </div>
  );
}
