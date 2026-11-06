# Standards S1: sources and the B1 practice foundation

S1 registers official bibliographic metadata, governs access to private original
synthetic evidence, and creates a sealed draft for German B1 practice. It does
not claim provider authorization, official examination equivalence, or expert
validation.

| Official reference | Version and date recorded | S1 use |
| --- | --- | --- |
| [Council of Europe: CEFR Companion Volume](https://www.coe.int/en/web/common-european-framework-reference-languages/cefr-companion-volume-and-its-language-versions), [exact English edition](https://rm.coe.int/cefr-companion-volume-with-new-descriptors-2020/16809ea0d4) | 2020 English edition, ISBN 978-92-871-8621-8; the edition states April 2020 | Bibliographic support for the B1 language constructs. No full descriptors or translations ingested. |
| [g.a.s.t.: Deutsch-Test für Zuwanderer (A2–B1)](https://www.gast.de/de/forschung-entwicklung/entwicklung/auftraege/deutsch-test-fuer-zuwanderer-dtz) | Undated information page observed 6 November 2026; 1 January 2023 is the start of administration by g.a.s.t., not the page publication date | Provider metadata only. No tasks, assessment structure, or cut scores activated. |
| [BAMF: official Leben in Deutschland catalogue](https://www.bamf.de/SharedDocs/Anlagen/DE/Integration/Einbuergerung/gesamtfragenkatalog-lebenindeutschland.pdf?__blob=publicationFile&v=22) | Revision 22, marked 7 May 2025; separate publication date not stated | Integration knowledge, with no CEFR level. No catalogue items, answers, or images copied. |
| [IntTestV consolidated text](https://www.gesetze-im-internet.de/inttestv/BJNR080100013.html), [October 2026 gazette amendment](https://www.recht.bund.de/bgbl/1/2026/306/regelungstext.pdf?__blob=publicationFile&v=1) | Original instrument 9 April 2013; amendment dated 13 October 2026, published 20 October, effective 1 November in Germany | Legal provenance for human review only. No regulatory thresholds or rules activated. |

The catalogue records observation, publication, and effective dates separately
and does not download documents. Its metadata checksum identifies the exact
bibliographic version. A separate binary SHA-256 identifies the approved
original bytes, if any. Public availability grants no ingestion, reproduction,
redistribution, or commercial-use permission.

In **Control Centre → Source registry**, register an official entry and inspect
the version, provenance, and pending rights. A separate platform reviewer must
record a decision against the exact metadata checksum. For official records,
metadata review permits bibliographic reference only. It does not permit
upload, parsing, embedding, or display of protected material. Institutions and
candidates have no access to these private source operations.

For explicitly permitted **original synthetic** evidence, a separate human
decision pins the SHA-256 before upload. The API requires configured S3-compatible
private storage. It records the bucket, key, size, and checksum in PostgreSQL;
the operator UI does not expose storage locations or object credentials.
Integrity verification and ingest read the private object and verify the pinned
bytes. Repeating the same upload or successful ingest returns the existing
identity. A changed file requires a new version and review. A failed ingest can
be retried with the exact approved bytes. Withdrawal blocks new content access
and retrieval while retaining the historical version and its receipts.

After ingest, content approval is separate from the legal decision. Use the
authorized index and search paths to inspect exact version, page, chunk, and
checksum citations. The technical acceptance tests use deterministic embeddings
with real PostgreSQL pgvector and real MinIO; real-provider semantic quality is
**NOT VERIFIED**. No external provider is authorized by this exercise.

In **Control Centre → Standards**, create the **LANGUAGE / de / B1 / PRACTICE**
draft against the exact reviewed CEFR metadata checksum. It includes four
original synthetic constructs (Reading, Listening, Writing, Speaking), exact
supporting source binding, and a stable configuration checksum. It is labelled
“PrüfAI alignment draft”, “Synthetic practice content”, and “Not official
provider material”. The listening example is a script for a future recording.
The workbench shows validation and publication blockers. External rights,
independent expert validation, and separately governed rules must be settled
before any authoritative publication. No DTZ or LiD pack is delivered by S1.

The dedicated GitHub Actions step requires both PostgreSQL and object storage,
runs the Alembic-backed HTTP integration suite, and rejects skipped tests. The
authenticated browser acceptance runs against disposable services and verifies
persistence after restarting the test API process. Neither workflow should be
pointed at the default development database.

## Temporary scoped source review

Scoped review is separate from platform Standards RBAC. The reviewer signs in
normally and opens `/source-review`. The grant itself binds the account to one
organisation and a sealed allowlist of exact SourceVersion UUIDs and metadata
SHA-256 values. No membership or role is created by a grant. An active platform
role disqualifies the account from the scoped path. The page offers the approved
bibliographic fields and decision actions. A source with unresolved evidence
can remain `NOT_REVIEWED`.

| Existing capability | Scope and affected surfaces |
| --- | --- |
| `platform.control_center.read` | Platform shell and overview. Sections require their own platform permissions. |
| `standards.source.read` | Platform-shared source listings and version histories. |
| `standards.document.read` | Platform-shared version detail, page and provenance metadata, eligible file integrity, and rights-filtered retrieval and citations. |
| `standards.document.review` | Platform-shared source rights decisions and document approval and rejection. |
| `platform.role.assign` | Platform-only role assignment through the existing service. Scoped grant issuance, suspension and revocation require this same capability. |
| Tenant roles | Existing role permissions apply within an active membership's selected organisation. They cannot authorize these platform actions. |

The scoped paths never populate any of those capabilities. They only read
eligible `DRAFT`, `METADATA_ONLY` versions and call the existing rights writer for
an allowed `METADATA_ONLY` or `DENY` decision. Canonical source metadata and
separately pinned display metadata are rechecked. Versions with a live successor,
withdrawn versions, and sources registered or versioned by the reviewer fail
closed. The paths have no ingestion, retrieval, download or publication
operation.

### Grant contract

The administrative API uses `POST /api/v1/scoped-source-review/orgs/{organisation_id}/grants`.
An existing authenticated platform authority must confirm the independently
approved private manifest and its qualification, independence, rationale and
suspension-owner evidence before calling the API. The API checks the issuer's
current role, rejects self-grants, binds the request to
`approved_scope_checksum`, and records the single-use
`business_approval_reference` as `sha256:<manifest digest>`. A manifest
digest alone cannot establish that a human approved it. A proposed planning
manifest cannot be used as approval evidence.

To compute the scope checksum for a manifest, serialize the following as UTF-8
JSON with recursively sorted object keys, compact separators and no ASCII
escaping, then take the SHA-256.

- Include `schema_version` equal to 1, the canonical UUID strings
  `organisation_id`, `reviewer_user_id` and `authorized_by`, the trimmed
  `purpose`, and `starts_at` and `expires_at` as UTC ISO timestamps with
  `+00:00`. Omit fractional seconds when zero and use six digits when present.
- Include `resources` sorted by version UUID. Each element contains
  `version_id`, `metadata_checksum`, a sorted `metadata_fields` array and a
  sorted `allowed_decisions` array. Specify these arrays in the manifest.
  The API defaults the former to `effective_from`, `effective_until`,
  `publication_date`, `publisher`, `rights_status`, `title` and
  `version_label`. It defaults the latter to `DENY` and `METADATA_ONLY`.

Supply a unique `operation_id` with grant issuance, suspension, revocation and
review decisions. Exact replays preserve the original transition. Reusing a
manifest digest for a different grant or changing input for an existing operation
is rejected. An extension needs a new grant and separately approved manifest.
The immutable grant ID identifies that authorization for its entire lifetime.

The grant is available at its start time and expires at its end time. Activation
and expiration are audited when first observed. The issuer can call
`POST .../grants/{grant_id}/suspend` or `POST .../grants/{grant_id}/revoke`.
Suspension ends reviewer access immediately and may be followed by revocation.
A grant can never be reactivated. Grant state and a decision are serialized in
the same PostgreSQL transaction, and an old bearer session cannot bypass
expiration or revocation. Scoped audit events contain identifiers, pins,
operations and safe outcome codes. The original rights service retains the
canonical decision and private evidence.

Initial platform administrator recovery and reviewer account enrollment require
separate authorized procedures. The feature provides no bootstrap or account
creation route. A deployment without an eligible administrator cannot issue
grants. Provisioning in a real workspace requires a verified backup and explicit
operator approval of the final named manifest. Browser and PostgreSQL tests use
synthetic actors in disposable databases.
