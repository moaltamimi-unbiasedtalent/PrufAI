# Deferred reviews before production readiness

Development and synthetic acceptance can continue while these reviews are open.
An item closes when its authorized decision or environment-specific evidence is
recorded. Keep the evidence link, exact revision or environment, accountable
reviewer, actual decision date, findings and any corrective action on the item.
This register records deferral of the PR #56 review to pre-production. The
historical GitHub review record stays intact.

| Item | Current evidence | What closes it |
| --- | --- | --- |
| PR #56 merge-control exception | Open. Independent pre-merge review was not verified. The technical baseline on `cdde88020aef060362c3300b057d3dcd9b0967f0` passed. [PR #56](https://github.com/moaltamimi-unbiasedtalent/PrufAI/pull/56) | A named authorized reviewer independent of the change and merge reviews the exact diff at `3c103890568f19c5d51f2611925b2fefc9363002` and records their actual date, findings, disposition and required actions. Retain the appointment and decision references. |
| S5B-RO.2 and Berlin editorial governance | Open. Synthetic LiD evidence does not authorize editorial onboarding or publication. [LiD practice foundation](../lid-practice-foundation.md) | Designate independent recovery authority, complete the authorized trust-root rehearsal and separately obtain source metadata, rights and expert review evidence before governed editorial use. |
| Berlin LiD internal human UAT and accessibility | Independent sessions and manual accessibility acceptance remain open. [O2A open gates](deployment-reliability-o2a.md#open-deployment-and-pilot-gates) | Record actual internal candidate, institution and Control Centre sessions, keyboard testing and any actual assistive technology results. Review and resolve genuine blockers before production readiness. |
| Privacy/controller authority and operational retention | Privacy authority, real request handling and production retention/deletion remain open. [Governed privacy cases](../security/data-lifecycle/governed-privacy-cases.md) · [Retention readiness](../security/data-lifecycle/retention-readiness.md) | Assign qualified controller/privacy review, approve a real request channel, and record retention, processor, deletion, hold and recovery decisions with operational evidence. |
| Production storage and managed S3 | Production provider selection and live environment verification are open. Guarded MinIO containment evidence is specific to the tested topology. [Guarded storage](../security/storage-readiness-o1b2/README.md) · [Managed S3](../security/managed-s3-o1b3/README.md) | Approve the provider and configuration, verify least-privilege access, storage isolation and restore/migration evidence. If choosing AWS, obtain separate authority and run genuine managed-S3 synthetic acceptance. |
| Real AI provider and worker deployment | Existing synthetic technical evidence is scoped to disposable infrastructure. [O2A topology](deployment-reliability-o2a.md) | Obtain separate provider and privacy authority, validate approved synthetic provider use, and verify real deployment coverage, consumer health and recovery. |
| Deployment security, monitoring and response | TLS, network containment, secret handling, monitoring aggregation, paging, responder ownership and recovery objectives require deployment-specific evidence. [O2A runbooks](deployment-reliability-o2a.md) | Assign responders, review the target topology and IAM, exercise alerts and recovery in the approved environment and retain independent evidence. |
| Official examination and certification | Official issuance remains default-deny. [LiD practice foundation](../lid-practice-foundation.md) | Obtain separate applicable authority and evidence before enabling any official capability. |
