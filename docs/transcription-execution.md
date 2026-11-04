# Step 2B.4A: durable governed transcription execution

Original private candidate audio remains the primary evidence. This execution
path produces derived transcript evidence only. It does not create criterion
marks, a subjective approval, a score, a result release, an appeal decision or
certification authority. The HUMAN_ONLY/AUDIO Speaking journey remains usable
without any transcript or worker.

## Identity, access and durability

An institution actor with actual current `speech_transcription.request`
membership authority requests execution for an exact submitted response revision
and original READY PRIMARY_AUDIO asset. The service revalidates complete-input
validation, duration, recording policy, item version, candidate, organisation,
response binding and original-byte checksum before queueing and before completion.
There is no object-key input and no latest-media lookup.

`SpeechTranscriptionJob` is an execution record attached to the existing
`SpeechTranscription` root. `SpeechTranscriptionVersion` remains the only transcript
evidence history. The job stores the exact source identity and an immutable
profile/configuration snapshot, including a content-addressed profile version,
provider/model/adapter, language, safe deployment references and checksums. Worker
recovery does not select a newer current profile. Secrets are never stored in the
profile snapshot or job.

New requests must match the latest exact independent registry-approval record;
an edited ACTIVE row cannot inherit approval for different configuration. The job
pins that approval identity, and recovery validates the historical approval rather
than selecting a newer registry revision. TEST ONLY fixture approvals are marked
as automated technical approvals and cannot authorize external processing.

`INSTITUTION_ADMIN` and `EXAM_MANAGER` receive request/read permission. Generic
Question Bank REVIEWER and candidate roles do not. Current active membership and
database role grants are reloaded; a platform permission alone does not confer
institution transcript access. Reading transcript history requires the separate
`speech_transcription.read` permission. Platform AI Operations projects only safe
job/provider/version/attempt/timing/failure metadata, never candidate identity,
speech, transcript, private keys, credentials or provider payloads.

## Worker lifecycle and failure recovery

Apply the repository's Alembic workflow first, then run the separate worker using
the same PostgreSQL and private object-storage configuration as the API:

```sh
cd services/api
python3 -m alembic upgrade head
python3 -m app.speech_transcription.worker --poll-interval 2
```

An existing deployment must also reconcile the repository's predefined capability
vocabulary using its existing `seed_defaults(session)` maintenance function before
granting institution access. This creates no users or role assignments:

```sh
cd services/api
python3 - <<'PY'
from app.authorization.seed import seed_defaults
from app.config import get_settings
from app.db import create_session_factory
with create_session_factory(get_settings())() as session:
    seed_defaults(session)
PY
```

For a bounded one-job operational check:

```sh
cd services/api
python3 -m app.speech_transcription.worker --once
```

Use SIGTERM or Ctrl-C for graceful shutdown. The worker stops claiming new work;
an interrupted claim is recoverable after its lease expires. Configure process
supervision to restart the worker. The API does not execute provider calls in a
FastAPI background task and Redis is not the durable queue.

Claims use PostgreSQL row locks with SKIP LOCKED, persisted lease/token/attempt
state and bounded retries. A claim is committed before private-storage/provider
I/O. Completion rechecks the exact source, current requester authority and fencing
token; job completion and the existing transcript-version insert commit together.
A unique execution-job version link prevents duplicate authoritative publication.
Expired/stale workers cannot publish over a newer claim. Safe state transition
history is append-only. A retry does not replace or mutate a previous transcript.

This guarantees one authoritative persisted output per execution identity, not
exactly-once external billing. A crash after a provider has accepted audio but
before PostgreSQL commit can require another provider invocation. Do not claim
provider-level idempotence where the provider contract does not provide it.

## External provider: closed by default

The explicit production-capable adapter is OpenAI's file transcription endpoint
using the documented `gpt-transcribe` JSON contract. It performs direct HTTPS to
the EU endpoint only, without environment proxy use, redirects, arbitrary URLs,
public audio URLs or local-disk fallback. Input is bounded to 25,000,000 bytes;
response is bounded to 2,000,000 bytes, with a bounded execution timeout. Provider
error bodies are never copied to operational errors. The JSON adapter does not
invent timestamps, segments or confidence.

Sources: [file transcription](https://developers.openai.com/api/docs/guides/speech-to-text),
[request/response contract](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create),
[data controls](https://developers.openai.com/api/docs/guides/your-data),
[model identifier](https://developers.openai.com/api/docs/models/gpt-transcribe).

The profile pins the documented model identifier, adapter version and entire
configuration. It does not assert an undocumented frozen provider-weights
snapshot. A model/configuration change requires a newly approved fingerprint.

An ACTIVE, explicitly approved `SPEECH_TRANSCRIPTION` model profile must use:

```json
{
  "schema_version": "1",
  "adapter_version": "1",
  "languages": ["de"],
  "endpoint": "https://eu.api.openai.com/v1/audio/transcriptions",
  "region": "EU",
  "deployment_id": "proj_REPLACE_WITH_APPROVED_EU_PROJECT",
  "credential_reference": "PRUFAI_SPEECH_OPENAI_API_KEY",
  "model_configuration_version": "REPLACE_WITH_APPROVED_CONFIG_VERSION",
  "response_format": "json",
  "processing_approval_reference": "REPLACE_WITH_ACTUAL_APPROVAL_REFERENCE",
  "processing": {
    "legal_basis_reference": "REPLACE_WITH_ACTUAL_REFERENCE",
    "consent_policy_reference": "REPLACE_WITH_ACTUAL_REFERENCE",
    "data_processing_agreement_reference": "REPLACE_WITH_ACTUAL_REFERENCE",
    "residency_approval_reference": "REPLACE_WITH_ACTUAL_REFERENCE",
    "retention_policy_reference": "REPLACE_WITH_ACTUAL_REFERENCE",
    "logging_training_controls_reference": "REPLACE_WITH_ACTUAL_REFERENCE",
    "audio_transfer_authorization_reference": "REPLACE_WITH_ACTUAL_REFERENCE",
    "data_minimization_reference": "REPLACE_WITH_ACTUAL_REFERENCE"
  }
}
```

These reference placeholders are a configuration illustration, not processing
approval. A responsible deployment operator must approve the actual legal basis,
consent policy where applicable, DPA, residency/project eligibility, retention,
logging/training controls, transfer and minimization arrangements. Selecting an EU
URL alone does not establish those arrangements. No retention period or legal
compliance is inferred by this implementation.

Only then configure `SPEECH_TRANSCRIPTION_APPROVED_PROFILE_FINGERPRINTS` as a JSON
list containing the exact approved profile fingerprint and supply the credential
via the referenced environment variable/secret manager. API and worker must share
the same approval allowlist. Never paste credentials into registry configuration,
transcript requests, job rows, logs, documentation or commits. Revoking a runtime
approval closes the worker boundary even for previously queued jobs. Without
approval or credentials the service reports UNCONFIGURED and does not transfer
audio externally.

The existing platform `POST /api/v1/platform/ai/models` creates a DRAFT profile.
`GET /api/v1/platform/ai/models` reports its `transcription_profile_version`.
An independent platform actor with current `ai.manage` and `ai.review` may use
`POST /api/v1/platform/ai/models/{id}/approve-transcription` with
`{"expected_profile_version":"<exact fingerprint>"}`. Self approval, stale
fingerprints and invalid configuration are denied. This registry approval still
does not authorize external transfer; the deployment approval and credential gate
above remains closed until deliberately configured. It grants no assessment or
result authority.

## Technical test adapter and acceptance claims

`DETERMINISTIC_TEST` / `fixture-transcriber` / adapter `1` is available only with
explicit `SPEECH_TRANSCRIPTION_TEST_ADAPTER_ENABLED=true` in a non-production
automated technical test. Development browser execution additionally requires
both guarded `PRUFAI_E2E_ALLOW_DEMO=1` and `PRUFAI_DEMO_ENABLED=1`; ordinary local
development cannot select the adapter by enabling its setting alone.
Production settings reject this opt-in. It is never
selected as a fallback. Its text is explicitly `TEST ONLY`; it is not recognized
speech, German transcription accuracy or evidence of genuine provider availability.
The normal application has this option disabled.

The guarded browser runner explicitly enables the option for an isolated synthetic
candidate/exam/profile. It records and submits real private audio, queues through
the authorized API, launches the separate production worker CLI, observes persisted
QUEUED/RUNNING/READY history and reads the derived TEST ONLY version. No audio,
transcript, execution job, human approval, mark or result is seeded.

The required PostgreSQL/MinIO suite uses fresh disposable Alembic databases and
actual private stored audio. It covers process termination, independent claims,
leases/fencing, storage/provider/output/database failures, retries, exact pins,
profile drift, permissions and immutable history. Unit tests with mocked HTTP are
adapter-contract tests only. Required integration CI asserts tests executed with
zero skips, separately from optional-service skips in the general unit job.

An authentic-provider smoke is a separate gate: only an approved deployment may
send an original synthetic German recording to the genuine adapter and persist
its validated output/provenance. If that configuration is unavailable, record
`REAL PROVIDER SMOKE = NOT RUN` and `AUTHENTIC TRANSCRIPTION VERIFIED = NO`.

## Reviewer routes and operational visibility

The existing institution audio-detail and Speaking-assessment pages expose a
separate derived-evidence panel. Request controls require server-authorized exact
source pins and an available approved profile. State displays distinguish not
requested, queued, running, retryable/failed and ready. Authorized readers can view
immutable historical versions; no automatic Speaking mark or approval is created.
Control Centre AI Operations adds Transcription jobs and a separate configuration
descriptor. CONFIGURED reports configuration availability, not worker liveness or
successful authentic recognition. The generic assessment executor remains separate.
