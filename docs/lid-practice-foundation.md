# Leben in Deutschland: practice foundation

S3 adds a German-language `INTEGRATION_KNOWLEDGE` draft for the
`LEBEN_IN_DEUTSCHLAND` exam family. CEFR does not apply. The draft is sealed and
cannot be published or used to deliver an exam. The separate Berlin fixture
contains original practice scenarios for disposable acceptance environments.
It carries no BAMF branding, official catalogue questions or keys, legal
recognition, examination-centre authorization, or certificate eligibility.

## Source and rights decisions

- The [BAMF interactive catalogue](https://oet.bamf.de/ords/oetut/f?p=534:1)
  describes 300 general items and 10 for a selected Land. The [BAMF catalogue
  PDF, edition 7 May 2025](https://www.bamf.de/SharedDocs/Anlagen/DE/Integration/Einbuergerung/gesamtfragenkatalog-lebenindeutschland.pdf?__blob=publicationFile&v=22)
  is registered by edition. [EinbTestV Annex 1](https://www.gesetze-im-internet.de/einbtestv/anlage_1.html)
  distinguishes the general and state sections: 310 catalogue entries are
  relevant to one state; 460 is the nationwide total. These figures describe
  the official preparation catalogue, not the number of items stored in PrüfAI.
- [IntTestV § 3](https://www.gesetze-im-internet.de/inttestv/__3.html) refers to
  [EinbTestV § 1](https://www.gesetze-im-internet.de/einbtestv/__1.html) and
  Annex 1 for the form and time policy. The form has 30 general questions and
  3 for one state, four options and one key per item, and 60 minutes. S3's
  selection policy does not reconstruct the unpublished official forms.
- [IntTestV § 10(2)](https://www.gesetze-im-internet.de/inttestv/__10.html) sets
  the 15/33 orientation-course benchmark. [EinbTestV § 1(3)](https://www.gesetze-im-internet.de/einbtestv/__1.html)
  sets the separate 17/33 benchmark for the supervised naturalization test.
  IntTestV § 10(3) addresses a LiD-based certificate when the applicable
  [Land–BAMF administrative agreement](https://www.gesetze-im-internet.de/einbtestv/__2.html)
  exists. S3 does not establish that an agreement, verified residence, or any
  other required condition exists for a candidate. [IntV §§ 17](https://www.gesetze-im-internet.de/intv/__17.html)
  and [20a](https://www.gesetze-im-internet.de/intv/__20a.html) keep integration
  course completion and test-centre approval separate.
- The pinned [IntTestV amendment](https://www.recht.bund.de/bgbl/1/2026/306/regelungstext.pdf?__blob=publicationFile&v=1)
  was issued 20 October 2026 and took effect 1 November 2026. It changes the DTZ
  condition in § 10(1). The LiD provisions in § 10(2)–(3) are unchanged. The
  [EinbTestV edition amendment](https://www.recht.bund.de/bgbl/1/2024/211/regelungstext.pdf?__blob=publicationFile&v=3)
  was issued 26 June 2024 and took effect 1 July 2024, amending Annex 1. The
  [IntV amendment](https://www.recht.bund.de/bgbl/1/2026/218/regelungstext.pdf?__blob=publicationFile&v=1)
  was issued 27 July 2026 and took effect 28 July 2026; its amendment does not
  change §§ 17 or 20a. Edition, issue, retrieval and legal effectivity are
  distinct fields.

Only bibliographic metadata is registered. The source author and metadata
rights reviewer must be different. That review does not grant ingestion,
question, image, derivative or redistribution rights. The draft pins each
exact source version, rights decision and claim checksum. A reviewed effective
successor or a withdrawal disables live previews; historical claims remain
inspectable. The two Rules-as-Code versions remain draft, cannot be bound to
live scoring, and produce separate numerical comparisons. The official
outcome stays `UNDETERMINED`, even when both numerical thresholds are met.

## Technical practice flow

### Set up a local walkthrough

From the repository root, use the same explicit disposable PostgreSQL/pgvector
target as the [integrated browser runner](../scripts/run-integrated-browser.sh).
Set `DATABASE_URL` and `PRUFAI_DISPOSABLE_DATABASE` to the exact newly allocated
test database, then run these commands in the API virtual environment.

```sh
export PYTHONPATH="$PWD/services/api"
export ENVIRONMENT=development PRUFAI_DEMO_ENABLED=1
python scripts/migrate-evaluation-disposable.py
python -m app.scripts.seed_demo
python -m app.scripts.seed_lid_practice --prepare-demo
```

The final command registers the five bibliographic editions, records
`TEST ONLY` metadata-only decisions under a separate fixture identity, seals
the technical draft, and prepares the synthetic Berlin practice exam. The
fixture reviewer has no password. Running the command again returns the same
blueprint and exam IDs. Use a fresh disposable database for this shortcut. An
incompatible existing source decision or fixture causes setup to stop.

Start the API with `make api-dev` in a terminal with the same disposable
database and development settings. Start the web app in another terminal with
`PRUFAI_API_BASE_URL=http://127.0.0.1:8000 PRUFAI_DEMO_ENABLED=1 make web-dev`.
At [the local login page](http://localhost:3000/login), sign in as
`admin@demo.prufai.local`.
Open **Assign practice assessment**, select **Demo Candidate** and
**Leben in Deutschland — Practice**, then choose **Assign assessment**.
Sign in as `candidate@demo.prufai.local`, choose **Prepare exam**, select
**Berlin**, and start the exam. Both accounts use the local-only password
`Demo-PrufAI-2026!` unless `PRUFAI_DEMO_PASSWORD` was set during seeding.

### Exercise the governed flow manually

1. A platform operator uses **Control Centre → Standards & packs → Leben in
   Deutschland blueprint** to register sources, inspect independent metadata
   rights decisions, and create the sealed draft. The workbench shows sources,
   the 16 jurisdictions, exact rule versions, and publication blockers.
2. In the existing guarded browser setup, run
   `python -m app.scripts.seed_lid_practice --blueprint-version-id <draft-version-id>`
   from `services/api` after the draft is ready. This requires development demo
   enablement and an explicit disposable PostgreSQL database. It refuses the
   default database. Distinct existing actors author and approve the private
   synthetic Question Bank versions. The seed is a test fixture installer, not
   a platform publication endpoint.
3. An institution administrator creates an assignment for the eligible Berlin
   practice ExamVersion. The candidate explicitly selects Berlin. All 16 codes
   are recognized, but the other 15 return `CONTENT_UNAVAILABLE`. The choice
   is not interpreted as residence. The form receipt pins the draft, technical
   pack, both pools, the seed, ordering, item versions and key checksums. The
   attempt adds its identity and freezes the same form and timer through resume.
4. The candidate submits through the standard runtime. An institution actor
   runs the existing scorer. The owned practice preview reads the persisted
   ScoringRun and ResultVersion and shows the correct count and each rule
   comparison. Incomplete answers or unavailable rules cannot produce a
   numerical threshold claim. No candidate-facing response contains the answer
   key or private rights decisions. `NOT OFFICIAL / NOT CERTIFIED` remains
   visible. Ordinary practice release still follows the existing governance
   workflow; LiD cannot be given certificate authority, even when the general
   certificate feature is enabled.

The acceptance workflow requires real Alembic PostgreSQL and private MinIO;
source retrieval runs the deterministic adapter as `TEST_ONLY`. Browser proof
uses the authenticated platform, institution and candidate routes. A browser
pass does not validate protected official content, provider authorization,
formal equivalence, or the quality of an external retrieval system.
