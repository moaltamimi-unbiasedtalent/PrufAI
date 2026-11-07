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

Use Python 3.11 or newer, Node.js 22, npm and Docker Compose v2. From the
repository root, install the API into an active virtual environment and use
the committed frontend lockfile.

```sh
python -m pip install -e services/api
npm ci --prefix apps/web
```

The separate Compose stack binds PostgreSQL/pgvector, Redis and MinIO to
loopback. It uses ports 55438, 56379 and 59000. If a port is already in use,
leave that service alone. An operator with an existing acceptance PostgreSQL
service on 55438 can instead allocate a new matching test database there and
supply its explicit URL and separate private Redis and MinIO targets.

In a private shell, generate an owner-only environment file outside Git. Keep
its path for the second terminal and later replays.

```sh
umask 077
PRUFAI_LID_DEMO_ENV="$(mktemp "$HOME/.prufai-lid-demo.XXXXXXXX")"
demo_id="$(openssl rand -hex 16)"
pg_password="$(openssl rand -hex 24)"
cat >"$PRUFAI_LID_DEMO_ENV" <<EOF
ENVIRONMENT=development
PRUFAI_DEMO_ENABLED=1
PRUFAI_DEMO_PASSWORD=$(openssl rand -hex 24)
PRUFAI_LID_PROJECT=prufai-lid-$demo_id
PRUFAI_LID_PG_PASSWORD=$pg_password
PRUFAI_DISPOSABLE_DATABASE=prufai_eval2_liddemo_$demo_id
DATABASE_URL=postgresql+psycopg://prufai:$pg_password@127.0.0.1:55438/prufai_eval2_liddemo_$demo_id
REDIS_URL=redis://127.0.0.1:56379/0
AUTH_RATE_LIMIT_BACKEND=redis
OBJECT_STORAGE_BACKEND=s3
OBJECT_STORAGE_ENDPOINT=http://127.0.0.1:59000
OBJECT_STORAGE_BUCKET=prufai-lid-$demo_id
OBJECT_STORAGE_ACCESS_KEY=lid-local-operator
OBJECT_STORAGE_SECRET_KEY=$(openssl rand -hex 24)
EOF
unset demo_id pg_password
set -a
. "$PRUFAI_LID_DEMO_ENV"
set +a
export PYTHONPATH="$PWD/services/api"

docker compose -p "$PRUFAI_LID_PROJECT" -f infra/compose.lid-demo.yml up -d --wait
curl --fail --retry 10 --retry-connrefused "$OBJECT_STORAGE_ENDPOINT/minio/health/ready"
python scripts/initialize-audio-storage.py
python scripts/migrate-evaluation-disposable.py
python -m app.scripts.seed_lid_practice --prepare-demo
```

The final command requires the repository's current single Alembic head.
On a fresh target it seeds the synthetic organisation and accounts, registers
five bibliographic editions, records `TEST ONLY` metadata decisions under a
separate fixture identity with no password, seals the technical draft and
assigns the Berlin exam. Replay returns the same blueprint, exam and
assignment IDs without resetting passwords, attempts or results. An
incompatible existing fixture stops setup. No Berlin attempt or timer starts
during seeding.

Run the API with the same private environment loaded.

```sh
python -m uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8185
```

Start the web app from the repository root in a second terminal.

```sh
PRUFAI_API_BASE_URL=http://127.0.0.1:8185 PRUFAI_DEMO_ENABLED=1 \
  npm run dev --prefix apps/web -- --hostname 127.0.0.1 --port 3185
```

1. Open [local sign-in](http://127.0.0.1:3185/login). Use the
   `PRUFAI_DEMO_PASSWORD` from the private file with the accounts below. These
   are synthetic local identities.
2. Sign in as `candidate@demo.prufai.local`. Open **Leben in Deutschland —
   Practice**, choose **Prepare exam**, select **Berlin**, then choose
   **Start exam**. Answer a question and reload to verify the saved response.
   Complete the questions and submit.
3. Sign in as `admin@demo.prufai.local`. Choose **Score submitted attempt**,
   then **Inspect score, review and release**. Request human result review,
   approve the reviewed result, and release the practice result.
4. Sign in again as the candidate to view the submitted practice state and the
   released result. The 15/33 and 17/33 comparisons are separate, and the
   official outcome remains undetermined.
5. Sign in as `platform.admin@demo.prufai.local`. Open **Standards & packs →
   Leben in Deutschland blueprint** to inspect the exact draft, source pins,
   and publication blockers.

Stop the API and frontend with Ctrl+C. To stop only the demo services and retain
the data, use `docker compose -p "$PRUFAI_LID_PROJECT" -f infra/compose.lid-demo.yml down`.
After you have verified that the project is the disposable stack you created,
add `--volumes` to that command only if you intend to delete its database and
bucket, then remove the private environment file. Use the same file for replay
or restart while you want to keep the demo.

This local technical demo is separate from the authorized S5 editorial
workspace. **S5B-RO.2 remains a pre-launch requirement.** The synthetic source
decisions are limited to the disposable fixture and convey no official rights.

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
