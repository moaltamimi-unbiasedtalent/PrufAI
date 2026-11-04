# PrüfAI npm advisory inventory — actual security branch after remediation

Repository: `moaltamimi-unbiasedtalent/PrufAI`

Branch: `security/dependency-hardening`

Baseline main: `e7752acbecb47a1182f7c2d39a4f2f2822ad311f`

Capture state: actual security-branch installation, with exact source SHA256s in `npm-inventory-after.json`. All recorded package/lock/workflow/Python declaration hashes were matched to committed branch code at `43a8be5688a798bcbec9001ee5ca70891050ca0d`; documentation can advance separately. Final exact-head remote CI must be recorded separately.

## Executed audit results and before/after comparison

| Scope | Before | After |
|---|---|---|
| All npm dependencies | 24 affected packages: 3 critical, 2 high, 19 moderate, 0 low | 18 affected development packages: **0 critical, 0 high, 18 moderate, 0 low** |
| Production-only (`npm audit --omit=dev --json`) | 2 affected packages: 1 critical Next.js, 1 high PostCSS | **0 affected packages / 0 known findings** |
| Deduplicated source advisories | 16 GHSAs | 1 remaining GHSA; **15 resolved** |
| Remaining production critical/high packages | 1 critical / 1 high | **0 / 0** |

Actual raw results: [all dependencies](prufai-npm-audit-after-all.json), [production only](prufai-npm-audit-after-production.json). The all-dependency audit remains nonzero because of the 18 moderate development records; do not label it a zero-vulnerability or fully clean audit. The production-only audit succeeds. Counts are npm package records, not distinct CVEs.

## Exact installed/locked changes

| Package | Before | After | Change boundary |
|---|---|---|---|
| next | 15.5.27 | 15.5.29 | Compatible Next 15 patch; React/React DOM unchanged. |
| eslint-config-next | 15.5.27 | 15.5.29 | Keep framework lint configuration aligned with Next. |
| postcss | 8.4.31 | 8.5.23 | Only Next-owned PostCSS overridden, within PostCSS 8; no global CSS-family override. |
| vitest | 3.2.7 | 4.1.12 | Documented development-tool major; coherent companions and removal of Tinypool. |
| vite | 7.3.6 | 7.3.7 | Existing Vite 7 patch pinned, avoiding unnecessary Vite 8 resolution. |

All versions above match actual `npm explain` and the npm-generated lock. `@vitest/mocker` is now 4.1.12. Tinypool is absent from the lock. No forced update or unrelated direct application dependency upgrade is included.

## Resolved advisory IDs

These IDs appeared in the before audit and are absent from the executed actual-branch after audit. Runtime reachability assessment is preserved in `npm-before.md`; fixing a package finding is not a claim that exploitation previously occurred.

| Advisory | CVE | Before package(s) | Primary source |
|---|---|---|---|
| GHSA-2464-fhr4-w9xw | No CVE assigned in captured source | next | [maintainer/advisory](https://github.com/vercel/next.js/security/advisories/GHSA-2464-fhr4-w9xw) |
| GHSA-587h-7wmj-h5pm | CVE-2026-112942 | vite | [maintainer/advisory](https://github.com/vitejs/launch-editor/security/advisories/GHSA-587h-7wmj-h5pm) |
| GHSA-5gmw-xhrv-c9v3 | CVE-2026-104848 | tinypool | [maintainer/advisory](https://github.com/tinylibs/tinypool/security/advisories/GHSA-5gmw-xhrv-c9v3) |
| GHSA-698v-4r3x-3vcv | CVE-2026-112943 | vite | [maintainer/advisory](https://github.com/vitejs/launch-editor/security/advisories/GHSA-698v-4r3x-3vcv) |
| GHSA-6g55-p6wh-862q | CVE-2026-45623 | postcss | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-6g55-p6wh-862q) |
| GHSA-82fw-gwwq-j7x9 | CVE-2026-84373 | @vitest/mocker, vitest | [maintainer/advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9) |
| GHSA-85c8-ppgw-ccpr | CVE-2026-104849 | tinypool | [maintainer/advisory](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr) |
| GHSA-9jrq-w75r-8gcw | CVE-2026-112945 | vite | [maintainer/advisory](https://github.com/vitejs/vite/security/advisories/GHSA-9jrq-w75r-8gcw) |
| GHSA-fxqj-rqcc-2cmp | CVE-2026-69153 | postcss | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-fxqj-rqcc-2cmp) |
| GHSA-p3m5-59mp-jv5r | CVE-2026-94482 | next | [maintainer/advisory](https://github.com/vercel/next.js/security/advisories/GHSA-p3m5-59mp-jv5r) |
| GHSA-qx2v-qp2m-jg93 | CVE-2026-41305 | postcss | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-qx2v-qp2m-jg93) |
| GHSA-qxg6-89mg-rqv8 | CVE-2026-112949 | @vitest/mocker | [maintainer/advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8) |
| GHSA-r28c-9q8g-f849 | CVE-2026-73646 | postcss | [maintainer/advisory](https://github.com/postcss/postcss/security/advisories/GHSA-r28c-9q8g-f849) |
| GHSA-r825-w8mm-5pxw | CVE-2026-112944 | vite | [maintainer/advisory](https://github.com/vitejs/launch-editor/security/advisories/GHSA-r825-w8mm-5pxw) |
| GHSA-rq7h-c2jc-7f22 | CVE-2026-112946 | vite | [maintainer/advisory](https://github.com/vitejs/vite/security/advisories/GHSA-rq7h-c2jc-7f22) |

## Remaining advisory and all affected package records

**GHSA-g3hv-75wg-m249 / CVE-2026-118764 remains moderate.** It accounts for 18 affected package records: minimatch plus **17 inherited/meta-vulnerabilities**, not 18 independent advisories. All are development/CI lint/config/glob dependencies; the production audit is empty.

npm reports all minimatch versions below 11.0.1 as affected. The [maintainer advisory](https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249) describes 10.2.6 preprocessing with optimizationLevel>=2. Root v3.1.5 has no optimizationLevel implementation; the locked TypeScript-ESLint use of nested v10.2.6 supplies {dot:true} and keeps default optimization level 1. No production request-controlled pattern path was found. These observations reduce demonstrated reachability; they do not suppress or erase the audit finding.

A global minimatch 11 override is unsafe: legacy v3 consumers expect the callable CommonJS export, while v11 returns an export object. The nested TypeScript-ESLint ^10.2.2 range also does not admit 11. The npm suggestions to downgrade ESLint to 4.0.0 or eslint-config-next to 0.2.4 are not acceptable fixes. Follow up with a supported lint-family/plugin migration or compatible upstream backport, then repeat audit/lint/typecheck/tests/build/browser acceptance.

| Affected package | Actual version(s) | npm severity | Production exposure |
|---|---|---|---|
| @eslint-community/eslint-utils | 4.10.1 | moderate | NO — development/CI only |
| @eslint/eslintrc | 2.1.4 | moderate | NO — development/CI only |
| @humanwhocodes/config-array | 0.13.0 | moderate | NO — development/CI only |
| @typescript-eslint/eslint-plugin | 8.71.0 | moderate | NO — development/CI only |
| @typescript-eslint/parser | 8.71.0 | moderate | NO — development/CI only |
| @typescript-eslint/type-utils | 8.71.0 | moderate | NO — development/CI only |
| @typescript-eslint/typescript-estree | 8.71.0 | moderate | NO — development/CI only |
| @typescript-eslint/utils | 8.71.0 | moderate | NO — development/CI only |
| eslint | 8.57.1 | moderate | NO — development/CI only |
| eslint-config-next | 15.5.29 | moderate | NO — development/CI only |
| eslint-plugin-import | 2.32.0 | moderate | NO — development/CI only |
| eslint-plugin-jsx-a11y | 6.10.2 | moderate | NO — development/CI only |
| eslint-plugin-react | 7.37.5 | moderate | NO — development/CI only |
| file-entry-cache | 6.0.1 | moderate | NO — development/CI only |
| flat-cache | 3.2.0 | moderate | NO — development/CI only |
| glob | 7.2.3 | moderate | NO — development/CI only |
| minimatch | 10.2.6, 3.1.5 | moderate | NO — development/CI only |
| rimraf | 3.0.2 | moderate | NO — development/CI only |

The portable JSON preserves every affected node and full normal/optional plus recursive peer root path from actual branch captures. It also retains the before advisory metadata, affected/fixed ranges and primary references for ID-level review.

## Installed-tree reproducibility observations

The saved npm-tree capture reports two extraneous optional platform packages in the local installation: `@emnapi/runtime@1.11.3` and `@img/sharp-wasm32@0.35.5`. Their presence is recorded without inferring an installer cause or claiming a completely clean npm tree. No invalid override/version constraint was reported.

An independent disposable manifest listed those exact versions as optional dependencies, generated its lock with `--package-lock-only --ignore-scripts --no-audit --no-fund`, and ran `npm audit --json`. The lock contains both exact versions and one transitive node; **zero known findings** were returned. Evidence: [manifest](extraneous-package-audit-manifest.json), [lock](extraneous-package-audit-lock.json), [audit](extraneous-package-npm-audit.json). This is separate from the application’s before/after package counts and does not inspect wasm/native binaries or OS packages. No application dependency or installed package was removed by this check.

Clean deployment installation/rebuild and final exact-head CI remain required; removing package declarations does not uninstall orphaned existing packages.

## Verification and privacy boundary

Only package reports and normalized public advisory/dependency metadata are copied here. Raw all/production audit files have verified SHA256 equality with actual branch captures. Exact source manifest/lock/workflow/Python declaration hashes identify the audited working-tree snapshot. Raw npm tree/explain captures remain local; relevant portable paths and tree observations are embedded in JSON.

Application regression and exact-final-SHA CI are recorded in the closure evidence. This npm audit does not establish container/OS/Go/Actions/Chromium-binary security, authentic model execution, language-assessment validity or PILOT SECURITY READY.
