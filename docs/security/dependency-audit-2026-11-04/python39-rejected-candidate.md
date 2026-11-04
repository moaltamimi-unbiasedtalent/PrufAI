# Supplementary Python 3.9 audit — declared-support blocker

Audit date: **2026-11-04**. Audited rejected candidate: the isolated security-remediation tree whose Python manifests had already removed the two unused LangChain Core/LangGraph declarations but still declaredPython>=3.9. This was not a complete original-main Python3.9 audit. No tracked repository source changed during this audit.

The prior clean Python3.11 runtime audit did **not** establish dependency security across the still-declared Python `>=3.9` range. This executed supplementary audit found substantial legacy runtime vulnerabilities. Retaining Python3.9 is therefore **not a clean remediation disposition**. The earlier provisional pytest-only residual plan is superseded by this evidence.

## Actual execution

Two fresh isolated **Python3.9.6** production environments were created. Bootstrap pip/setuptools were upgraded to the newest versions compatible with that interpreter; each project was installed from the external candidate. Both installation and `pip check` passed. A separate **pip-audit2.10.1/Python3.11.15** auditor inspected each Python3.9 `site-packages` via `--path`; no auditor packages were installed into the application targets.

| Target | Distributions | Raw advisory records | Unique GHSA | Runtime GHSA | Runtime severity | Bootstrap GHSA | Audit exit |
|---|---:|---:|---:|---:|---|---:|---:|
| API production | 48 | 37 | 21 | 15 | 1 critical, 8 high, 5 medium, 1 low | 6 | 1 |
| AI production | 23 | 25 | 14 | 8 | 1 critical, 2 high, 4 medium, 1 low | 6 | 1 |

Feed duplicates are normalized by identical GHSA identity; raw JSON remains preserved. These findings are actual package/advisory results, not inferred from age or installation warnings. Production installation identifies exposure requiring disposition; exploitation preconditions are separate and no exploit was attempted.

## Why a compatible Python3.9 patch-only resolution is unavailable

| Installed legacy component or constraint | Required patched release | Captured patched `Requires-Python` | Consequence |
|---|---|---|---|
| `anyio==4.12.1` | `4.14.2` | `>=3.10` | One critical TLS issue and one medium process-pool issue remain on3.9. |
| `python-dotenv==1.2.1` | `1.2.2` | `>=3.10` | Existing medium advisory has no listed compatible3.9 fix. |
| `fastapi==0.128.8` → `starlette<1.0.0,>=0.40.0`; resolved `starlette==0.49.3` | Starlette `1.0.1`, `1.1.0`, `1.3.0`, `1.3.1` for the captured findings | Earliest and final patched versions checked require `>=3.10` | Upstream patches cannot be installed on3.9; forced Starlette overrides would also violate installed FastAPI constraints. |
| `boto3==1.42.97` → `botocore==1.42.97` → `urllib3<1.27,>=1.25.4` when `python_version<3.10`; resolved `urllib3==1.26.20` | urllib3 `2.5.0` through `2.8.0`, with `2.8.0` covering the current captured set | `2.8.0` requires `>=3.10` | No compatible3.9 patch satisfies both the interpreter and actual SDK dependency range. |
| Latest compatible3.9 bootstrap `pip==26.0.1`, `setuptools==82.0.1` | pip `26.1`/`26.1.2`/`26.2.0`; setuptools `83.0.0`/`84.1.0` | Checked first patched tooling releases require `>=3.10` | Bootstrap findings are separate from runtime findings, but cannot honestly be called patched on3.9. |

Exact PyPI release metadata and actual installed `Requires-Dist` constraints are retained in `prufai-security-python39-fixed-version-compatibility.json` and the target inspect JSON files. The same candidate on maintained Python3.11 with patched tooling had zero known production-runtime findings; that result must still be repeated on the final branch.

## All unique legacy findings

The following table covers the union of the two production audits. Pip/setuptools rows are bootstrap tooling, not application runtime.

| Scope / package version | Advisory / CVE | Severity | Affected range | Patched version(s) | Dependency path |
|---|---|---|---|---|---|
| application runtime; `anyio==4.12.1` | [GHSA-5p39-cfhj-2xmp](https://github.com/agronholm/anyio/security/advisories/GHSA-5p39-cfhj-2xmp); CVE-2026-64847 | **MEDIUM** | `< 4.14.2` | `4.14.2` | prufai-api or prufai-ai → fastapi → starlette → anyio |
| application runtime; `anyio==4.12.1` | [GHSA-82r6-8w77-94w6](https://github.com/agronholm/anyio/security/advisories/GHSA-82r6-8w77-94w6); CVE-2026-63374 | **CRITICAL** | `< 4.14.2` | `4.14.2` | prufai-api or prufai-ai → fastapi → starlette → anyio |
| application runtime; `python-dotenv==1.2.1` | [GHSA-mf9w-mj56-hr94](https://github.com/theskumar/python-dotenv/security/advisories/GHSA-mf9w-mj56-hr94); CVE-2026-28684 | **MEDIUM** | `< 1.2.2` | `1.2.2` | prufai-api → pydantic-settings → python-dotenv |
| application runtime; `starlette==0.49.3` | [GHSA-82w8-qh3p-5jfq](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq); CVE-2026-54283 | **HIGH** | `>= 0.4.1, < 1.3.1` | `1.3.1` | prufai-api or prufai-ai → fastapi → starlette |
| application runtime; `starlette==0.49.3` | [GHSA-86qp-5c8j-p5mr](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr); CVE-2026-48710 | **MEDIUM** | `<= 1.0.0` | `1.0.1` | prufai-api or prufai-ai → fastapi → starlette |
| application runtime; `starlette==0.49.3` | [GHSA-jp82-jpqv-5vv3](https://github.com/Kludex/starlette/security/advisories/GHSA-jp82-jpqv-5vv3); CVE-2026-54282 | **LOW** | `< 1.3.0` | `1.3.0` | prufai-api or prufai-ai → fastapi → starlette |
| application runtime; `starlette==0.49.3` | [GHSA-wqp7-x3pw-xc5r](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r); CVE-2026-48818 | **HIGH** | `< 1.1.0` | `1.1.0` | prufai-api or prufai-ai → fastapi → starlette |
| application runtime; `starlette==0.49.3` | [GHSA-x746-7m8f-x49c](https://github.com/Kludex/starlette/security/advisories/GHSA-x746-7m8f-x49c); CVE-2026-48817 | **MEDIUM** | `< 1.1.0` | `1.1.0` | prufai-api or prufai-ai → fastapi → starlette |
| application runtime; `urllib3==1.26.20` | [GHSA-2xpw-w6gg-jr37](https://github.com/urllib3/urllib3/security/advisories/GHSA-2xpw-w6gg-jr37); CVE-2025-66471 | **HIGH** | `>= 1.0, < 2.6.0` | `2.6.0` | prufai-api → boto3 → botocore → urllib3 |
| application runtime; `urllib3==1.26.20` | [GHSA-38jv-5279-wg99](https://github.com/urllib3/urllib3/security/advisories/GHSA-38jv-5279-wg99); CVE-2026-21441 | **HIGH** | `>= 1.22, < 2.6.3` | `2.6.3` | prufai-api → boto3 → botocore → urllib3 |
| application runtime; `urllib3==1.26.20` | [GHSA-8988-9cw3-xx77](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77); CVE-2026-97687 | **HIGH** | `>= 1.26.0, < 2.8.0` | `2.8.0` | prufai-api → boto3 → botocore → urllib3 |
| application runtime; `urllib3==1.26.20` | [GHSA-gm62-xv2j-4w53](https://github.com/urllib3/urllib3/security/advisories/GHSA-gm62-xv2j-4w53); CVE-2025-66418 | **HIGH** | `>= 1.24, < 2.6.0` | `2.6.0` | prufai-api → boto3 → botocore → urllib3 |
| application runtime; `urllib3==1.26.20` | [GHSA-pq67-6m6q-mj2v](https://github.com/urllib3/urllib3/security/advisories/GHSA-pq67-6m6q-mj2v); CVE-2025-50181 | **MEDIUM** | `< 2.5.0` | `2.5.0` | prufai-api → boto3 → botocore → urllib3 |
| application runtime; `urllib3==1.26.20` | [GHSA-qccp-gfcp-xxvc](https://github.com/urllib3/urllib3/security/advisories/GHSA-qccp-gfcp-xxvc); CVE-2026-44431 | **HIGH** | `>= 1.23, < 2.7.0` | `2.7.0` | prufai-api → boto3 → botocore → urllib3 |
| application runtime; `urllib3==1.26.20` | [GHSA-vxq7-64xx-v4gw](https://github.com/urllib3/urllib3/security/advisories/GHSA-vxq7-64xx-v4gw); CVE-2026-97689 | **HIGH** | `>= 1.10.3, < 2.8.0` | `2.8.0` | prufai-api → boto3 → botocore → urllib3 |
| bootstrap tool; `pip==26.0.1` | [GHSA-58qw-9mgm-455v](https://github.com/advisories/GHSA-58qw-9mgm-455v); CVE-2026-3219 | **MEDIUM** | `<= 26.0.1` | `26.1` | venv bootstrap → pip |
| bootstrap tool; `pip==26.0.1` | [GHSA-jp4c-xjxw-mgf9](https://github.com/advisories/GHSA-jp4c-xjxw-mgf9); CVE-2026-6357 | **MEDIUM** | `< 26.1` | `26.1` | venv bootstrap → pip |
| bootstrap tool; `pip==26.0.1` | [GHSA-qwm4-qh6w-59xr](https://github.com/pypa/advisory-database/tree/main/vulns/pip/PYSEC-2026-3721.yaml); CVE-2026-13346 | **MEDIUM** | `< 26.2.0` | `26.2 / 26.2.0` | venv bootstrap → pip |
| bootstrap tool; `pip==26.0.1` | [GHSA-wf93-45jw-7689](https://github.com/pypa/advisory-database/tree/main/vulns/pip/PYSEC-2026-196.yaml); CVE-2026-8643 | **MEDIUM** | `< 26.1.2` | `26.1.2` | venv bootstrap → pip |
| bootstrap tool; `setuptools==82.0.1` | [GHSA-h35f-9h28-mq5c](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c); CVE-2026-59890 | **MEDIUM** | `< 83.0.0` | `83.0.0` | venv bootstrap → setuptools |
| bootstrap tool; `setuptools==82.0.1` | [GHSA-xmj3-9gf7-h52w](https://github.com/pypa/setuptools/security/advisories/GHSA-xmj3-9gf7-h52w); No CVE listed | **LOW** | `>= 38.2.2, < 84.1.0` | `84.1.0` | venv bootstrap → setuptools |

## Required compatibility decision

An explicit minimum **Python3.11** for both services aligns with the existing CI and the actually exercised modern full application baseline. It avoids an untested compromise floor. The [official Python lifecycle table](https://devguide.python.org/versions/) lists3.9 as end-of-life on **2025-10-31**,3.10 as end-of-life on **2026-10-01**, and3.11 in security support through **2027-10** as of this audit. Supporting3.10 would therefore not supply an upstream-maintained minimum on2026-11-04.

This is a deliberate compatibility change: existing3.9/3.10 environments must be rebuilt using a supported interpreter and freshly resolved dependencies; changing a virtual environment in place is insufficient. No database reset or migration is needed merely to rebuild application dependencies. The service metadata must reject old interpreters rather than silently retaining vulnerable resolutions.

Once that minimum is explicit, the Python3.9 rationale for retaining pytest8 no longer applies. A bounded, documented move to **pytest>=9.0.3,<10** can fix its development advisory; it requires a new complete regression and audit run. CI private temporary directories remain useful hardening but are not a replacement for available patched pytest on the supported runtime.

No repository manifest, source, CI, migration or database was modified by this supplementary audit. Final minimum-version enforcement, application/development audits, complete regressions and exact-SHA remote CI remain required before remediation closure.

## Evidence files

- `prufai-security-python39-inventory.json`: normalized findings, counts, constraints and recommendation.
- `prufai-security-python39-primary-advisories.json`: captured primary advisory metadata.
- `prufai-security-python39-fixed-version-compatibility.json`: direct PyPI patched-release metadata.
- `prufai-security-py39-api-prod-{audit.json,inspect.json,freeze.txt,audit.exit}`.
- `prufai-security-py39-ai-prod-{audit.json,inspect.json,freeze.txt,audit.exit}`.
