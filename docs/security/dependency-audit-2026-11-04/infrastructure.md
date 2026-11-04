# PrüfAI dependency security — infrastructure and toolchain inventory

Repository: `moaltamimi-unbiasedtalent/PrufAI`

Inspected baseline: `origin/main` at `e7752acbecb47a1182f7c2d39a4f2f2822ad311f`

Method: read-only tracked-file inspection and local tool version commands.

Scope: baseline dependency/deployment inventory plus explicitly labelled current security-branch changes and audit-coverage limits. The inventory writer made no repository/source changes. The security PR’s supported-runtime/dependency/CI changes are identified separately below.

**Container/OS/Go/Actions vulnerability status: NOT VERIFIED.** No container scanner, OS-package auditor, Go vulnerability scanner or GitHub Actions dependency/security scan was executed. A later isolated npm audit of the exact Playwright 1.56.1 package tree completed successfully with zero known findings; this does not audit Chromium binaries or their OS dependencies. An exact version, source release, checksum or image digest is provenance evidence; it does not establish that the component is free of vulnerabilities. No CVEs are guessed from package age, floating tags or historical installation output.

## Package-manager and application dependency boundaries

- The only tracked npm application is `apps/web/package.json` with `apps/web/package-lock.json`, npm lockfile version 3. There is no root `package.json`, npm workspace configuration, pnpm/yarn lockfile or other tracked installable JS package. `packages/shared-contracts/README.md` is a placeholder.
- At the inspected baseline, API and AI declarations are `services/api/pyproject.toml` and `services/ai/pyproject.toml`, both declaring Python `>=3.9`. The current security branch explicitly raises both floors to `>=3.11`, as documented below. Python lockfiles, requirements files and constraints files are absent from the baseline tracked tree. Runtime ranges are therefore resolved by pip when installed. The Python audit report must distinguish the concrete audited resolution from every possible allowed future resolution.
- The browser acceptance tool is installed separately from the application: `@playwright/test@1.56.1` in an isolated runner directory. The browser harness loads it through `PRUFAI_BROWSER_TOOL_DIR`/`NODE_PATH`. It is not an application production dependency.
- At baseline, no repository-owned npm/pip/container/Go security-audit CI step, Dependabot configuration, CodeQL configuration, Trivy configuration or OSV scanner configuration was found. The security branch adds the explicit production npm audit described below. Historical implementation reports mention earlier npm audits or unavailable Python tooling; they are historical reports, not current execution evidence.

## Actual local toolchain observations

Read-only version commands produced the following on the audit workstation. These observations do not claim current upstream support or security status.

| Tool | Observed executable/version |
|---|---|
| Host OS | macOS `27.0.1`, build `26A434` |
| Node.js | `/usr/local/bin/node`, `v24.15.0` |
| npm | `11.12.1` |
| Python | `/usr/bin/python3`, `3.9.6` |
| Docker CLI | `29.7.2`, build `a7dcaa6fdb` |
| Docker Compose | `5.5.0` |
| Host Go | Not found on the inspected PATH |

At baseline, local Node/Python versions differed from declared CI. The security branch now selects Node 24 for both Node jobs, matching the locally tested Node 24 family. Its explicit Python floor is >=3.11, so the observed system `/usr/bin/python3` 3.9.6 is no longer a supported application interpreter; the remediation audit and complete regression use isolated Python 3.11.15 environments. Passing local checks does not replace exact-final-SHA CI. Missing host Go does not require a workstation installation to use the supported local MinIO Docker build, whose build stage supplies Go itself.

## GitHub Actions inventory

The single tracked workflow is `.github/workflows/ci.yml`. It runs on pushes to `main` and `phase/**`, pull requests and explicit workflow dispatch. A `security/**` branch relies on its pull-request event for the required run.

| Item | Actual baseline declaration | Pinning/verification boundary |
|---|---|---|
| Jobs | `web`, `python`, `browser` | Preserve all three jobs and their existing application checks. |
| Runner | `ubuntu-latest` | Floating runner image; no runner-OS vulnerability scan performed. |
| Checkout | `actions/checkout@v4` | Mutable major tag, not an immutable commit SHA. |
| Node setup | `actions/setup-node@v4`; Node `20` | Action major tag and Node patch selection float. |
| Python setup | `actions/setup-python@v5`; Python `3.11` | Action major tag and Python patch selection float. |
| Go setup | `actions/setup-go@v5`; Go `1.24.x` | Action major tag and Go patch selection float. |
| Artifact upload | `actions/upload-artifact@v4` | Mutable major tag. Browser evidence is uploaded only on failure, retained seven days. |
| PostgreSQL service | `pgvector/pgvector:pg16` | Floating image tag; health uses `pg_isready`. |
| Redis service | `redis:7` | Floating image tag; health uses `redis-cli ping`. |
| MinIO binary | `go install github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z` | Exact source-release identifier; no Go transitive audit or resulting-binary scan performed. |
| Audio runtime | Existing `scripts/setup-media-runtime.sh`, image `prufai-media:ffmpeg-6.1.1` | Built from the repository Dockerfile below. No image/OS scan performed. |
| Browser tooling | `@playwright/test@1.56.1`; Chromium installed by its CLI | Separate exact top-level test tool. Its isolated npm dependency tree later audited with zero known findings; Chromium binary/OS packages remain NOT VERIFIED. |

The `web` job uses `npm install`; `browser` uses `npm ci`. Both consume the committed application manifest/lock. The Python job upgrades pip and installs API/AI development extras through pip. These commands are actual workflow inventory, not proof that every future resolution is identical to the audited environment.

CI creates actual PostgreSQL/pgvector and Redis services. Its pinned MinIO source installation starts a real S3-compatible process, waits for readiness, verifies the process remains alive, and initializes a bucket. Do not substitute memory storage or accept an integration step that merely collected/skipped tests.

## Container images and build sources

### Audio inspection runtime

Source: `infra/media-probe.Dockerfile`; build entry point: `scripts/setup-media-runtime.sh`.

```dockerfile
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg=7:6.1.1-3ubuntu5 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /input
USER 65534:65534
ENTRYPOINT ["/usr/bin/ffprobe"]
```

The Ubuntu base is pinned by digest and the named FFmpeg Debian package by version. The `apt-get update` repository and other resolved OS package versions are not a repository-owned complete OS lock/SBOM. The setup script performs an offline, read-only, capability-dropped version invocation; that is runtime/provenance verification, not a vulnerability scan. Preserve the existing bounded media execution/security settings during application dependency remediation.

### Supported private audio MinIO runtime

Source: `infra/minio-source.Dockerfile`; operational entry point: `scripts/local-audio-storage.sh`; bucket verification: `scripts/initialize-audio-storage.py`.

| Stage/item | Exact baseline value |
|---|---|
| Build base | `golang:1.24.13-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac` |
| Go build mode | `CGO_ENABLED=0` |
| MinIO source | `github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z` |
| Final base | `ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852` |
| Final identity | Non-root UID/GID `10001` |
| Image tag built locally | `prufai-minio:RELEASE.2025-10-15T17-29-55Z` |
| Dedicated local publication | `127.0.0.1:19000:9000` and `127.0.0.1:19001:9001` |

The supported script uses an explicitly labelled dedicated container and persistent owned volume. It requires explicit local storage credentials/bucket, preserves objects when stopped and verifies private bucket policy. It contains no reset/delete/anonymous-policy path. This inventory did not start or stop it, change its credentials, alter volumes, or scan its binaries/OS/Go modules.

### Legacy Compose configuration and known workflow gap

Source: `infra/docker-compose.yml`.

| Service | Image declaration | Boundary/gap |
|---|---|---|
| PostgreSQL | `postgres:16-alpine` | Plain PostgreSQL image, unlike the required CI `pgvector/pgvector:pg16`. The existing vector-extension migrations require PostgreSQL with pgvector available. |
| Redis | `redis:7-alpine` | Floating tag; differs from CI `redis:7`. No image/OS audit performed. |
| MinIO | `minio/minio:RELEASE.2025-04-22T22-12-26Z` | Older release than the supported audio/CI source build; not pinned to an image digest. Do not treat its existence as evidence for the official real-audio gate. |

Compose maps PostgreSQL/Redis/MinIO ports using host-port syntax without loopback-only addresses and supplies development credential defaults. It is a development configuration, not a verified production exposure/authentication policy. The supported dedicated audio script instead uses explicit loopback publication. No credential values need to be copied into this report.

`README.md` already points recorded-audio users to `docs/recorded-audio.md` and the supported private MinIO/media workflow. Its general product-state paragraphs remain older than the integrated assessment implementation. The plain-PostgreSQL Compose mismatch and documentation drift are explicit follow-up items; they were not silently repaired or used to justify unrelated image upgrades in this dependency PR.

## Deployment and application compatibility constraints

No tracked production API/frontend deployment Dockerfile, Kubernetes/Helm configuration, release/deployment workflow or additional application image was found. The tracked infrastructure files are the Compose file and two purpose-specific Dockerfiles above. Deployed production image digest, OS patch state, egress policy, TLS boundary, secret manager and hosted readiness are therefore NOT VERIFIED by this repository-only inventory.

The existing Makefile is the normal command entry point: frontend `npm` scripts under `apps/web`, FastAPI API on port 8000, the independent AI health boundary on port 8001, and infrastructure through Compose. The recorded-audio and full browser acceptance documentation narrows the appropriate PostgreSQL/MinIO workflow beyond the older general Compose example.

Preserve the following behavior while making dependency fixes:

- Next.js App Router, same-origin API rewrites, authentication/refresh/CSRF and account-partitioned offline response queues.
- Actual browser recording and private authenticated playback; original evidence, exact response/media binding and backend Range semantics.
- Existing HUMAN_ONLY and explicitly labelled AI_ASSISTED technical Writing/Speaking workflows, their separate durable workers, current RBAC and immutable history.
- Migration-backed PostgreSQL/pgvector, actual private MinIO and Redis integration paths.
- Mandatory independent human judgment, scoring/release authorization and certification default-deny. Dependency audit success does not establish linguistic accuracy or pilot readiness.

## Existing acceptance paths that must remain intact

The merged baseline defines 17 authenticated browser cases across nine specification files. This is source inventory, not a fresh execution claim about this baseline:

| Source | Existing coverage |
|---|---|
| `tests/browser/integrated-mvp.spec.cjs` | Objective three-application result/appeal, cross-role denial and account/logout offline partitioning. |
| `tests/browser/writing-human.spec.cjs` | Independent human Writing review, scoring/release/statement and negative authority. |
| `tests/browser/recorded-audio.spec.cjs` | Real synthetic recording, private persistence, exact binding, candidate/reviewer playback and Range behavior. |
| `tests/browser/recorded-audio-corrective.spec.cjs` | Recovery, reload interlocks, client clock skew and server expiry enforcement. |
| `tests/browser/speaking-human.spec.cjs` | Version-pinned independent human Speaking assessment and safe audit. |
| `tests/browser/speaking-results.spec.cjs` | Human Speaking approval/scoring/release/statement and unapproved/unauthorized negative cases. |
| `tests/browser/transcription-execution.spec.cjs` | Separate durable TEST ONLY transcription worker and exact derived evidence. |
| `tests/browser/ai-writing-execution.spec.cjs` | Actual separate TEST ONLY Writing worker, legitimate proposal and pending human review. |
| `tests/browser/ai-speaking-execution.spec.cjs` | Actual separate TEST ONLY Speaking worker, original audio provenance, legitimate proposal and pending human review. |

The official command is `bash scripts/run-integrated-browser.sh`. It requires a fresh isolated PostgreSQL/pgvector database, real S3/MinIO configuration, isolated pinned browser tooling and unused ports. It checks private bucket access, refuses occupied target ports before migrations/seed, asserts one dynamic Alembic head, upgrades the fresh database, reconciles supported development prerequisites, builds Next against the exact API target and runs all browser specification files. It does not drop/truncate/reset databases. It starts only its own API/frontend child processes and cleans up those children.

The baseline single head is `0029_speaking_execution`, naturally following `0028_writing_execution`. It is unchanged by this security work. The helper `services/api/tests/alembic_helpers.py` derives the current head dynamically; required recorded-audio, AI Writing and AI Speaking integration files use that helper. Migration creation or historical migration edits are not part of dependency remediation.

Required regressions remain the repository's frontend format/lint/typecheck/tests/build, Ruff/Ruff format, API and AI mypy, full API/AI tests, single-head Alembic upgrade, actual PostgreSQL/MinIO/Redis suites and all authenticated browser journeys. The durable Writing, recorded-audio, human Speaking, Speaking results, transcription and AI Speaking CI steps assert positive JUnit counts and zero required skips. General-suite optional-service skips must be reconciled against subsequent executed service-backed steps; never relabel them as direct general-suite passes.

## Current security-branch changes — distinct from the historical baseline

These are actual security-branch declarations and coordinator-verified local results, rather than changes implied by the baseline tables. The relevant source hashes match committed code at `43a8be5688a798bcbec9001ee5ca70891050ca0d`; the evidence/documentation commit can advance separately. Final exact-head remote acceptance must still be recorded in the PR closure.

| Area | Actual security-branch state | Compatibility/evidence boundary |
|---|---|---|
| CI Node toolchain | Both `web` and `browser` now declare `node-version: 24` | Node 24 is LTS; Node 20 is EOL according to the [official Node.js release table](https://nodejs.org/en/about/previous-releases), verified 4 November 2026. Local tested Node is 24.15.0. No claim that an arbitrary Node 20 execution is still a supported target. |
| Python runtime floor | API and AI each explicitly declare `requires-python = ">=3.11"` | An intentional supported-runtime compatibility change. Python 3.9 environments must recreate/upgrade their environment; they are not claimed to receive the fixed-toolchain envelope. CI remains Python 3.11. |
| Build tooling | Both build systems require `setuptools>=84.1,<85` | Patched build-tool floor. Audited isolated environments use patched pip/setuptools; source declarations are not a deployed-environment audit. |
| pytest | Both development extras require `pytest>=9.0.3,<9.1` | Fixes the prior temporary-directory advisory through the patched major, rather than relying solely on containment. The unique private 0700 CI temporary parent remains defense in depth. |
| API HTTP/environment safety floors | `anyio>=4.14.2,<5`, `starlette>=1.3.1,<2`, `python-dotenv>=1.2.2,<2`, `urllib3>=2.8,<3` | Explicit minimums selected from actual advisory evidence; no claim that old Python 3.9 resolutions remain supported. |
| AI HTTP/environment safety floors | `anyio>=4.14.2,<5`, `starlette>=1.3.1,<2`, `python-dotenv>=1.2.2,<2` | Same applicable patched floors; API-only urllib3 bound is not inaccurately attributed to the minimal AI service. |
| Unused framework attack surface | Unused LangChain/LangGraph declarations removed | Existing local Python/Pydantic orchestration and provider workers remain intact. Existing environments require clean rebuild/reinstallation to remove orphaned packages. |
| Web dependencies | Next/config 15.5.29; scoped Next→PostCSS 8.5.23; Vitest 4.1.12; Vite 7.3.7 | Coherent framework patch and explicitly documented test-tool major. React/React DOM and unrelated direct packages stay unchanged. |
| Production npm CI gate | Additive `npm audit --omit=dev` step | Preserves all existing format/lint/typecheck/test/build and service-backed checks. Full-development residuals remain visible rather than suppressed. |

Ruff `py39` and mypy `3.9` conservative syntax-check settings do not override the declared >=3.11 installation/runtime floor or certify a Python 3.9 execution environment.

The coordinator reports complete final local acceptance on the patched Python 3.11/pytest 9 source: **942 API tests passed with zero skips; 7 AI tests passed; all frontend formatting/lint/typecheck/build gates and 172 frontend tests passed; the repeated complete 17-journey authenticated browser suite passed.** The tested candidate’s 590 baseline-tracked files were matched byte-for-byte to the actual branch; see the portable `local-verification.json` and `candidate-source-checksums.json`. Those are local results, not final remote-CI claims.

Actual branch npm after audits contain **zero critical/high package findings, zero production findings and 18 residual moderate development records linked to one minimatch GHSA**. See `npm-after.md` and `npm-inventory-after.json` for raw-result links, exact before/after IDs, installed versions, source hashes and residual disposition. Do not describe the all-dependency audit as zero vulnerabilities.

The saved installed tree also listed two extraneous optional platform packages. A separate scripts-disabled disposable manifest/lock audited the exact `@emnapi/runtime@1.11.3` and `@img/sharp-wasm32@0.35.5` versions and their dependency graph: **zero known npm findings**. The independent result is in `extraneous-package-npm-audit.json`, with its manifest/lock alongside. It is separate from the application’s 24-before/18-after package counts; no installer cause, wasm/native-binary safety or clean-tree claim is inferred.

No Actions/image/container/OS/Go security verdict follows from these application or isolated-package npm results. Chromium binaries and browser OS packages also remain NOT VERIFIED. Final deployment, hosted security, genuine inference and pilot readiness remain separate gates.

## Explicit residual coverage and bounded follow-ups

| Item | Current status | Recommended separately bounded follow-up |
|---|---|---|
| GitHub Actions referenced code | NOT VERIFIED by security scan; mutable major tags inventoried | Evaluate current action support/advisories and pin reviewed immutable action SHAs without dropping existing permissions/checks. |
| Runner, PostgreSQL, Redis and legacy MinIO image contents | NOT VERIFIED | Capture exact deployed/resolved digests and scan generated SBOMs with a maintained image/OS auditor; remediate from actual findings and rerun relevant integrations. |
| MinIO Go dependency graph/binary | NOT VERIFIED | Audit the exact source release/build resolution using maintained Go tooling and capture binary/module provenance; do not infer CVEs from release age alone. |
| Ubuntu/FFmpeg packages | NOT VERIFIED | Scan the actual built media image and record OS package versions/SBOM; validate any patch under existing sandbox/validation and real-audio regressions. |
| Isolated browser-tool npm dependencies | PASS in separate exact Playwright 1.56.1 audit: zero known npm findings | See `playwright-npm-audit.json` and `playwright-audit-lock.json`; Chromium binary and OS-package security remain NOT VERIFIED. No browser-tool update was made. |
| Legacy Compose PostgreSQL/MinIO mismatch | Confirmed source/workflow gap, not a claimed CVE | Align a separately reviewed local-dev workflow with pgvector/private storage while preserving volumes and requiring explicit approval for destructive data operations. |
| README/product documentation drift | Confirmed source drift | Reconcile current supported commands/status in a bounded documentation change; retain truthful local versus hosted/production boundaries. |
| Hosted deployment and pilot security | NOT VERIFIED | Verify actual deployment, authorization, operations and wider security requirements separately; dependency audits alone cannot establish PILOT SECURITY READY. |

No floating-tag, Compose, image, credential, worker, migration, RBAC, application-security or deployment change was made for this inventory. The findings above are explicit residual coverage/follow-up records, not invented vulnerability counts and not permission to start another assessment phase.
