# D-01 redacted release-evidence index

The offline [generator](../../../scripts/release-evidence-index/index.py) produces
reviewer JSON, Markdown and a SHA-256 receipt from operator-staged synthetic
technical evidence. It does not collect participant records, call GitHub or
providers, authenticate a human, approve a finding, choose a release candidate,
or modify source evidence. E03A remains BLOCKED, production readiness NO and
pilot launch NOT AUTHORIZED. Reviewer appointment and disposition paperwork
remains deferred on the [pre-production checklist](../../operations/pre-production-review-checklist.md).

## Inventory and bounded gap

Inspected main: `5f28508fd99377ee2ce5e0b863c84b7cec1d2e3f`.

| Existing capability | Reused boundary |
| --- | --- |
| [E06A manifest, checksum and rehearsal](../v1-e06a/README.md) | Existing schema `prufai-v1-e06a/1`, filenames and checksum behavior are unchanged. The index checks and projects these files; it never imports or runs the rehearsal. |
| [E06A generator and embedded preflight assertions](../../../scripts/storage-readiness/v1_release.py) | Existing disposable full rehearsal remains in Supply-chain CI. There was no separate E06A pytest module; its assertions execute inside that rehearsal. |
| [Supply-chain summaries](../../../scripts/summarize-supply-chain.py) and [workflow](../../../.github/workflows/supply-chain.yml) | Existing artifact names, metadata, source identities and informational scan classification are retained. No new scanner or vulnerability acceptance is introduced. |
| [Required JUnit checker](../../../scripts/check-required-evaluation-junit.py) and application workflow | Required-report signatures are derived from the pinned workflow, including its privacy wrapper. Raw Python logs supply observed report totals; the generator does not infer them from a green job. |
| [Readiness register](../v1-readiness-register.md), [approval packets](../v1-human-approval-packets.md), [review ledger](../../security/review-exception-ledger.md) and [checklist](../../operations/pre-production-review-checklist.md) | Existing human decisions and historical records remain authoritative. Their approval is never inferred or copied from CI. |
| D-01 gap | No deterministic index previously joined exact PR-head/post-merge revisions, workflow/job links, locally checked archive/manifest digests, unavailable evidence and open decisions. This addition fills that navigation gap only. |

## Trust and verification contract

An accountable operator first captures the GitHub API records, logs and artifacts
through an authenticated, approved read-only process. Stage only synthetic
nonproduction evidence in a dedicated, owner-only directory outside the source
checkout. The generator itself has no credentials or network collector. A receipt
can prove local consistency and byte identity; it cannot independently prove
that a staged API response came from GitHub. `github_authenticity` and current
remote availability therefore remain **NOT VERIFIED**, even when local checks
succeed. Keep acquisition/custody records private and separate.

The source checkout must contain the pinned Git objects and their complete
migration/workflow files. The tool reads objects without checkout, hooks,
replacement objects, signature helpers, optional locks or lazy fetching;
network transports are disabled. It checks the exact trees, second merge parent
and reviewed-head/merge tree equality. Squash/rebase or unequal-tree merges are
unsupported and rejected instead of being silently treated as equivalent.
A draft may specify no merge and no post-merge run; the index then states
**NOT VERIFIED** and asserts no merge.

Only fixed filenames below are read. JSON cannot supply filenames, local paths,
URLs, free-form annotations, human decisions or authorization fields. Unknown
input-envelope keys and unsupported schemas are rejected. Raw GitHub metadata
may include extra fields, but only validated IDs, enum statuses and derived
canonical links enter the output. Unknown participant names, tokens, answers,
response text, object keys, private URLs, credentials, exception details and raw
logs are never copied. This is a field projection, not a general-purpose
sanitizer for arbitrary text.

## Stage a private bundle

Create a fresh directory such as `/private/tmp/prufai-d01-input.XXXXXX` with
permissions `0700`, owned by the operator. Stage files with no group/other write
permission. Symlinks, hardlinks, special files, traversal paths and symlinked
ancestors are rejected. Each input is bounded to 32 MiB. Archives must have
unique, flat filenames without symlinks; selected members are bounded, read in
memory and never extracted. Larger or differently structured evidence needs a
separately reviewed schema extension.

`index-input.json` has exactly these keys (replace placeholders with reviewed
identities; this illustrative envelope is not executable evidence):

```json
{
  "schema": "prufai-release-evidence-input/1",
  "repository": "moaltamimi-unbiasedtalent/PrufAI",
  "as_of": "2026-11-25T00:00:00Z",
  "profile": "SYNTHETIC_TEST",
  "example_only": false,
  "sources": {
    "head": {"sha": "<40 lowercase hex>", "tree": "<40 lowercase hex>"},
    "merge": {"sha": "<40 lowercase hex>", "tree": "<same reviewed tree>"}
  },
  "runs": {
    "head": {"application": 101, "supply-chain": 102},
    "merge": {"application": 201, "supply-chain": 202}
  }
}
```

For an unmerged PR, set both `sources.merge` and `runs.merge` to `null` and stage
no `merge-*` files. An unknown workflow ID may also be `null`; do not substitute
an older run. IDs above are examples, not observed GitHub runs.

For each available `head` or `merge` slot:

| Fixed input | Expected capture |
| --- | --- |
| `<slot>-application-run.json` | Complete API response for the exact application run. Repository, workflow path, event, SHA, ID, attempt and status must agree. |
| `<slot>-application-jobs.json` | Complete, untruncated jobs response for that run; job IDs, source SHAs and run IDs must match. |
| `<slot>-supply-chain-run.json`, `<slot>-supply-chain-jobs.json` | Equivalent exact-source Supply-chain captures. |
| `<slot>-python.log` | Downloaded Python job log, including its exact checkout SHA, required-checker summaries and applied-head observation. Do not provide a manually asserted count. |
| `<slot>-artifacts.json` | Complete API artifact-list response for that slot's Supply-chain run. Names, IDs, digest, run identity and expiry are validated. |
| `<slot>-containers.zip`, `<slot>-language.zip` | Original, unmodified downloaded ZIP bytes corresponding to that metadata. Both archive summaries must match the source and scope. The container archive also contains the unchanged E06A files. |

Missing optional records yield NOT VERIFIED or UNAVAILABLE. Expired metadata or
expiry at/before `as_of` yields UNAVAILABLE even when cached bytes remain. The
explicit UTC snapshot time makes output repeatable; it is not a live availability
claim. Refresh metadata and capture a new snapshot to reassess access. Artifacts
in the current workflow have 30-day retention and can disappear sooner.

The parser derives expected report signatures and a single migration head from
the exact Git revision, without executing migration code or opening a database.
It checks actual report lines, refuses duplicates/conflicts, keeps incomplete
sets unverified, and keeps general API sweep skips outside required totals.
Nonzero required skips cannot accompany a reported successful application run.
An applied head is shown only when the log contains one matching observation;
E06A's rehearsal head is independently checked against the same source graph.

## Generate and verify locally

Use Python 3.11+ and an existing complete local source checkout:

```bash
python3 scripts/release-evidence-index/index.py \
  --bundle /private/tmp/prufai-d01-input.XXXXXX \
  --repository /absolute/path/to/PrufAI \
  --output /private/tmp/prufai-d01-output.NEW
cd /private/tmp/prufai-d01-output.NEW
sha256sum --check release-evidence-index.sha256
```

On macOS, use `shasum -a 256 --check release-evidence-index.sha256` if GNU
`sha256sum` is not installed. Output must be a new directory outside the source
and input bundle. Existing output, symlinked parents and overlapping paths are
refused. Generation reads all evidence before publishing three owner-only files:

- `release-evidence-index.json`: canonical sorted JSON, one trailing newline.
- `release-evidence-index.md`: reviewer navigation and explicit limitations.
- `release-evidence-index.sha256`: checksums of those two outputs.

The same inputs, source objects and `as_of` produce the same bytes. Input files,
E06A manifests, archives, source trees and operational state are not rewritten.
The CLI emits fixed success/failure messages without input paths or raw errors.
Diagnostic probes run in privately captured child processes. A deliberately
unsafe parser control must cause two expected test failures while leaving zero
canary occurrences in the outer pytest report; failure output is never relayed.

## Example and regression commands

[Generated synthetic example](example.md) uses deterministic disposable Git
objects, fictitious run/artifact IDs and small synthetic reports. It is marked
EXAMPLE_ONLY, suppresses external evidence links and is not a release receipt.
The [tests](../../../services/api/tests/test_release_evidence_index.py) reproduce
it without GitHub credentials, provider access or production services.

```bash
PYTHONPATH=services/api python3 -m pytest services/api/tests/test_release_evidence_index.py
python3 -m ruff check --config services/api/pyproject.toml scripts/release-evidence-index
python3 -m ruff format --check --config services/api/pyproject.toml scripts/release-evidence-index
```

## Independent acceptance handoff

Review source pinning and merge handling; compare genuine captures with the
underlying authenticated workflow; exercise malformed JSON, source/digest/run
mismatches, duplicates, incomplete reports, missing/expired artifacts, unsafe
paths/archives, the lazy-fetch negative control, canary exclusion, fixed CLI
errors and deterministic checksums. Confirm links address the intended Git
objects, runs, jobs and artifacts. Verify no E06A schema/generator change, then
inspect exact-head Python/Web/Browser/Supply-chain CI and actual required reports.
Keep local parsing/checksum evidence distinct from GitHub authentication,
informational scans, independent human acceptance and formal closure.

R81-01, backend-guard P2, legacy-fence P2, P1 acceptance, H10, PR #60 P2,
E02A-S1 and merge-review exceptions remain OPEN pending authorized decisions.
E05 historical startup timeout ROOT CAUSE UNRESOLVED. E03A BLOCKED.
No production, pilot, official-content or certificate authority is granted.
