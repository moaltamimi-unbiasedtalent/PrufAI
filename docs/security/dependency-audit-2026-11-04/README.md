# Dependency security audit and remediation — 4 November 2026

Repository: `moaltamimi-unbiasedtalent/PrufAI`.
Baseline: merged PR #31, main `e7752acbecb47a1182f7c2d39a4f2f2822ad311f`.
The post-merge baseline passed exact-head [main CI 41864261704](https://github.com/moaltamimi-unbiasedtalent/PrufAI/actions/runs/41864261704), including 172 frontend tests, 17 browser journeys and 89 required Speaking PostgreSQL/MinIO cases with zero required skips.
This is dependency security work, not pilot or production security certification.

## Evidence and count semantics

The actual before audits used the committed npm v3 lock and isolated Python 3.11
targets. Historical installation warnings were not used as advisory evidence.
The auditor ran separately from the Python application targets through `--path`.
First-party application code is covered by tests and review, not by a PyPI
advisory-database assertion.

| Captured before target | Actual result | Counting unit |
|---|---|---|
| npm, all dependencies | 3 critical, 2 high, 19 moderate, 0 low | 24 affected package records, 16 unique GHSAs |
| npm, production only | 1 critical, 1 high | Next.js and its PostCSS child; 6 unique direct/inherited GHSAs |
| Combined API/AI development, CI-aligned install | 1 critical, 5 high, 6 medium, 2 low | 14 unique GHSAs; 25 feed records include duplicated aliases |
| API runtime dependencies | No known runtime finding | Initial venv pip/setuptools findings are bootstrap tools |
| AI runtime dependencies | 1 critical, 5 high, 5 medium, 1 low | 12 unique advisories in unused framework dependencies |

See the complete npm and Python inventories for every affected package/version,
dependency path, GHSA/CVE alias, affected range, available fixed version, exposure
and disposition. Counts from different ecosystems are not added together.

## Actual supported-runtime after audits

- npm all-dependency audit: **0 critical, 0 high, 18 moderate, 0 low**. The eighteen
  affected development packages propagate one remaining minimatch GHSA.
- npm production audit: **0 known findings**, exit 0.
- Python 3.11 fresh combined development, API production and AI production targets:
  **0 known findings**, auditor exit 0 and `pip check` clean for all three. The
  targets inventoried 63/45/23 distributions and audited 61/44/22 third-party
  distributions. The remaining 2/1/1 records are unpublished first-party editable
  packages, outside PyPI vulnerability lookup rather than missing dependencies.
- All fifteen unique IDs across the original captured Python before scopes are
  absent from the supported after targets. The separate legacy resolution findings
  are preserved as a rejected candidate, not conflated with original-main counts.
- Separately pinned Playwright 1.56.1 npm tool audit: **0 known findings**. Chromium
  binary and OS-package vulnerability status is outside that npm result.

## Bounded changes

1. Next.js and matching ESLint configuration: **15.5.27 → 15.5.29**. The Next-owned
   PostCSS dependency is scoped to **8.5.23** within the same PostCSS major. Next
   15.5.29 still declares vulnerable PostCSS 8.4.31, so the framework patch alone
   is insufficient. React/React DOM 19.1.1, TypeScript and ESLint stay unchanged.
2. Development-only Vitest: **3.2.7 → 4.1.12**, with its coherent companion package
   family. No fixed Vitest 3 release exists. Vitest 4 removes Tinypool and fixes
   both mocker advisories. Vite stays within its existing major at **7.3.7**. The
   documented Vitest 4 mock/pool behavior requires all existing tests and typecheck
   to pass; no assertion or test is weakened for compatibility.
3. Remove unused `langchain-core==0.3.79` and `langgraph==0.6.7` declarations. No
   application, worker, script or entry point imports or dynamically loads these
   SDKs. Existing local Python/Pydantic orchestration and provider workers remain.
   Only the stale module docstring changes; historical architecture reports remain.
4. Explicitly require **Python >=3.11** for both services, matching the established
   CI baseline. A real Python 3.9 audit of the SDK-removal-only candidate still found
   critical/high runtime vulnerabilities. Its old FastAPI→Starlette and botocore→
   urllib3 constraints cannot resolve the patched releases. Python 3.9 and 3.10 are
   both end-of-life; keeping that former support declaration would be misleading.
5. Declare patched HTTP-stack minimums: **AnyIO >=4.14.2,<5**, **Starlette
   >=1.3.1,<2** and **python-dotenv >=1.2.2,<2** for both services; **urllib3
   >=2.8,<3** for the API's botocore path. This also rejects vulnerable older
   resolutions on a supported interpreter. The modern installed runtime versions
   already satisfy these floors; domain/API behavior is unchanged.
6. Upgrade the test family to **pytest >=9.0.3,<9.1**. The first fixed 9.0 patch
   release is an intentional test-tool major change. Build isolation requires
   **setuptools >=84.1,<85**; both Python CI jobs install patched **pip >=26.2,<27**
   and setuptools. Tested versions are pytest 9.0.3, pip 26.3 and setuptools 84.2.1.
7. Use **Node 24 LTS** in both Node CI jobs, matching the passing local frontend,
   build and browser environment. Node 20 is end-of-life. Next/React application
   majors are not changed, and no deployment image is provisioned or upgraded.
8. Add a plain `npm audit --omit=dev` CI gate. Retain a unique mode-0700 pytest CI
   temporary parent as defense in depth, now alongside the upstream pytest fix.
   Existing format, lint, typecheck, tests, builds and real-service gates remain.

The npm lock is generated by npm, not manually edited. No forced update, secret
change, migration, history rewrite, automatic merge or assessment feature is included.

## Applicability and priority

Next.js is an actual production server dependency. The upstream image-optimization
RCE requires its vulnerable native-image path on glibc Linux. The baseline lock
already contains Sharp 0.35.5, satisfying the maintainer's workaround; no Next Image
usage or SVG override was found. The Server Actions DoS requires at least one
registered action; no `use server` action was found and the candidate production
Server Action manifest contains zero Node and zero Edge entries. These observations reduce
demonstrated exploitability; they do not erase the package advisories. Patch the
framework within the supported 15.5 family rather than relying on those conditions.

PostCSS is present in the production tree through Next. No request-controlled CSS
processing or CSS/HTML injection path was found; its role is the application build
pipeline. The file-read/source-map and CSS-stringification advisories are still
removed with a compatible scoped update.

Vitest, Tinypool, Vite and lint tooling are development/CI dependencies. Tests use
Node-only `vitest run`, with no exposed Vitest browser/UI/API or standalone mocker
server. Development status is not treated as immunity: Tinypool's execution
gadgets and Vite's affected development paths are removed or patched.

The LangChain/LangGraph packages were declared production dependencies despite no
upstream execution path. Their unsafe serialization, template, checkpoint and SDK
advisories are eliminated by removing unused dependencies, avoiding a needless
runtime SDK major migration. New upstream SDK use must be a separately reviewed,
versioned implementation with current security evidence.

## Explicit residuals and follow-up gates

- **Minimatch GHSA-g3hv-75wg-m249 / CVE-2026-118764:** 18 development package records
  (minimatch plus 17 inherited/meta-vulnerabilities), one advisory. npm's affected
  range includes all versions below 11.0.1. The old callable minimatch 3 API is used
  by ESLint/glob consumers; a global major override would break those consumers.
  The nested minimatch 10 call uses default optimization level 1, whereas the
  maintainer describes the level >=2 path. No production use was found. Do not
  suppress or relabel the finding. Follow up with a supported lint-family
  migration or compatible upstream backports; require current audits, lint,
  typecheck, all frontend tests, build and browser acceptance.
- **Python 3.9/3.10 are intentionally unsupported after this change.** The rejected
  Python 3.9 candidate had 15 unique API-runtime findings (1 critical, 8 high,
  5 medium, 1 low), and 8 AI-runtime findings (1 critical, 2 high, 4 medium, 1 low).
  Those are candidate resolution counts after SDK removal, not original-main counts.
  The legacy report records exact dependency constraints and patched-release Python
  requirements. Create a fresh Python 3.11+ environment for installation; no database
  reset or evidence deletion is involved. The earlier provisional plan to retain
  pytest 8 is superseded: pytest 9.0.3 fixes its advisory in supported environments.
- Use a clean build with the patched bootstrap/build-tool bounds. Installing over
  an old environment can retain orphaned vulnerable packages. Python 3.9 bootstrap
  findings are preserved as evidence, not treated as a supported after result.
- Python has no committed lock/constraints, so captured resolved inventories are
  timestamped evidence, not a guarantee about a future fresh resolution. Explicit
  security floors reject the known older affected HTTP versions. Maintain
  supported versions and repeat audits for deployment builds.
- Removing declarations does not uninstall orphaned packages from an existing
  environment. Deployment requires a clean dependency installation/image rebuild
  and a new audit. No deployment or running-environment cleanup was performed.
- The separately pinned Playwright 1.56.1 npm dependency tree was audited with zero
  known findings; Chromium binary and browser OS security remain unverified.
  Actions, container/OS and Go dependencies, floating image/action tags and the
  legacy Compose mismatch are inventoried separately. Their vulnerability scan
  status is **NOT VERIFIED**. They are not guessed CVEs or silently upgraded here.
- Authentic model inference, linguistic validity, authorization review, deployment
  hardening, penetration testing and pilot security readiness are separate gates.
  The existing AI browser journeys remain explicitly TEST ONLY.

## Verification boundary

The final supported-runtime candidate passed the full local regression under
**Python 3.11.15 / pytest 9.0.3 / Node 24.15.0**: **942 API tests, zero failures,
errors or skips**, actual PostgreSQL/private MinIO and both Redis cases; **7 AI
service tests**; frontend format/lint/typecheck, **172 tests across 18 files** and
production build; **17 authenticated browser journeys, zero retries/skips**; Ruff,
format (302 files), mypy API (168 source files) and AI (4 source files). Fresh owned
databases migrated to the single dynamic head `0029_speaking_execution`.

`local-verification.json` records exact counts and capture hashes. Every baseline-
tracked file in the final tested candidate was compared byte for byte with the
security branch; `candidate-source-checksums.json` records that check. The only
application Python code edit is its module docstring, and its non-docstring runtime
AST is unchanged. No test, migration, RBAC or assessment/release implementation is
modified. Final actual-branch audits and manifest provenance are in the after
artifacts. First-party editable packages are excluded from public database lookup,
not from the captured third-party dependency graph or application regressions.

Remote acceptance belongs to the exact-head PR checks and closure description;
this timestamped dependency snapshot does not substitute for remote CI.

No dependency audit alone establishes PILOT SECURITY READY.
