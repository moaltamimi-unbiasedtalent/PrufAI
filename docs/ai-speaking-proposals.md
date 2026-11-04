# Step 2B.4B.2: governed AI-assisted Speaking foundation

This is a technical execution foundation for non-final Speaking proposals.
Original verified private audio remains the primary evidence. It does not establish
German proficiency accuracy, CEFR reliability, pronunciation validity, fluency
validity, fairness or examiner agreement. A worker cannot approve an assessment,
create a final score, release a result or grant certification authority.

## Supported modes and existing sources of truth

The operational executor supports only an explicitly published
`AI_ASSISTED / AUDIO` rubric. Every criterion must require AUDIO evidence. The
published policy pins the exact approved execution profile ID and version, and
requires human review. HUMAN_ONLY / AUDIO remains independent, creates no AI job
and retains its existing two-human assessment/scoring/release path.

AI_ASSISTED / TRANSCRIPT and AI_ASSISTED / AUDIO_AND_TRANSCRIPT are explicitly
unsupported by this executor. They are not silently selected. Any future
transcript-dependent execution must independently require an exact, legitimate
matching READY transcript; caller text cannot become acoustic evidence. Existing
derived transcription history remains a separate feature. The AUDIO executor
neither requests nor creates a transcript.

`AIModelProfile`, `PromptVersion`, `AgentVersion`, `AIWorkflowVersion`, `AIRun`,
`AIRunStep`, `SubjectiveAssessmentVersion` and `HumanReviewRequest` remain the
existing registry, execution, assessment and review roots. Shared execution-bundle
logic lives in `app/ai_orchestration/execution_profiles.py`; the Writing contract
retains its established schemas, public helper interfaces and fingerprints. The
Speaking contract supplies a distinct fixed purpose and AUDIO schema. This is not
a general autonomous-agent executor.

`SpeakingExecutionJob` is a domain-specific durable execution receipt. It does not
replace the assessment root or put Speaking work into a transcription table.
Additive migration `0029_speaking_execution` follows the repository's dynamic head
at implementation start, `0028_writing_execution`. Historical migrations are not
rewritten. Existing registry creation timestamps remain mapped to their existing
schema columns.

## Exact audio identity and transport

Requests pin organisation, candidate, attempt, ExamVersion, task/item binding,
QuestionBankItemVersion, submitted current response revision/checksum, exact
PRIMARY_AUDIO binding, recording session, CandidateMediaAsset, original SHA256,
byte size, verified duration, complete-validation provenance, recording policy,
Standard Pack and published Speaking rubric/checksum. No caller supplies a storage
key. Recovery does not select a newer asset or profile.

The shared submitted-audio resolver is in `app/speaking_audio/evidence.py`.
Transcription's existing resolver delegates to it without creating a transcript.
The worker uses the actual private S3 backend, reads through `get_bounded()`, and
recomputes the complete original byte count and SHA before provider processing.
Ownership, current submission/binding, complete validation, source checksums,
approval and requester membership are checked again before authoritative handoff.

The provider accepts WAV transport. `app/speaking_execution/audio_transport.py`
fully decodes the verified original through the existing bounded media runtime,
then creates an explicit PCM16 WAV rendition. It preserves sample rate, channels
and relative sample timing. PCM16 quantization is explicit; this is not claimed to
be a lossless bit-depth transformation. No trimming, speech synthesis, denoising,
downmix, resampling, time stretching or transcript substitution is performed.
Unsupported discontinuous timelines fail closed rather than inventing a mapping.

The immutable original is unchanged and remains the authenticated playback source.
The transport bytes are temporary, not a replacement CandidateMediaAsset. The job
pins a separate transport checksum, byte size, format, sample format/rate/channels,
frame count, duration, transformation policy/version and converter version.
The base request checksum stays immutable; `provider_input_checksum` binds the
actual model metadata plus rendition identity. Retries must reproduce those pins.
The application caps rendered audio at 20,000,000 bytes and 180 seconds, with
stricter approved profile limits permitted.

## Audio-capable genuine provider and approval

The implemented adapter is Azure OpenAI `gpt-audio-1.5`, exact deployment model
version `2026-02-23`, adapter `1`. It uses the documented
`POST https://<approved-resource>.openai.azure.com/openai/v1/chat/completions`
contract, with the approved deployment name and `input_audio` WAV bytes. It asks
for text output only and creates no generated speech or transcript artifact.

This audio model does not provide the Writing adapter's native Structured Outputs.
The truthful output contract is `LOCAL_VALIDATED_JSON`: trusted instructions ask
for one JSON object, then strict local parsing and domain validation constrain the
result. The profile records native structured output as false. There is no invented
`response_format: json_schema` capability.

An actual platform actor with current `ai.manage` creates the fixed Speaking bundle
through `POST /api/v1/platform/ai/speaking-execution-profiles`. An independent actor
with current `ai.manage` and `ai.review` approves exact artifact IDs and the expected
profile fingerprint through `/speaking-execution-profiles/approve`. Tenant context,
self approval, stale fingerprints, credentials embedded in configuration and
incompatible modalities fail closed. API responses project safe version metadata,
not private prompts or cloud configuration.

The genuine configuration requires the following safe, exact values:

- Schema/adapter version `1`, model revision `2026-02-23`, and
  `output_contract: LOCAL_VALIDATED_JSON`.
- Approved Azure resource name, subscription UUID, resource group, deployment name,
  exact HTTPS endpoint and resource region (`northeurope` or `swedencentral`).
- `deployment_type: GlobalStandard`, `processing_scope: GLOBAL` and
  `version_upgrade_policy: NoAutoUpgrade`.
- Exact `expected_account_etag` and `expected_deployment_etag` captured from the
  approved deployment. An account/deployment edit requires a new approved bundle.
- `credential_reference: PRUFAI_SPEAKING_AZURE_OPENAI_API_KEY` and
  `management_credential_reference: PRUFAI_SPEAKING_AZURE_MANAGEMENT_TOKEN`.
- Safe model-configuration and processing-approval references, a timezone-aware
  `processing_approval_valid_until`, and the exact processing-reference set in
  `app/speaking_execution/runtime.py:PROCESSING_REFERENCES`.
- Optional bounded output-token, audio-byte and audio-duration limits.

The processing references cover legal basis, consent, DPA, residency, retention,
logging/training controls, candidate-audio transfer, data minimization and explicit
cross-border transfer authorization. References are not proof that those legal
arrangements exist; an authorized deployment operator must establish them.

**Global Standard does not mean EU-only inference**, even when its Azure resource
is in Europe. This adapter requires explicit GLOBAL processing approval and does
not claim EU-only processing. An EU resource or endpoint alone grants no legal
authorization.

Separately allowlist the exact bundle fingerprint in the API and worker deployment:

```sh
SPEAKING_APPROVED_PROFILE_FINGERPRINTS='["<exact approved bundle SHA256>"]'
SPEAKING_PROVIDER_TIMEOUT_SECONDS=90
```

Supply credentials from the deployment secret manager, never registry JSON,
requests, source files or logs. The management credential needs read access only;
this code performs no deployment creation/update or auto-upgrade operation.

Before approval and before/after genuine inference, read-only management GETs
verify the exact account ID, endpoint, location, provisioning state and ETag, and
the deployment ID, model/revision, Global Standard SKU, provisioning state,
NoAutoUpgrade and ETag. Missing or changed metadata fails closed. Only the approved
Azure host and fixed `management.azure.com` metadata paths are used; redirects and
arbitrary egress are rejected. No model fallback or automatic version substitution
is performed. Retired or unavailable deployments fail rather than upgrading.

Configuration availability does not assert worker health or successful inference.
Empty deployment allowlists, missing credentials/processing references, expired
processing approval or unsupported capability yield UNCONFIGURED. Genuine provider
transfer remains closed by default. `store: false` is requested, without claiming
universal zero retention or independently verifying legal arrangements.

The implementation is based on the official [Azure model catalog](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure),
[model-update policy](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/working-with-models),
[deployment GET contract](https://learn.microsoft.com/en-us/rest/api/aiservices/accountmanagement/deployments/get?view=rest-aiservices-accountmanagement-2024-10-01),
[chat API](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/chat?preserve-view=true&view=rest-microsoft-foundry-v1)
and [audio input requirements](https://learn.microsoft.com/en-us/azure/foundry/openai/audio-completions-quickstart).
These establish transport capabilities, not pronunciation/fluency validity.

## Worker, handoff and human decision

Load the repository's existing PostgreSQL/private S3 settings and apply migrations:

```sh
cd services/api
python3 -m alembic upgrade head
python3 -m app.speaking_execution.worker
# At most one eligible claim, as used by bounded acceptance tests:
python3 -m app.speaking_execution.worker --once
```

The worker follows the established PostgreSQL `SKIP LOCKED` claim, renewable lease,
fencing, bounded retry, crash recovery and cancellation pattern. Live institution
membership and `subjective.assess` are required, independently of a platform role.
Same-candidate self-assessment, cross-tenant replay and caller-authored run/proposal
overrides are denied. Processing authorization must still be current at completion.
Safe attempted/completed/failed processing audit metadata does not contain audio,
storage keys, credentials, full prompts or provider bodies.

The exact approved workflow is INPUT → PROPOSAL → RULE_CHECK → HITL, with one audio
provider invocation and no tools. Those are not independent assessor models. The
worker validates complete criteria, bounded Decimal marks, permitted levels,
bounded rationale/uncertainty fields, exact source/rubric/execution identity and
strict AUDIO-only references. Timestamps must be actual integers, not booleans,
with `0 <= start_ms < end_ms <= verified original duration`. Transcript evidence,
wrong SHA/asset/revision, extra authority fields and malformed output are rejected.

Only a completed job with an actual SUCCEEDED run/step and matching input/output
receipt can invoke `SpeakingAssessmentService.propose()`. That existing service
creates AI_PROPOSED and one pending HumanReviewRequest atomically. Generic AI
completion, review and checkpoint actions cannot bypass it, including through a
shadow assessment-root label. Retries cannot duplicate the authoritative handoff.

The existing Speaking workspace shows original authenticated audio playback,
verified metadata, published rubric, criterion proposals, proposed audio spans and
model/run provenance. The span's acoustic relevance is not independently proved;
the reviewer must listen and judge it. Independent human fields start blank.
Decisions require current review permission, exact checksum, operation ID, reason
and complete explicit criterion judgments with rationale/evidence; applicable
separation of duties and immutable approved successors remain enforced.

Control Centre `speaking-proposals` exposes safe job/profile/workflow state, retry
counts, failure classification, current registry approval and review status. It
does not expose audio, candidate text, transcripts, storage keys, private prompts
or provider payloads. Completed historical evidence is retained; approving a
profile never grants assessment approval, release or certification authority.

## Technical execution and evaluation boundary

`DETERMINISTIC_TEST / speaking-fixture-v1 / model revision 1` is available only with
`SPEAKING_TEST_ADAPTER_ENABLED=true` in explicit automated test configuration.
Guarded development additionally requires both `PRUFAI_DEMO_ENABLED=1` and
`PRUFAI_E2E_ALLOW_DEMO=1`. Production rejects the flag. This adapter consumes real
stored synthetic audio and a real bounded rendition but provides no speech
understanding or linguistic/acoustic quality judgment. It is never a fallback.

The isolated official seed creates only synthetic prerequisite governance and an
explicit AI/AUDIO rubric, not recordings, transcripts, submissions, jobs, AI runs,
assessment decisions, scores or releases. It retains required normative Standard
Pack authority and requires actual original-audio evidence. It does not invent
retrieval citations. Existing human and Writing namespaces remain independent.

Required PostgreSQL/MinIO proof uses fresh Alembic databases and real private S3;
it never uses `Base.metadata.create_all()`. Actual separate workers, process
termination/restart, competing claims, retries, rollback, cancellation, exact
source/profile pins, current-role revocation, strict outputs and immutable history
are exercised. Required CI asserts a positive test count and zero skips. HTTP
adapter tests mock transport and do not establish real-provider execution.

The complete browser suite adds one seventeenth authenticated journey: actual
candidate recording/finalization/private persistence/binding/submission, exact
institution request, separate TEST ONLY worker, original reviewer playback and
pending review. It stops without approval, scoring, release or transcription.

Technical safety fixtures cover invalid spans/criteria/modality, wrong provenance,
malformed output and structural prompt-injection boundaries. Tone fixtures do not
prove semantic resistance to spoken prompt injection or any German/CEFR/acoustic
scoring accuracy. Authentic AI readiness requires a separately approved real
provider smoke using original synthetic spoken German. Without that actual call,
report REAL PROVIDER SMOKE = NOT RUN and AUTHENTIC AI VERIFIED = NO.
