# Python dependency after-audit — actual security branch

Audit date: **2026-11-04**. Branch: **security/dependency-hardening**. Audited checkout: `/private/tmp/prufai-dependency-audit`.

These audits installed the **actual branch working-tree manifests**, not only an external candidate. At capture HEAD was still the starting main commit `e7752acbecb47a1182f7c2d39a4f2f2822ad311f` with six remediation files uncommitted. That HEAD is **not** represented as the final audited remediation commit. The SHA256 content hashes below identify the audited source; all were rechecked unchanged after audits. Final commits and exact-SHA remote CI remain separate acceptance evidence.

## Source content identity

| File | SHA256 |
|---|---|
| `services/api/pyproject.toml` | `42366896bc1aac9607e3656d3619e40c6ee16ef35c389a508ca3fb087d4d2eae` |
| `services/ai/pyproject.toml` | `4d96c6a37169140b18985bed57de8c83b182ae8f91dbfafad873362c97cc0ece` |
| `.github/workflows/ci.yml` | `b9618e882f52566ebc6e7046bce5a245ec965c9ddc64962851581a14b630e657` |

## Executed result

Dedicated **pip-audit2.10.1 / Python3.11.15** inspected separate freshly installed Python3.11 targets via `--path`, using the public advisory service. The fresh targets used tested patched bootstrap **pip26.3 / setuptools84.2.1**. Their package-manager and inspector outputs were retained. No audit-tool dependencies were installed into application targets.

| Actual branch target | Inventoried distributions | Third-party distributions audited | First-party editable lookups excluded | Known findings | Audit exit | `pip check` |
|---|---:|---:|---:|---:|---:|---|
| ci | 63 | 61 | 2 | 0 | 0 | PASS |
| api-prod | 45 | 44 | 1 | 0 | 0 | PASS |
| ai-prod | 23 | 22 | 1 | 0 | 0 | PASS |

All three executed audits returned **zero known vulnerabilities**. The explicitly excluded vulnerability lookups are only the unpublished first-party editable distributions (`prufai-api`/`prufai-ai`); every installed third-party dependency in their resolved graphs was audited. These lookup exclusions are not test/integration skips, and do not hide third-party findings.

## Declared policy and actual versions

Both installed first-party project metadata records declare **Python>=3.11**. Both captured project manifests declare build requirements **setuptools>=84.1,<85**. API declares security floors **anyio>=4.14.2,<5**, **starlette>=1.3.1,<2**, **python-dotenv>=1.2.2,<2**, **urllib3>=2.8,<3**; AI declares the same first three. Both development extras declare **pytest>=9.0.3,<9.1**. The two unused upstream AI framework declarations and their transitive graph are absent. Existing local orchestration and API worker boundaries remain unchanged.

The combined target resolved:

| Package | Actual installed version |
|---|---|
| `pip` | `26.3` |
| `setuptools` | `84.2.1` |
| `pytest` | `9.0.3` |
| `anyio` | `4.16.0` |
| `starlette` | `1.10.0` |
| `python-dotenv` | `1.2.4` |
| `urllib3` | `2.8.0` |
| `fastapi` | `0.162.0` |
| `httpx` | `0.28.1` |

## Advisory comparison

| Original before scope | Unique GHSA before | Unique GHSA after | Previously observed IDs absent after |
|---|---:|---:|---:|
| ci | 14 | 0 | 14 |
| api-prod | 2 | 0 | 2 |
| ai-prod | 14 | 0 | 14 |

Across the original captured scopes, **all15 distinct observed GHSA IDs are absent** from the actual after graphs; no new advisory ID appeared. Runtime framework advisories were eliminated by removing unused declarations; pytest was patched rather than waived; bootstrap/build tools were patched under the explicit supported Python floor. Exact IDs, per-scope comparison, dependency metadata and package versions are in `python-inventory-after.json`.

The separately rejected Python3.9 SDK-removal candidate had21 distinct runtime/bootstrap GHSA IDs across its API/AI graphs; those are also absent in the actual supported-runtime after inventories. It was **not** a complete original-main3.9 audit. The final service metadata explicitly withdraws3.9/3.10 support; clean application environments must be created with the supported interpreter. No database reset or migration follows from this dependency rebuild.

## Evidence and limits

- `python-inventory-after.json`: normalized actual-branch provenance, content hashes, selected versions, complete concise distribution metadata, first-party policy and advisory comparison.
- `python-after-ci-audit.json`, `python-after-api-prod-audit.json`, `python-after-ai-prod-audit.json`: genuine raw after-audit JSON. They contain no vulnerability descriptions because no vulnerabilities were returned.
- Full raw `pip inspect`, `pip freeze --all`, pip-check logs, audit stdout/stderr and recorded exit status remain under local `prufai-security-after-py-<target>-*` capture paths; these are not presented as adjacent downloadable files.

This result is an actual resolved-dependency audit, not a claim about every future resolution, OS/container/browser binaries, interpreter CVEs, authentic AI quality, pilot-security readiness or production acceptance. Final branch commits, full application regressions and exact-SHA remote CI are still required for closure.
