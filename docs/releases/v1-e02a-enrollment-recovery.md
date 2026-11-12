# V1-E02A candidate enrollment and recovery

This is the technical foundation for the proposed institution-invited Berlin
LiD practice pilot. Real candidate enrollment is **NOT AUTHORIZED**. Product
signoff remains **PENDING** and pilot launch is **NOT AUTHORIZED**.

| Release evidence | State |
| --- | --- |
| E02A technical foundation | Implementation and disposable acceptance are scoped to this revision. Record PASS only after application and supply-chain CI, including the required enrollment and browser checks, pass on the exact PR commit. Until that evidence is attached to the PR, record PARTIAL. |
| E02 approved delivery | **OPEN**. Real delivery is **NOT CONFIGURED**. |
| E02 full pilot gate | **OPEN** until authorized delivery and actual target acceptance are verified. |
| H01, H06, H12 | **OPEN**. This milestone records no human approval. |

## Enrollment and recovery

An institution actor with `membership.manage` opens **Person zur Übung
einladen** from institution inspection. The server binds an invitation to
that institution and the existing candidate role (`EXAMINER`, with
`organisation.read`). It persists only the digest of the random code. In the
test harness, the synthetic recipient obtains the code and institution ID
through the private capture. The German form at `/einladung` accepts those
values and a password. It creates a new identity and one active membership
only after validating the institution, role, expiry and pending state.
Existing accounts use sign-in or recovery and are never taken over by an
invitation.

The public sign-in screen links to German invitation and recovery pages.
`/anmelden` offers German sign-in and leads to the existing candidate
workspace. Use **Abmelden** to revoke the session and clear that account's
offline drafts on a shared device. The recovery request is generic for known
and unknown addresses. A valid recovery code at `/konto/neues-passwort`
changes the password and revokes sessions and other outstanding reset codes
in one database transaction. A completed reset clears the local session and
asks the user to sign in again. Rate limiting uses the configured Redis
limiter in service-backed tests and fails closed when it is unavailable.

Input validation returns fixed errors without submitted codes, passwords or
email addresses. Codes are entered as form fields and stay out of URLs and
persistent browser storage. Audit records carry event and institution or
actor references. Recovery pages display the configured server-side support
contact when supplied through `AUTH_ENROLLMENT_SUPPORT_CONTACT`; the default
guidance directs the user to their inviting institution.

## Delivery boundary

- `DISABLED` is the default. Requesting, accepting or confirming codes through
  the application is unavailable and no delivery is claimed.
- `TEST_ONLY` requires explicit wiring by a disposable test harness and is
  rejected in production settings. The browser runner validates its
  disposable database before injecting the capture adapter. It accepts
  synthetic `.test` recipients and stores captures in a private temporary
  directory outside uploaded artifacts. This browser spec disables traces,
  screenshots and video. The runner removes the capture after use.
- `APPROVED_ADAPTER` is reserved and refused by this implementation. A
  separately reviewed adapter would require recorded product, privacy and
  security authorization for the delivery or staff-assisted method, target
  environment, purpose, data, channel or processor, support contact,
  credential custody and retention. Verify failure handling and delivery
  receipts on the authorized target before activation. Token creation alone
  proves no delivery.

This change adds no public registration, published Berlin items, audio
processing, provider calls, official certification, or assessment rules.
The pilot assessment exposure remains tracked under E04.

## Acceptance evidence

- [Required migrated PostgreSQL and Redis test](../../services/api/tests/test_enrollment_postgresql_integration.py)
  covers wrong tenant and role, invalid, expired, revoked and used codes,
  duplicate and concurrent acceptance, Redis limits and outage, and recovery
  with password and session invalidation.
- [Authenticated browser test](../../tests/browser/pilot-enrollment.spec.cjs)
  uses actual institution and candidate actions, a persisted invitation
  across API restart, mobile keyboard focus and error feedback, sign-out and
  reset with stale credential/session denial. It exercises the isolated
  PostgreSQL, Redis and guarded MinIO browser environment. Manual accessibility
  and real participant UAT remain open under H10.
- [V1 readiness register](v1-readiness-register.md) and
  [U01/U02/U13](v1-uat-and-release-tests.md) retain the target evidence and
  human decision requirements. There is no new migration.
