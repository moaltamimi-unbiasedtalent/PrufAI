# Standards S2: DTZ A2–B1 practice blueprint

The DTZ-informed draft is a German-language A2–B1 **PRACTICE** framework.
It is not an authorized test, a validated equivalent, or a source of official
results or credentials. Its Standard Pack is sealed for inspection and cannot
be used by Exam Studio, production scoring, or certification.

The blueprint uses the existing Standard Pack modules, sections, and task
specifications. The draft rules and review records use the existing
`RuleVersion` and `RubricVersion` lifecycles. S2 adds one immutable provenance
record to the existing Standard Pack version for the claim ledger and exact
artifact references. It does not add another exam, scoring, or rubric registry.

In **Control Centre → Standards & packs → DTZ A2–B1 blueprint**, register the
official metadata and use **Source Registry** for an independent rights
decision on each exact version. Once the metadata is reviewed, create or
inspect the draft. The source bindings preserve both the exact edition and
the applicable citation locator. The draft records its task structure, claims,
draft rules, rubric review records, and publication blockers. A withdrawal or
applicable reviewed successor makes the pinned draft stale for preview. The
historical record remains inspectable.

| Reference | Role in the draft |
| --- | --- |
| [g.a.s.t. DTZ information](https://www.gast.de/de/forschung-entwicklung/entwicklung/auftraege/deutsch-test-fuer-zuwanderer-dtz), [overview](https://www.gast.de/de/forschung-entwicklung/entwicklung/auftraege/deutsch-test-fuer-zuwanderer-dtz/der-dtz-auf-einen-blick), [FAQ](https://www.gast.de/de/forschung-entwicklung/entwicklung/auftraege/deutsch-test-fuer-zuwanderer-dtz/faq) | Undated pages verified 6 November 2026. The start of g.a.s.t. administration in 2023 is not a publication date. |
| [g.a.s.t. June 2024 specimen](https://www.gast.de/fileadmin/gast.de/GAST/5_DTZ/PDF/gast_DTZ_UEbungssatz_1.pdf) | Bibliographic reference for structural facts and historical scoring context. No specimen questions, answer sheets, rubrics, or audio are stored or reused. |
| [BAMF curriculum](https://www.bamf.de/SharedDocs/Anlagen/DE/Integration/Integrationskurse/Kurstraeger/KonzepteLeitfaeden/rahmencurriculum-integrationskurs.pdf?__blob=publicationFile&v=8) and [Council of Europe CEFR Companion Volume](https://rm.coe.int/cefr-companion-volume-with-new-descriptors-2020/16809ea0d4) | Construct context only. The BAMF-commissioned revision identifies 2016 and Goethe-Institut copyright; its exact publication day is unstated. |
| [IntTestV §10](https://www.gesetze-im-internet.de/inttestv/__10.html), [October 2026 gazette](https://www.recht.bund.de/bgbl/1/2026/306/regelungstext.pdf?__blob=publicationFile&v=1) | The amendment was issued 20 October and took effect 1 November 2026. It adds the remaining-domain A2 minimum to the overall B1 condition. It does not establish new raw score thresholds or rubric weights. |
| [IntV §17](https://www.gesetze-im-internet.de/intv/__17.html), [July 2026 gazette](https://www.recht.bund.de/bgbl/1/2026/218/regelungstext.pdf?__blob=publicationFile&v=1) | The current integration-course context distinguishes the language assessment, LiD, and course completion. The July amendment, effective 28 July, concerns course access priority and does not establish a DTZ scoring rule. |

The blueprint distinguishes 20 Listening items and 25 Reading items in one
objective domain, one Writing response selected from two prompts, and separate
Speaking evidence. It represents recorded monologue practice, examiner-mediated
dialogue and pair interaction separately. The latter two cannot be delivered at
full fidelity by the recorded practice runtime. All practice examples are
original. The Speaking count and criteria, Writing criterion terminology, and
approximate oral timing are visible in the claim ledger. A three-section/five-part
interpretation is recorded as evidence for review, with no fabricated resolution.

The dated B1 rule is a **draft preview**: after the amendment starts, a complete
synthetic input qualifies only with Speaking B1, B1 in at least one written
domain, and A2 or B1 in both written domains. The preview does not infer A2 or a
below-A2 overall result. Missing judgments, incorrect pins and stale sources
return `UNDETERMINED`. Published raw bands are evidence claims, not a conversion
policy. The separate objective example uses the existing item scorer on an
original one-point exercise. No candidate, exam or result record is created.

Writing and Speaking have non-executable human review versions with exact
source references. They do not contain invented criteria or weights. Speaking
review requires original audio and criterion-specific time evidence, rather
than transcript-only inference. Independent task and rubric validation,
external reuse rights, rule approval, live interaction fidelity, and separate
publication authority remain open.

CI requires Alembic PostgreSQL with pgvector and private MinIO. Only original
synthetic evidence enters storage/retrieval; deterministic search is a
`TEST_ONLY` check. The integration gate rejects skips. The authenticated
browser journey exercises the real registration, draft, preview, publication
denial and tenant isolation paths. Do not point these tests at a development
database.
