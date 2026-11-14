# E06A synthetic release rehearsal

The rehearsal verifies a Berlin LiD technical release on isolated PostgreSQL,
Redis, guarded MinIO, API, AI and production-mode Web processes. The only
authorized profile is `SYNTHETIC_TEST`. It uses generated accounts and
TEST_ONLY questions. The institution actions use a synthetic operator account
with normal permissions and audit. They confer no human or content approval.

- **BERLIN_FORM_APPROVAL = NOT VERIFIED**
- **REAL_FORM_MANIFEST = UNAVAILABLE**
- **CONTENT_PUBLICATION = NOT AUTHORIZED**

The starting source is `00e29905d60c0f0bae3173992c71428fc8b89303`, the merge
commit of [PR #62](https://github.com/moaltamimi-unbiasedtalent/PrufAI/pull/62).
Its [application run](https://github.com/moaltamimi-unbiasedtalent/PrufAI/actions/runs/43516788586)
ran 35 authenticated browser journeys and 20 required integration reports with
zero required skips. The database had one applied head,
`0043_privacy_case_foundation`. The
[supply-chain run](https://github.com/moaltamimi-unbiasedtalent/PrufAI/actions/runs/43516788660)
verified the guarded images and scanners and ran the storage and operations
rehearsals. For a candidate release, use application and supply-chain evidence
for the exact candidate source. Match each job's checked-out revision to the head reported by GitHub.
The application and supply-chain jobs both explicitly check out that source.

## Verify the release bundle

The supply-chain job emits three public files for its checked-out commit.
They contain schema versions, tool versions, statuses, counts and hashes.

| File | Contents |
| --- | --- |
| `v1-e06a-release-manifest.json` | Commit and tree, prior commit and tree, service versions, wheel hashes, Next build identity, Docker image IDs and available registry digests, binary hashes, configuration fingerprint, Alembic head, CI run references and rehearsal digest |
| `v1-e06a-release-manifest.sha256` | SHA-256 of the canonical manifest |
| `v1-e06a-rehearsal.json` | Measured readiness, stop, previous-API restore, history and object reconciliation |

Retrieve the artifacts from the matching GitHub Actions run. Verify GitHub's
artifact digest, run `sha256sum --check v1-e06a-release-manifest.sha256` from the
extracted directory, and compare the rehearsal file's SHA-256 with
`TEST_EVIDENCE.evidence_sha256`. Match `SOURCE_IDENTITY.commit` and
`SOURCE_IDENTITY.tree` to the candidate Git commit, then match the MinIO and
guard hashes and decoder image ID to the scanner evidence in the same archive.
These are checksums with build and execution provenance. Organizational
decisions are recorded in the readiness register and review system.

The release record has canonical UTF-8 JSON with sorted keys and one trailing
newline. The same collected inputs produce identical bytes. Python package
builds use `SOURCE_DATE_EPOCH` from the commit, and the synthetic Next build
sets its build ID to the full Git SHA. Toolchains, architecture, dependency
resolution, and runtime evidence influence the emitted hashes. Backup hashes
identify the observed restore rehearsal, which uses newly generated synthetic
state on each invocation. Independent byte-for-byte reproducibility of a Web
rebuild across hosts has no evidence in this rehearsal.

[Configuration, runbook, and operator checklist](runbook.md) ·
[V1 readiness register](../v1-readiness-register.md) ·
[V1 pilot scope](../v1-e04a-pilot-scope.md)
