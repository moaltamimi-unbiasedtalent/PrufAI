# V1 completion backlog

Use the [scope proposal](v1-scope-proposal.md) and [readiness register](v1-readiness-register.md) to assign these tickets. Order in A reflects the candidate's route into the pilot and the release dependencies. Engineering can develop and rehearse on disposable data while human and operational decisions are pending.

## A. Must fix or verify before the V1 pilot

| Ticket / gate | Work | Acceptance and retained evidence |
| --- | --- | --- |
| A-01 / E02 | Review the [E02A technical foundation](v1-e02a-enrollment-recovery.md) and dry-run evidence. Select and separately authorize a delivery or staff-assisted method and verify it on the intended target. | New disposable candidate enters the intended institution, rejects expired/replayed tokens and wrong-tenant access, signs out safely on a shared device, and has a usable support route. Retain U01/U02/U13 and exact-commit integration results. |
| A-02 / E03, A01 | Define a manifest verifier for the proposed Berlin form. Rehearse binding with synthetic items. | One manifest pins the form, Berlin items, rights and review references, answers and scoring rules. A stale, withdrawn or changed pin fails. Retain the synthetic verification and, separately, the approved release manifest. |
| A-03 / E04 | Verify scope controls and release confidentiality on the pilot profile. Fix any exposed excluded entry points or role gaps. | Candidate can see only the owner-released practice result. Revocation denies direct API/URL and history access. Tenant crossing, official issuance, audio and external-provider requests are denied for pilot users. Retain positive and negative results. |
| A-04 / E05 | Rehearse keyboard, screen reader, mobile, timed save/resume and error messaging; fix observed completion blockers. | Record dry run and, after authorized participant processing, U03–U05 and U10–U13. Each blocker has a reproducible case, disposition, and retest on the corrected commit. |
| A-05 / E06 | Package the release candidate and rollback evidence. | Pin commit, build and item manifest digests, migration, role/flag profile and exact synthetic rehearsal logs. Demonstrate the selected stop and rollback procedure with retained audit evidence. |
| A-06 / O01–O03 | Prepare target storage, access and backup verification scripts and acceptance notes. | On the separately approved target prove private access, deny paths, TLS, secret rotation and encrypted restore against approved objectives. Record exact environment and result. If AWS is selected, attach its authorized synthetic smoke. |
| A-07 / O04–O07 | Prepare operator exercises and evidence capture. | Named responders receive and acknowledge routed synthetic alerts. Verify the worker/process boundary, lifecycle/request execution and independent target security findings with corrective evidence. |
| A-08 / E01 | Verify baseline and final candidate CI against the exact revisions. | Completed normal Python, Web, Browser and supply-chain runs, required real PostgreSQL, guarded MinIO and Redis, zero required skips, single applied Alembic head, and checksum-verifiable artifacts. Update the register with exact evidence. |

## B. Required human decisions

| Ticket | Work | Acceptance and retained evidence |
| --- | --- | --- |
| B-01 / H01 | Product owner decides scope, institution, cohort and exclusions. | Actual dated scope signoff references the accepted proposal revision. |
| B-02 / H02, H03 | Appoint independent reviewers and reconcile PR #56 and PR #57. | Authenticated dated dispositions or contemporaneous review evidence for each exact revision, plus closure of required actions. Preserve the original histories. |
| B-03 / H04, H05, A01 | Establish S5B-RO.2 authority, rights and examination decisions. | Authorized custody and rehearsal references, item-level rights, independent examiner evidence and approved immutable practice form. |
| B-04 / H06, H07 | Decide privacy, participant handling, retention, storage provider and IAM. | Actual signed or authenticated decisions bound to intended data, processors, region, principals and target. |
| B-05 / H09, H10, A02 | Assign operational owners and approve human UAT and accessibility disposition. | Named responders and exercise approval plus actual UAT and assistive technology evidence, findings and retests. |
| B-06 / H12 | Decide whether to launch the bounded pilot. | Actual authorized launch decision linked to closed applicable gates, cohort and target, release revision and stop criteria. |

## C. Capabilities for a later validated release

| Ticket | Work | Acceptance and retained evidence |
| --- | --- | --- |
| C-01 / A03 | Broaden practice coverage or add CEFR B1/DTZ delivery. | Exact regional or domain item set, independent rights/construct/rule approval and pilot evidence for each claim. Validate live interaction fidelity where applicable. |
| C-02 / A03, H08 | Qualify Writing, Speaking, recorded audio and external transcription/proposals. | Approved purpose and provider if used, scoped population and modality validity evidence, rubric/assessor checks and independent human release controls. Synthetic technical tests remain supporting evidence. |
| C-03 / H11 | Consider official certification only as a separate authorized release. | Explicit competent authority decision and verified official controls for the exact scope. Until that decision keep the feature disabled. |

## D. Optional improvement

| Ticket | Work | Acceptance and retained evidence |
| --- | --- | --- |
| D-01 | Provide a redacted release evidence index with direct links. | A reviewer can locate the exact revision, runs, artifact digests and open decisions without needing participant data. |
| D-02 | Improve operator and support handoff copy after dry runs. | A scenario observer can identify the next authorized action and escalation path from the interface or runbook. |
