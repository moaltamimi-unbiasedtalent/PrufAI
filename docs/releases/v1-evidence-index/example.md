# Redacted V1 release-evidence index

**Synthetic illustration only. Source commits and run/artifact IDs are fixture values, not hosted execution evidence. External evidence links are omitted.**

Classification: **EXAMPLE_ONLY** · Profile: **SYNTHETIC_TEST**

Availability snapshot: `2026-11-25T00:00:00Z`. Local checks do not authenticate GitHub records or establish current remote availability.

**Production readiness: NO. Pilot launch: NOT AUTHORIZED. E03A: BLOCKED.**

Informational vulnerability scans do not establish independent security acceptance. Historical review records remain unchanged; no human approval is inferred.

## PR_HEAD

Example source: `f4c73c1ef2e20a531cdb4e6c051d3da4c75cdb12` · Tree: `33b273dfcbd123af64a286b04f1e181847379b54`

| Workflow | Reported conclusion | Verification |
| --- | --- | --- |
| application / 101 | success | LOCAL_METADATA_CHECKED |
| supply-chain / 102 | success | LOCAL_METADATA_CHECKED |

- Browser: completed / success

- Python: completed / success

- Web: completed / success

- Container/Go scans (informational): completed / success

- Language scans (informational): completed / success

Required reports: **2**; required skips: **0**; verification: **LOCAL_PARSED**.

Applied migration head: `0001_fixture`. General-suite skips are outside these required-report totals.

| Artifact | Availability / verification | SHA-256 |
| --- | --- | --- |
| containers | LOCAL_AVAILABLE / LOCAL_CHECKSUM_MATCHED | `8c65b7e999c5e4a76178a57a6a651469853e08827b64ae5467779e746d304878` |
| language | LOCAL_AVAILABLE / LOCAL_CHECKSUM_MATCHED | `ea698ce4cbfd5d4c278dcb7d4e1531b206d70a7e5e9f3f98c6a3129ceeeccf32` |

containers recorded expiry: `2026-12-24T00:00:00Z`.

language recorded expiry: `2026-12-24T00:00:00Z`.

E06A manifest/rehearsal verification: **LOCAL_CHECKSUMS_MATCHED**.

- `v1-e06a-release-manifest.json` SHA-256: `9be5918cce5216d375b966b3022879b75d1ffd3b0cf5a43a67ca48d6daf90c77`
- `v1-e06a-rehearsal.json` SHA-256: `b1b2edad06cb0d1f8314b2061f61d02c032e57834f781458a8c05637a14c1135`

## POST_MERGE

Example source: `12c491e736915b5b8b38421060418536d5521e9c` · Tree: `33b273dfcbd123af64a286b04f1e181847379b54`

| Workflow | Reported conclusion | Verification |
| --- | --- | --- |
| application / 201 | success | LOCAL_METADATA_CHECKED |
| supply-chain / 202 | success | LOCAL_METADATA_CHECKED |

- Browser: completed / success

- Python: completed / success

- Web: completed / success

- Container/Go scans (informational): completed / success

- Language scans (informational): completed / success

Required reports: **2**; required skips: **0**; verification: **LOCAL_PARSED**.

Applied migration head: `0001_fixture`. General-suite skips are outside these required-report totals.

| Artifact | Availability / verification | SHA-256 |
| --- | --- | --- |
| containers | LOCAL_AVAILABLE / LOCAL_CHECKSUM_MATCHED | `24b00c528e79750bab81ada0256a6aee9d792ca6f5a53c35eb6795c729cda427` |
| language | LOCAL_AVAILABLE / LOCAL_CHECKSUM_MATCHED | `fce9d96c308d2c36e032fb935da64348aebed5c2f302c14f1f13400526f7f5f4` |

containers recorded expiry: `2026-12-24T00:00:00Z`.

language recorded expiry: `2026-12-24T00:00:00Z`.

E06A manifest/rehearsal verification: **LOCAL_CHECKSUMS_MATCHED**.

- `v1-e06a-release-manifest.json` SHA-256: `ba97ca7ba80e45f5836b4a011b05ec79f76c1190d930176c96884027d4f36d07`
- `v1-e06a-rehearsal.json` SHA-256: `d99a196bb3b042995256e9e3f7cb9a4737139ceb88838ef9001a53b06a8933c3`

## Open decisions

| Gate | Disposition |
| --- | --- |
| A01 | OPEN |
| A02 | OPEN |
| E01 | OPEN |
| E02 | OPEN |
| E02A-S1 | OPEN |
| E03 | OPEN |
| E03A | BLOCKED |
| E04 | OPEN |
| E05 | OPEN |
| E05-historical-timeout | ROOT CAUSE UNRESOLVED |
| E06 | OPEN |
| H01 | OPEN |
| H02 | OPEN |
| H03 | OPEN |
| H04 | OPEN |
| H05 | OPEN |
| H06 | OPEN |
| H07 | OPEN |
| H09 | OPEN |
| H10 | OPEN |
| H12 | OPEN |
| O01 | OPEN |
| O02 | OPEN |
| O03 | OPEN |
| O04 | OPEN |
| O05 | OPEN |
| O06 | OPEN |
| O07 | OPEN |
| P1-acceptance | OPEN |
| PR60-P2 | OPEN |
| R81-01 | OPEN |
| backend-guard-P2 | OPEN |
| legacy-fence-P2 | OPEN |
| merge-review-exceptions | OPEN |

Outside-pilot capability gates H08, H11 and A03 remain DEFERRED in the existing register. No capability or decision is enabled here.

Artifact links can expire or be deleted before their recorded expiry. A local retained archive is not proof of current remote access. Refresh authenticated metadata and repeat local checks for a new snapshot. Missing or expired evidence is never a pass.
