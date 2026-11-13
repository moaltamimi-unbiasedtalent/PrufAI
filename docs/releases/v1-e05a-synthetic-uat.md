# V1-E05A synthetic dry run and human UAT worksheet

This packet covers the non-official Berlin LiD practice profile using original synthetic items, synthetic accounts, an isolated PostgreSQL database with Alembic applied, Redis, guarded MinIO, and the application. Record evidence by task and commit. H01, H06, H10 and the target-environment approval govern real participant sessions. Product signoff is pending and pilot launch is not authorized. PR #60's independent P2 security disposition and the E03A gate remain separate and open.

## Before and after

The existing workflow already kept preparation separate from start, used an authoritative server deadline, prevented stale revisions from overwriting newer responses, made duplicate submissions idempotent, and withheld unpublished or revoked scores. Enrollment and recovery already had German error and institution-support messages, and sign-out cleared the account's local response outbox.

The Berlin screen could lose the original save ID after a connection error, even if the API had already committed the answer. The screen now writes the exact operation ID and revision to the existing account-specific outbox before sending. A retry uses that ID and revision. An unresolved save pauses navigation and submission. A stale revision needs an explicit choice to load the server's answer and discard the rejected local change. A lost submission acknowledgement triggers an authoritative status check before permitting a retry. The browser shows a distinct status for each outcome and keeps keyboard focus on the current task. The separate server result gate still controls score disclosure.

Pending responses survive a same-account reload only when browser storage is available and the outbox write succeeded. Storage is bounded and shared-device sign-out clears it. A disconnected page cannot load an exam from scratch, and the server can reject a queued write after expiry. If the browser cannot save locally, the UI says so. The reconnect test uses an expiring synthetic attempt and the real server's rejection or acknowledgement.

## Walkthroughs

| Scenario | Task and role | Expected observation | Device and input to record |
| --- | --- | --- | --- |
| U03–U04 | Candidate prepares the Berlin form, starts explicitly, changes answers, reloads, closes and reopens the browser | Preparation has no timer or question content. The server starts the time window, and the same attempt and confirmed answers resume. | Desktop browser, pointer or keyboard |
| U05 | Candidate loses the connection while saving, after commit and before acknowledgement, or near the deadline. Reconnect, retry, and try changing the same answer in two tabs. | The interface distinguishes pending, confirmed, and stale work. A retry preserves the operation identity. Expired writes are denied and the server's latest answer is never silently overwritten. | Desktop browser, record connection intervention and timing |
| U10 | Keyboard-only candidate signs in, accepts an invitation, reaches an answer, changes it, reviews unanswered items, opens and cancels the confirmation, submits, returns to the dashboard and signs out | Visible focus, logical order, reachable controls, focus restoration after cancel and an intelligible submission state | Browser/version, keyboard only |
| U11 | Screen-reader candidate follows the same core flow and exercises a retry | Labels, current question, answer state, timer thresholds, save state and withheld results are understandable without sighted help | Record actual screen reader, version, browser, OS and input |
| U12 | Candidate completes the flow on a physical phone and checks a zoomed desktop viewport | Questions, timer, controls, confirmation and account help fit the viewport and remain usable | Record physical model, OS, browser, zoom and orientation |
| U13 | Candidate tries wrong credentials, expired invitation or recovery, an unavailable auth service and a withheld result | German messages distinguish retry, wait, sign in again, expired attempt and contacting the institution. No unapproved contact address is displayed. | Browser/device and input method |
| Release and revocation | Institution operator reviews, releases and revokes the synthetic result. Candidate revisits the result after each change. | Review precedes release. Candidate cannot see the score before release or after revocation. Practice wording and the certificate boundary remain clear. | Separate institution and candidate sessions |

Copy one row per observed task:

| Task | Role | Expected behavior | Browser and device | Input and assistive technology | Observed result | Severity | Safe evidence reference | Retest commit | Observer status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | |

Record confusion, assistance and unsuccessful steps as observed. Use synthetic data in screenshots or notes and keep credentials and answer-bearing captures private. Automated keyboard and mobile viewport checks support technical acceptance. Human keyboard, screen-reader and physical-phone UAT remain **NOT VERIFIED**. Record actual human findings only after the authorized session.
