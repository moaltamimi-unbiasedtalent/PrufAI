# PrüfAI npm advisory inventory — before remediation

Repository: `moaltamimi-unbiasedtalent/PrufAI`

Baseline main: `e7752acbecb47a1182f7c2d39a4f2f2822ad311f`

Scope: actual machine-readable npm audits and installed dependency explanations, joined to committed npm lock v3. No manifests or repository files changed while building this inventory.

**All dependencies: 24 affected packages — 3 Critical, 2 High, 19 Moderate, 0 Low.**

**Production-only audit: 2 affected packages — 1 Critical (Next.js), 1 High (PostCSS).**

These package totals are not counts of independent CVEs. There are **16 deduplicated advisory IDs**. The minimatch finding accounts for **18 affected package records: minimatch plus 17 inherited/meta-vulnerabilities**, all linked to one GHSA. npm can propagate severity through dependencies and peers; preserving each package record does not establish a separate exploit.

## Proposed bounded changes and compatibility limits

- Patch Next.js 15.5.27 → 15.5.29 with matching eslint-config-next 15.5.29. Use only a scoped Next→PostCSS 8.5.23 override; the Vite-owned PostCSS 8.5.28 is already outside these ranges.
- Use a documented development-tool major update, Vitest 3.2.7 → 4.1.12, with Vite 7.3.7 pinned within the existing Vite 7 family. Vitest 4 removes Tinypool, aligns fixed @vitest/mocker 4.1.12 and supports current CI Node 20. Vitest 5 would additionally require Node >=22.12; it is unnecessary for the listed advisories.
- [Vitest 4 migration guide](https://v4.vitest.dev/guide/migration.html) documents worker-pool, mock-restoration, constructor-mock and exclusion changes. The repository uses Node-only vitest run and no custom pools/coverage/browser/UI, but all existing tests/typecheck/lint/build must pass; no compatibility success is claimed before execution.
- Do not force a global minimatch 11 override or accept npm suggestions to downgrade ESLint to 4.0.0 / eslint-config-next to 0.2.4. Root v3 has callable CommonJS API, v11 an object API; legacy consumers would break. Nested TypeScript-ESLint ^10.2.2 also excludes v11. Retain an explicit development-only residual with a supported lint-family migration/backport plan.
- Audit zeroes, production exploitability, pilot readiness and successful remediation are not claimed by this BEFORE inventory.

## Deduplicated source advisories

| Advisory / CVE | npm source severity | Affected package ranges → patched versions | Primary source |
|---|---|---|---|
| [GHSA-2464-fhr4-w9xw](https://github.com/vercel/next.js/security/advisories/GHSA-2464-fhr4-w9xw) / NOT ASSIGNED | critical | next >= 10.0.0, <= 15.5.27 → 15.5.28; next >= 16.0.0, <= 16.4.0 → 16.4.1 | [maintainer/advisory](https://github.com/vercel/next.js/security/advisories/GHSA-2464-fhr4-w9xw) |
| [GHSA-587h-7wmj-h5pm](https://github.com/vitejs/launch-editor/security/advisories/GHSA-587h-7wmj-h5pm) / CVE-2026-112942 | high | launch-editor <= 2.14.1 → 2.14.2; vite >= 8.3.0, <= 8.3.2 → 8.3.3; vite >= 8.2.0, <= 8.2.3 → 8.2.4; vite >= 8.0.0, <= 8.1.5 → 8.1.6; vite >= 7.0.0, <= 7.3.6 → 7.3.7; vite <= 6.4.3 → 6.4.4; vite-plus <= 1.0.0 → 1.1.0 | [maintainer/advisory](https://github.com/vitejs/launch-editor/security/advisories/GHSA-587h-7wmj-h5pm) |
| [GHSA-5gmw-xhrv-c9v3](https://github.com/tinylibs/tinypool/security/advisories/GHSA-5gmw-xhrv-c9v3) / CVE-2026-104848 | critical | tinypool <= 2.1.0 → 2.1.1 | [maintainer/advisory](https://github.com/tinylibs/tinypool/security/advisories/GHSA-5gmw-xhrv-c9v3) |
| [GHSA-698v-4r3x-3vcv](https://github.com/vitejs/launch-editor/security/advisories/GHSA-698v-4r3x-3vcv) / CVE-2026-112943 | moderate | launch-editor <= 2.14.1 → 2.14.2; vite >= 8.3.0, <= 8.3.2 → 8.3.3; vite >= 8.2.0, <= 8.2.3 → 8.2.4; vite >= 8.0.0, <= 8.1.5 → 8.1.6; vite >= 7.0.0, <= 7.3.6 → 7.3.7; vite <= 6.4.3 → 6.4.4; vite-plus <= 1.0.0 → 1.1.0 | [maintainer/advisory](https://github.com/vitejs/launch-editor/security/advisories/GHSA-698v-4r3x-3vcv) |
| [GHSA-6g55-p6wh-862q](https://github.com/postcss/postcss/security/advisories/GHSA-6g55-p6wh-862q) / CVE-2026-45623 | high | postcss <= 8.5.11 → 8.5.12 | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-6g55-p6wh-862q) |
| [GHSA-82fw-gwwq-j7x9](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9) / CVE-2026-84373 | moderate | @vitest/mocker >= 2.1.0, < 4.1.11 → 4.1.11; vitest >= 2.1.0, < 4.1.11 → 4.1.11; vitest >= 5.0.0-beta.1, < 5.0.0-rc.2 → 5.0.0-rc.2; @vitest/mocker >= 5.0.0-beta.1, < 5.0.0-rc.2 → 5.0.0-rc.2 | [maintainer/advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9) |
| [GHSA-85c8-ppgw-ccpr](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr) / CVE-2026-104849 | critical | tinypool < 2.1.2 → 2.1.2 | [maintainer/advisory](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr) |
| [GHSA-9jrq-w75r-8gcw](https://github.com/vitejs/vite/security/advisories/GHSA-9jrq-w75r-8gcw) / CVE-2026-112945 | low | vite >= 8.3.0, <= 8.3.2 → 8.3.3; vite >= 8.2.0, <= 8.2.3 → 8.2.4; vite >= 8.0.0, <= 8.1.5 → 8.1.6; vite >= 7.0.0, <= 7.3.6 → 7.3.7; vite <= 6.4.3 → 6.4.4; vite-plus <= 1.0.0 → 1.1.0 | [maintainer/advisory](https://github.com/vitejs/vite/security/advisories/GHSA-9jrq-w75r-8gcw) |
| [GHSA-fxqj-rqcc-2cmp](https://github.com/postcss/postcss/security/advisories/GHSA-fxqj-rqcc-2cmp) / CVE-2026-69153 | moderate | postcss <= 8.5.22 → 8.5.23 | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-fxqj-rqcc-2cmp) |
| [GHSA-g3hv-75wg-m249](https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249) / CVE-2026-118764 | moderate | minimatch < 11.0.1 → 11.0.1 | [maintainer/advisory](https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249) |
| [GHSA-p3m5-59mp-jv5r](https://github.com/vercel/next.js/security/advisories/GHSA-p3m5-59mp-jv5r) / CVE-2026-94482 | high | next >= 13.0.0, <= 15.5.27 → 15.5.28; next >= 16.0.0, <= 16.4.0 → 16.4.1 | [maintainer/advisory](https://github.com/vercel/next.js/security/advisories/GHSA-p3m5-59mp-jv5r) |
| [GHSA-qx2v-qp2m-jg93](https://github.com/postcss/postcss/security/advisories/GHSA-qx2v-qp2m-jg93) / CVE-2026-41305 | moderate | postcss < 8.5.10 → 8.5.10 | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-qx2v-qp2m-jg93) |
| [GHSA-qxg6-89mg-rqv8](https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8) / CVE-2026-112949 | moderate | @vitest/mocker >= 2.1.0-beta.7, < 4.1.12 → 4.1.12; @vitest/mocker >= 5.0.0-beta.1, < 5.0.3 → 5.0.3 | [maintainer/advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8) |
| [GHSA-r28c-9q8g-f849](https://github.com/postcss/postcss/security/advisories/GHSA-r28c-9q8g-f849) / CVE-2026-73646 | high | postcss <= 8.5.17 → 8.5.18 | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-r28c-9q8g-f849) |
| [GHSA-r825-w8mm-5pxw](https://github.com/vitejs/launch-editor/security/advisories/GHSA-r825-w8mm-5pxw) / CVE-2026-112944 | moderate | launch-editor <= 2.14.1 → 2.14.2; vite >= 8.3.0, <= 8.3.2 → 8.3.3; vite >= 8.2.0, <= 8.2.3 → 8.2.4; vite >= 8.0.0, <= 8.1.5 → 8.1.6; vite >= 7.0.0, <= 7.3.6 → 7.3.7; vite <= 6.4.3 → 6.4.4; vite-plus <= 1.0.0 → 1.1.0 | [maintainer/advisory](https://github.com/vitejs/launch-editor/security/advisories/GHSA-r825-w8mm-5pxw) |
| [GHSA-rq7h-c2jc-7f22](https://github.com/vitejs/vite/security/advisories/GHSA-rq7h-c2jc-7f22) / CVE-2026-112946 | moderate | vite >= 8.3.0, <= 8.3.2 → 8.3.3; vite >= 8.2.0, <= 8.2.3 → 8.2.4; vite >= 8.0.0, <= 8.1.5 → 8.1.6; vite >= 7.0.0, <= 7.3.6 → 7.3.7; vite <= 6.4.3 → 6.4.4; vite-plus <= 1.0.0 → 1.1.0 | [maintainer/advisory](https://github.com/vitejs/vite/security/advisories/GHSA-rq7h-c2jc-7f22) |

Tinypool advisory pages currently label the issues High, while the captured npm audit labels them Critical. This report preserves npm counting and records the source severity separately in JSON. Minimatch affected ranges also differ: npm flags all <11.0.1, while the maintainer describes 10.2.6 with optimizationLevel>=2. No finding is silently suppressed.

## All 24 affected package records

| Package | Locked/installed node versions | npm severity | Production audit | All inherited/direct GHSAs | Disposition |
|---|---|---|---|---|---|
| @eslint-community/eslint-utils | 4.10.1 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @eslint/eslintrc | 2.1.4 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @humanwhocodes/config-array | 0.13.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @typescript-eslint/eslint-plugin | 8.71.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @typescript-eslint/parser | 8.71.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @typescript-eslint/type-utils | 8.71.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @typescript-eslint/typescript-estree | 8.71.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @typescript-eslint/utils | 8.71.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| @vitest/mocker | 3.2.7 | moderate | NO — dev only | GHSA-82fw-gwwq-j7x9, GHSA-qxg6-89mg-rqv8 | FIX_WITH_COHERENT_VITEST_4_FAMILY |
| eslint | 8.57.1 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| eslint-config-next | 15.5.27 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| eslint-plugin-import | 2.32.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| eslint-plugin-jsx-a11y | 6.10.2 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| eslint-plugin-react | 7.37.5 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| file-entry-cache | 6.0.1 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| flat-cache | 3.2.0 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| glob | 7.2.3 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| minimatch | 10.2.6, 3.1.5 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| next | 15.5.27 | critical | YES | GHSA-2464-fhr4-w9xw, GHSA-6g55-p6wh-862q, GHSA-fxqj-rqcc-2cmp, GHSA-p3m5-59mp-jv5r, GHSA-qx2v-qp2m-jg93, GHSA-r28c-9q8g-f849 | FIX_COMPATIBLE_PATCH |
| postcss | 8.4.31 | high | YES | GHSA-6g55-p6wh-862q, GHSA-fxqj-rqcc-2cmp, GHSA-qx2v-qp2m-jg93, GHSA-r28c-9q8g-f849 | FIX_SCOPED_COMPATIBLE_POSTCSS_8_PATCH |
| rimraf | 3.0.2 | moderate | NO — dev only | GHSA-g3hv-75wg-m249 | RESIDUAL_DEVELOPMENT_ONLY_NO_SAFE_GLOBAL_OVERRIDE |
| tinypool | 1.1.1 | critical | NO — dev only | GHSA-5gmw-xhrv-c9v3, GHSA-85c8-ppgw-ccpr | REMOVE_VIA_SUPPORTED_VITEST_4_POOL_REWORK |
| vite | 7.3.6 | high | NO — dev only | GHSA-587h-7wmj-h5pm, GHSA-698v-4r3x-3vcv, GHSA-9jrq-w75r-8gcw, GHSA-r825-w8mm-5pxw, GHSA-rq7h-c2jc-7f22 | FIX_COMPATIBLE_VITE_7_PATCH |
| vitest | 3.2.7 | critical | NO — dev only | GHSA-5gmw-xhrv-c9v3, GHSA-82fw-gwwq-j7x9, GHSA-85c8-ppgw-ccpr, GHSA-qxg6-89mg-rqv8 | FIX_DOCUMENTED_DEV_TOOL_MAJOR |

## Shared minimatch-family exposure and residual disposition

**Applies to all 18 affected package records (minimatch plus 17 meta-vulnerabilities), not 18 independent advisories.**

Development lint/config/cache/glob chain. Repository-controlled file patterns; no production HTTP input path found. Maintainer describes minimatch 10.2.6 preprocessing with optimizationLevel>=2. Root v3.1.5 has no optimizationLevel implementation, and locked TypeScript-ESLint calls nested v10.2.6 with {dot:true}, leaving default level 1. npm flags all <11.0.1; retain that audit finding rather than asserting it absent.

Record explicit development-only residual for GHSA-g3hv-75wg-m249. Do not install npm suggested ESLint 4.0.0/eslint-config-next 0.2.4 downgrades. Do not globally override minimatch 11: minimatch 3 exports a callable CommonJS function while 11 exports an object, breaking legacy consumers; TypeScript-ESLint nested ^10.2.2 also does not admit 11. Follow up with a compatible ESLint/TypeScript-ESLint/plugin/glob family migration or maintainer backport; rerun the full lint/typecheck/test/build envelope and audit.

## Complete dependency paths, usage and disposition

Paths below enumerate root-to-node normal and optional installation dependencies. The JSON additionally retains recursive npm-explain paths, raw npm fix suggestions and via-package edges (including peer/cyclic advisory propagation). Neither graph is treated as proof of a reachable production exploit.

### @eslint-community/eslint-utils

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@eslint-community/eslint-utils` @ `4.10.1`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → @eslint-community/eslint-utils@4.10.1`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/type-utils@8.71.0 → @typescript-eslint/utils@8.71.0 → @eslint-community/eslint-utils@4.10.1`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/utils@8.71.0 → @eslint-community/eslint-utils@4.10.1`

### @eslint/eslintrc

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@eslint/eslintrc` @ `2.1.4`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → @eslint/eslintrc@2.1.4`

### @humanwhocodes/config-array

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@humanwhocodes/config-array` @ `0.13.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → @humanwhocodes/config-array@0.13.0`

### @typescript-eslint/eslint-plugin

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@typescript-eslint/eslint-plugin` @ `8.71.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0`

### @typescript-eslint/parser

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@typescript-eslint/parser` @ `8.71.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/parser@8.71.0`

### @typescript-eslint/type-utils

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@typescript-eslint/type-utils` @ `8.71.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/type-utils@8.71.0`

### @typescript-eslint/typescript-estree

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@typescript-eslint/typescript-estree` @ `8.71.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/type-utils@8.71.0 → @typescript-eslint/typescript-estree@8.71.0`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/type-utils@8.71.0 → @typescript-eslint/utils@8.71.0 → @typescript-eslint/typescript-estree@8.71.0`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/utils@8.71.0 → @typescript-eslint/typescript-estree@8.71.0`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/parser@8.71.0 → @typescript-eslint/typescript-estree@8.71.0`

### @typescript-eslint/utils

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@typescript-eslint/utils` @ `8.71.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/type-utils@8.71.0 → @typescript-eslint/utils@8.71.0`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/utils@8.71.0`

### @vitest/mocker

**Exposure:** DEVELOPMENT_AND_CI_ONLY

**Usage/reachability:** Node test mode does not register the affected public development-server handlers; no browser/UI dependency is installed. Package is nevertheless affected under audit ranges.

**Remediation/disposition:** Upgrade coherently through vitest 4.1.12. Do not override exact mocker 3.2.7 with a different-major sibling underneath Vitest 3.

**Nodes and complete root dependency paths:**

- `node_modules/@vitest/mocker` @ `3.2.7`; version evidence: npm explain JSON.
  - `@prufai/web → vitest@3.2.7 → @vitest/mocker@3.2.7`

### eslint

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/eslint` @ `8.57.1`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1`

### eslint-config-next

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded. Align this package to Next.js 15.5.29, while retaining the inherited minimatch residual.

**Nodes and complete root dependency paths:**

- `node_modules/eslint-config-next` @ `15.5.27`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27`

### eslint-plugin-import

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/eslint-plugin-import` @ `2.32.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → eslint-plugin-import@2.32.0`

### eslint-plugin-jsx-a11y

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/eslint-plugin-jsx-a11y` @ `6.10.2`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → eslint-plugin-jsx-a11y@6.10.2`

### eslint-plugin-react

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/eslint-plugin-react` @ `7.37.5`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → eslint-plugin-react@7.37.5`

### file-entry-cache

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/file-entry-cache` @ `6.0.1`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → file-entry-cache@6.0.1`

### flat-cache

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/flat-cache` @ `3.2.0`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → file-entry-cache@6.0.1 → flat-cache@3.2.0`

### glob

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/glob` @ `7.2.3`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → file-entry-cache@6.0.1 → flat-cache@3.2.0 → rimraf@3.0.2 → glob@7.2.3`

### minimatch

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/@typescript-eslint/typescript-estree/node_modules/minimatch` @ `10.2.6`; version evidence: npm explain JSON.
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/type-utils@8.71.0 → @typescript-eslint/typescript-estree@8.71.0 → minimatch@10.2.6`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/type-utils@8.71.0 → @typescript-eslint/utils@8.71.0 → @typescript-eslint/typescript-estree@8.71.0 → minimatch@10.2.6`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/eslint-plugin@8.71.0 → @typescript-eslint/utils@8.71.0 → @typescript-eslint/typescript-estree@8.71.0 → minimatch@10.2.6`
  - `@prufai/web → eslint-config-next@15.5.27 → @typescript-eslint/parser@8.71.0 → @typescript-eslint/typescript-estree@8.71.0 → minimatch@10.2.6`
- `node_modules/minimatch` @ `3.1.5`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → @eslint/eslintrc@2.1.4 → minimatch@3.1.5`
  - `@prufai/web → eslint@8.57.1 → @humanwhocodes/config-array@0.13.0 → minimatch@3.1.5`
  - `@prufai/web → eslint@8.57.1 → file-entry-cache@6.0.1 → flat-cache@3.2.0 → rimraf@3.0.2 → glob@7.2.3 → minimatch@3.1.5`
  - `@prufai/web → eslint@8.57.1 → minimatch@3.1.5`
  - `@prufai/web → eslint-config-next@15.5.27 → eslint-plugin-import@2.32.0 → minimatch@3.1.5`
  - `@prufai/web → eslint-config-next@15.5.27 → eslint-plugin-jsx-a11y@6.10.2 → minimatch@3.1.5`
  - `@prufai/web → eslint-config-next@15.5.27 → eslint-plugin-react@7.37.5 → minimatch@3.1.5`

### next

**Exposure:** PRODUCTION_DEPENDENCY; developer and deployed Next.js server

**Usage/reachability:** App Router, same-origin API rewrites, authentication/session UI and Next build/start. Root source review found no next/image imports or Server Actions; sharp 0.35.5 already exceeds the image-RCE workaround threshold. This reduces demonstrated reachability, not the need to patch a production framework advisory.

**Remediation/disposition:** Patch next 15.5.27 -> 15.5.29 with matching eslint-config-next 15.5.29. Preserve Next 15/React 19 contracts and prove all authenticated browser journeys.

**Nodes and complete root dependency paths:**

- `node_modules/next` @ `15.5.27`; version evidence: npm explain JSON.
  - `@prufai/web → next@15.5.27`

### postcss

**Exposure:** PRODUCTION_DEPENDENCY via next; build-tool CSS processing

**Usage/reachability:** Next.js pins vulnerable postcss 8.4.31. Root source review found no application path processing attacker-supplied CSS or source maps. The separate Vite-owned postcss 8.5.28 is not an affected audit node. Do not infer internet exploitability solely from production installation.

**Remediation/disposition:** Use a narrowly scoped Next -> postcss 8.5.23 override to cover all four findings, regenerated by npm; no global dependency-family override. Rerun build/audits/browser suites.

**Nodes and complete root dependency paths:**

- `node_modules/postcss` @ `8.4.31`; version evidence: npm explain JSON.
  - `@prufai/web → next@15.5.27 → postcss@8.4.31`

### rimraf

**Exposure:** DEVELOPMENT_AND_CI_ONLY.

**Usage/disposition:** Shared minimatch-family assessment above; inherited GHSA-g3hv-75wg-m249 remains explicitly recorded.

**Nodes and complete root dependency paths:**

- `node_modules/rimraf` @ `3.0.2`; version evidence: npm explain JSON.
  - `@prufai/web → eslint@8.57.1 → file-entry-cache@6.0.1 → flat-cache@3.2.0 → rimraf@3.0.2`

### tinypool

**Exposure:** DEVELOPMENT_AND_CI_ONLY

**Usage/reachability:** Vitest 3 test worker pool; gadget requires an independent prototype-pollution primitive. No production request handler directly calls Tinypool, but CI/developer privileges make the gadget material.

**Remediation/disposition:** Remove through supported Vitest 4.1.12 family upgrade. First common patched Tinypool is 2.1.2, but a 2.x override would exceed Vitest 3 declared ^1.1.1. Do not claim such override supported.

**Nodes and complete root dependency paths:**

- `node_modules/tinypool` @ `1.1.1`; version evidence: npm explain JSON.
  - `@prufai/web → vitest@3.2.7 → tinypool@1.1.1`

### vite

**Exposure:** DEVELOPMENT_AND_CI_ONLY

**Usage/reachability:** Transitive test transformation dependency. Application dev/start are Next.js, not Vite. No exposed Vite dev server, custom HTML middleware or editor endpoint is configured. Two launch-editor vectors require Windows; CI is Linux and observed local host is macOS.

**Remediation/disposition:** Patch Vite 7.3.6 -> 7.3.7 within supported Vitest 3/4 ranges; same Node >=20.19 or >=22.12 engines. No Vite 8 upgrade is needed for these five findings.

**Nodes and complete root dependency paths:**

- `node_modules/vite` @ `7.3.6`; version evidence: npm explain JSON.
  - `@prufai/web → vitest@3.2.7 → vite@7.3.6`
  - `@prufai/web → vitest@3.2.7 → vite-node@3.2.4 → vite@7.3.6`

### vitest

**Exposure:** DEVELOPMENT_AND_CI_ONLY

**Usage/reachability:** Only vitest run with environment=node. No configured Vitest browser/UI/API listener or public mocker/interceptor plugin. Tinypool still executes test workers, so prerequisite prototype pollution remains a developer/CI process risk.

**Remediation/disposition:** Deliberate minimal maintained major: vitest 4.1.12, with fixed exact @vitest/mocker 4.1.12 and no Tinypool. Pin compatible Vite 7.3.7 to avoid an unrelated Vite 8 resolution. Document mock/pool/default-exclusion changes and require the entire 172-test/typecheck/lint/build envelope.

**Nodes and complete root dependency paths:**

- `node_modules/vitest` @ `3.2.7`; version evidence: npm explain JSON.
  - `@prufai/web → vitest@3.2.7`

## Evidence and remaining verification

The adjacent portable JSON contains all advisory ranges, first patched versions, CVEs, npm/GitHub severity differences, exact dependency nodes/versions, full dependency paths, recursive npm-explain paths and SHA256 hashes of source reports/lock. Absolute workstation paths are excluded from the JSON data.

Regenerate the manifest/lock through npm, rerun both audit commands and compare advisory IDs. Complete the existing frontend/backend/AI/Alembic/PostgreSQL/MinIO/Redis/browser regressions and exact-final-SHA remote CI before remediation closure. This report does not establish container/OS/Go vulnerability absence or pilot security readiness.
