# PrüfAI Python dependency advisory inventory — before remediation

Audit date: **2026-11-04**. Repository: `moaltamimi-unbiasedtalent/PrufAI`.
Baseline main SHA: `e7752acbecb47a1182f7c2d39a4f2f2822ad311f` (merged PR #31).

This is the executed **original-main before** advisory inventory. Its counts remain historical facts. The initial provisional plan to retain Python3.9 andpytest8 was **superseded** by a subsequent actual rejected-candidate Python3.9 audit. The final candidate explicitly requires Python>=3.11, patchespytest/build tooling, removes the unused frameworks and sets patched HTTP dependency minimums. Final security-branch audit and exact-SHA remote acceptance remain separate gates.

## Method and scope

The audit used **pip-audit 2.10.1 / Python 3.11.15** on local macOS, with a dedicated auditor virtual environment and separate application target environments. `pip-audit --path` inspected resolved installed target metadata; auditor dependencies were excluded from the application graph. JSON retained descriptions, aliases and fixed versions. First-party editable distributions were explicitly excluded from vulnerability lookup; their installed third-party dependencies were audited.

The combined target followed the repository's API+AI development install and CI pip-upgrade procedure. It is a local resolution of that procedure, **not a claim that local macOS artifacts are identical to Ubuntu CI artifacts**. Separate API and AI production targets were also resolved. The repository has no Python lockfile or constraints export; the accompanying `pip inspect` and `pip freeze --all` snapshots define the versions actually audited.

The advisory feed returned duplicate PYSEC/alias entries. Raw results are preserved. The unique counts below normalize identical GHSA aliases; they do not count the same advisory twice.

| Before target | Installed distributions | Raw vulnerable records | Affected packages | Unique GHSA findings | Actual audit exit |
|---|---:|---:|---:|---:|---:|
| Combined API + AI development, pip upgraded to 26.3 | 85 | 25 | 6 | 14 | 1 |
| API production plus initial venv bootstrap tools | 45 | 3 | 2 | 2 | 1 |
| AI production plus initial venv bootstrap tools | 50 | 25 | 6 | 14 | 1 |

The combined development target has **1 critical, 5 high, 6 medium and 2 low** unique findings. Its runtime framework portion is **12 unique advisories: 1 critical, 5 high, 5 medium and 1 low**; pytest adds one development-only medium and bootstrap setuptools adds one low. The API production application graph has no known runtime finding in the executed audit; its two unique findings are bootstrap pip and setuptools. The AI production target's 14 unique findings consist of the same 12 runtime framework advisories plus those two bootstrap-tool findings.

Across all three before targets there are **15 distinct GHSA findings**: **1 critical, 5 high, 7 medium, 2 low**. These Python counts are separate from npm package/advisory counts and must not be added to npm installation warnings as if the counting units were identical.

## Complete unique advisory inventory

Dependency paths are declared or resolved package relationships. A production-installed dependency is not, by itself, proof that the vulnerable operation is reachable. “Remove unused family” below means remove the two unused direct declarations (`langchain-core` and `langgraph`) and verify that their vulnerable transitive graph disappears; it does not mean override incompatible transitive pins or force a major update.

| Package / installed version | Advisory / CVE | Severity | Upstream fixed release(s) | Dependency path and exposure | Evidence-backed disposition |
|---|---|---|---|---|---|
| `langchain-core==0.3.79` | [GHSA-2g6r-c272-w58r](https://github.com/langchain-ai/langchain/security/advisories/GHSA-2g6r-c272-w58r); CVE-2026-26013 | **LOW** | `1.2.11` | prufai-ai → langchain-core; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langchain-core==0.3.79` | [GHSA-6qv9-48xg-fc7f](https://github.com/langchain-ai/langchain/security/advisories/GHSA-6qv9-48xg-fc7f); CVE-2025-65106 | **HIGH** | `0.3.80 / 1.0.7` | prufai-ai → langchain-core; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langchain-core==0.3.79` | [GHSA-926x-3r5x-gfhw](https://github.com/langchain-ai/langchain/security/advisories/GHSA-926x-3r5x-gfhw); CVE-2026-40087 | **MEDIUM** | `0.3.84 / 1.2.28` | prufai-ai → langchain-core; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langchain-core==0.3.79` | [GHSA-c67j-w6g6-q2cm](https://github.com/langchain-ai/langchain/security/advisories/GHSA-c67j-w6g6-q2cm); CVE-2025-68664 | **CRITICAL** | `0.3.81 / 1.2.5` | prufai-ai → langchain-core; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langchain-core==0.3.79` | [GHSA-pjwx-r37v-7724](https://github.com/langchain-ai/langchain/security/advisories/GHSA-pjwx-r37v-7724); CVE-2026-44843 | **HIGH** | `0.3.85 / 1.3.3` | prufai-ai → langchain-core; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langchain-core==0.3.79` | [GHSA-qh6h-p6c9-ff54](https://github.com/langchain-ai/langchain/security/advisories/GHSA-qh6h-p6c9-ff54); CVE-2026-34070 | **HIGH** | `1.2.22` | prufai-ai → langchain-core; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langgraph==0.6.7` | [GHSA-g48c-2wqr-h844](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-g48c-2wqr-h844); CVE-2026-28277 | **MEDIUM** | `1.0.10` | prufai-ai → langgraph; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langgraph-checkpoint==2.1.2` | [GHSA-fjqc-hq36-qh5p](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-fjqc-hq36-qh5p); CVE-2026-48775 | **MEDIUM** | `4.1.1` | prufai-ai → langgraph → langgraph-checkpoint; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langgraph-checkpoint==2.1.2` | [GHSA-mhr3-j7m5-c7c9](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-mhr3-j7m5-c7c9); CVE-2026-27794 | **MEDIUM** | `4.0.0` | prufai-ai → langgraph → langgraph-checkpoint; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langgraph-checkpoint==2.1.2` | [GHSA-wwqv-p2pp-99h5](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-wwqv-p2pp-99h5); CVE-2025-64439 | **HIGH** | `3.0.0` | prufai-ai → langgraph → langgraph-checkpoint; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langgraph-sdk==0.2.15` | [GHSA-fvww-7h3r-vfhp](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-fvww-7h3r-vfhp); CVE-2026-104873 | **HIGH** | `0.4.4` | prufai-ai → langgraph → langgraph-sdk; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `langgraph-sdk==0.2.15` | [GHSA-w39p-vh2g-g8g5](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-w39p-vh2g-g8g5); CVE-2026-48776 | **MEDIUM** | `0.3.15` | prufai-ai → langgraph → langgraph-sdk; installed AI runtime dependency, affected upstream feature not used by current application. | Remove unused family; no upstream framework call needs migration. If reintroduced, separately review a patched compatible family. |
| `pip==26.1.2` | [GHSA-qwm4-qh6w-59xr](https://github.com/pypa/pip/commit/10dfb6b9005484578b386f64b9f36982e3dc6679); CVE-2026-13346 | **MEDIUM** | `26.2.0` | venv bootstrap → pip; package-install tooling, not application runtime. Initial prod venvs had 26.1.2; CI-aligned target already has patched 26.3. | Final decision: bounded CI pip>=26.2,<27 in both Python jobs; tested26.3. Exploit requires a malicious package index; audits used explicit public PyPI. |
| `pytest==8.4.2` | [GHSA-6w46-j5rx-g56g](https://github.com/pytest-dev/pytest/releases/tag/9.0.3); CVE-2025-71176 | **MEDIUM** | `9.0.3` | prufai-api[dev] → pytest; prufai-ai[dev] → pytest; development only, local UNIX temporary-directory risk. | Final decision: Python>=3.11 andpytest>=9.0.3,<9.1 in both services. Provisionalpytest8 retention is superseded; private CI temporary root remains defense in depth. Full new regressions/audit required. |
| `setuptools==83.0.0` | [GHSA-xmj3-9gf7-h52w](https://github.com/pypa/setuptools/security/advisories/GHSA-xmj3-9gf7-h52w); No CVE listed in captured record | **LOW** | `84.1.0` | venv bootstrap → setuptools; low Windows-only legacy wheel-install issue. Local macOS/CI Linux are outside that Windows exploit platform. | Final decision: explicit Python>=3.11; setuptools>=84.1,<85 in both build systems and CI jobs. Tested84.2.1. Bootstrap/build exposure remains distinguished from runtime. |

## Reachability and removal compatibility

The source/entry-point search found no imports of upstream `langchain`, `langchain_core`, `langgraph`, `langgraph-sdk` or `langsmith` in the API/AI application or worker execution paths; no dynamic loading or project entry point invokes them. The `LangGraphRuntime` and `LangChainCompatibleModel` classes in `services/ai/app/orchestration.py` are local bounded Python/Pydantic implementations. The actual Writing, Speaking and transcription workers are API modules and use their own existing provider boundaries. The declared frameworks are therefore installed attack surface without a demonstrated runtime use. This is evidence for removal, **not a claim that advisory severity is false or that future framework use would be safe**.

The isolated removal-candidate environment had no LangChain/LangGraph/LangSmith packages and no `httpx2`. With all existing declared AI development dependencies, **all 7 AI tests passed**, with one Starlette legacy-httpx deprecation warning. Existing declared `httpx==0.28.1` is supported by Starlette's explicit fallback; no additional HTTP client dependency is required for this removal. The initial minimal exploratory environment omitted declared development `httpx` and its import error was corrected; that exploratory failure is not an application defect.

The final candidate updates only the orchestration module docstring to describe its actual local runtime; the implementation remains unchanged. Historical Phase4 reports remain historical records.

## Final support-floor, pytest and tooling disposition

The initial Python3.11-only audit exposed pytest8's medium advisory, fixed in9.0.3, while the original manifests still declared Python>=3.9. Preserving that declaration and retainingpytest8 was only a provisional plan. Actual Python3.9 production audits then disproved a clean legacy-runtime disposition: even with the unused AI frameworks already removed, API runtime resolution had **15 unique advisories (1 critical,8 high,5 medium,1 low)** and AI runtime had **8 (1 critical,2 high,4 medium,1 low)**, separately from six bootstrap findings in each target.

The rejected candidate is not a complete original-main3.9 audit: it already lacked the two unused LangChain/LangGraph declarations. Its new HTTP-stack findings are therefore additional evidence about the still-declared interpreter range, not replacements for the original-main Python3.11 counts above. See [the rejected legacy candidate report](python39-rejected-candidate.md), [its normalized inventory](python39-rejected-candidate-inventory.json) and [patched-release metadata](python39-fixed-version-compatibility.json).

Concrete constraints prevent a normal patched3.9 resolution: legacyFastAPI selectsStarlette<1 while the requiredStarlette fixes requirePython>=3.10; legacybotocore constrainsurllib3<1.27 onPython<3.10 while the captured fixedurllib3 release is2.8.0 and requiresPython>=3.10. FixedAnyIO4.14.2 andpython-dotenv1.2.2 likewise require>=3.10. Newer compatible patch releases are not available for the rejected interpreter. The [official lifecycle table](https://devguide.python.org/versions/) listsPython3.9 EOL2025-10-31,Python3.10 EOL2026-10-01 andPython3.11 security support through2027-10 as of2026-11-04.

The final candidate deliberately sets **Python>=3.11 in both projects**, aligning the existing CI and actually tested modern baseline. This compatibility change requires old3.9/3.10 application environments to be recreated under the supported interpreter; it does not require a database reset or migration.

Both development extras now require **pytest>=9.0.3,<9.1**, fixing [GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g). There is **no present-tense decision to retain that medium finding**. A new complete regression/audit run and exact-SHA CI are required for thismajor test-tool change; the earlierpytest8 candidate captures below remain historical evidence only.

Both build-system requirements now use **setuptools>=84.1,<85**. Both Python CI jobs bootstrap bounded patched **pip>=26.2,<27** and **setuptools>=84.1,<85**, with tested versions26.3/84.2.1. The obsolete interpreter no longer constrains those fixes. Bootstrap/build findings remain distinguished from application runtime dependencies.

The final candidate also makes the known patched runtime floors explicit: API **anyio>=4.14.2,<5**, **starlette>=1.3.1,<2**, **python-dotenv>=1.2.2,<2**, **urllib3>=2.8,<3**; AI uses the same first three becauseurllib3 is not in its remaining runtime graph. These floors prevent a newly permitted but known-vulnerable older HTTP resolution; they do not replace an audit of actual installed versions.

CI retains its unpredictable `mktemp` pytest root withmode0700 and`PYTEST_DEBUG_TEMPROOT` as **defense in depth after adopting patchedpytest**. It is not an advisory waiver, suppression or substitute for the upstreamfix. Normal test collection, assertions and nonzero exitstatus remain intact.

## Historical initial candidate experiments, not final residuals

These initial external probes removed only the two unused framework declarations and patched bootstrap tools. They preceded the later legacy-interpreter audit and explicit Python/pytest support decision:

| Candidate experiment | Distributions | Actual audit result |
|---|---:|---|
| Minimal AI production dependencies | 22 | Exit 0; zero known findings |
| API production dependencies, unchanged app graph | 45 | Exit 0; zero known findings |
| Initial AI development dependencies withpytest8 | 37 | Historical exit1;pytest8 only — two raw duplicate records, one medium GHSA; subsequently addressed by the finalpytest9 plan |

The final Python>=3.11/pytest>=9.0.3 combined regression candidate is recorded separately from these earlier probes. These historical experiments do not establish its final audit status or committed-branch/remote CI acceptance. The final branch must repeat audits and pass all required application/PostgreSQL/MinIO/Redis/browser regressions before closure.

## Machine-readable evidence

The portable evidence included beside this report is:

- `python-inventory-before.json`: normalized package/version, advisory aliases, affected/fixed ranges, dependency paths, exposure, disposition, primary links and captured candidate-proof metadata.
- `python-resolved-before.json`: complete CI-aligned installed distribution versions, declared dependency edges and Python platform metadata, with workstation paths omitted.
- `prufai-security-py-ci-before-audit.json`, `prufai-security-py-api-prod-before-audit.json` and `prufai-security-py-ai-prod-before-audit.json`: captured auditor results with freeform advisory descriptions omitted; each wrapper preserves the original raw capture SHA256. Duplicate feed records and all IDs/aliases remain intact.
- `README.md` and the after-audit artifacts: final branch source matching, local regression and before/after comparison. Exact-head remote acceptance is recorded in the PR checks and closure description.

Full local audit/inspect/freeze captures, primary-advisory API responses, PyPI metadata and exploratory test logs were retained outside the repository during execution. They are not presented as adjacent downloadable files. Primary maintainer links and the relevant metadata are embedded in the normalized inventory, and the final reviewer evidence is in this portable bundle.

Use the captured JSON when comparing exact before/after advisory IDs; installation warnings and package-level summary counts are not substitutes for this advisory-level evidence. No genuine AI inference, production deployment safety or pilot-security readiness is established by these dependency audits.
