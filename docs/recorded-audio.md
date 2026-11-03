# Recorded audio primary evidence

This increment stores and replays original candidate audio. It adds no Speaking
human assessment decisions, transcription worker, AI Speaking execution, or
Speaking result/release integration. Automated microphone tests prove transport,
decoding and access control; physical microphone and other browser/device codec
checks remain separate manual acceptance.

## Governed policy and identity

`OralResponseContent.audio_policy` is the single typed source. Question Bank
canonical item content includes the policy; published item versions, ExamVersion
bindings and the frozen runtime manifest pin its version and checksum. Historical
items without a policy are unavailable for new recordings rather than rewritten.
The browser negotiates only server-permitted MediaRecorder MIME/codec types.

A recording receipt pins organisation, candidate, assignment/attempt, exam
version/checksum, item binding, item version/checksum and policy. Attachment also
checks the exact response and expected revision. Start uses a caller operation ID:
a transport replay returns the original session, while changed identity conflicts.
Independent recordings retain independent asset IDs and private keys even when
their SHA-256 values match. Migration `0026_audio_sha_scope` is additive; historical
merged migrations are unchanged, rows and existing private keys are preserved.

## Local private storage and media runtime

Prerequisites: Docker, Python/API dependencies, and the repository's real
PostgreSQL/pgvector development database. Keep database credentials in your local
environment. Do not reset a shared database. Migrate with the existing workflow:
`cd services/api && python3 -m alembic upgrade head`.

Use a private, uncommitted environment file with safe dedicated development
credentials (the following values are examples for local use only):

```sh
export OBJECT_STORAGE_BACKEND=s3
export OBJECT_STORAGE_ENDPOINT=http://127.0.0.1:19000
export OBJECT_STORAGE_BUCKET=prufai-audio-development
export OBJECT_STORAGE_ACCESS_KEY=prufai_audio_local_only
export OBJECT_STORAGE_SECRET_KEY=prufai_audio_local_only_change_me
export OBJECT_STORAGE_REGION=us-east-1
export AUDIO_MEDIA_RUNTIME=docker
export AUDIO_MEDIA_IMAGE=prufai-media:ffmpeg-6.1.1
bash scripts/setup-media-runtime.sh
bash scripts/local-audio-storage.sh start
```

The storage script builds the same exact MinIO source release as CI
(`RELEASE.2025-10-15T17-29-55Z`) with pinned Go 1.24.13 and Ubuntu image digests.
It uses a new `prufai-audio-minio-data` volume and ports 19000/19001 bound to
127.0.0.1. It waits for `/minio/health/ready`, creates the bucket only if absent,
and refuses an existing bucket policy or public/group ACL. It does not replace
credentials on an existing container. Reuse its original environment file.
`bash scripts/local-audio-storage.sh stop` stops only this labelled service and
preserves its volume and objects. No anonymous access is configured. Allow several
GB of Docker build space for the first source build; the script never prunes
images, caches or volumes automatically. Other development services are untouched.

The older Compose MinIO image is not required for this supported path. Do not
repeatedly retry an unavailable registry image or substitute memory/local-disk
objects for actual recording evidence.

The media image uses signed Ubuntu packages with exact FFmpeg/FFprobe
`7:6.1.1-3ubuntu5`. Every inspection runs offline, without capabilities, as a
non-root user, read-only except bounded tmpfs, with CPU/memory/PID limits and a
wall-clock timeout. Original bounded input enters the private container through
stdin, avoiding host file-sharing assumptions. Only frame metadata/CRCs leave the
decoder. Full-input inspection checks decoded PCM duration and the original
presentation timeline, including timestamp gaps and frame durations. A successful
decode of a time-limited prefix is not accepted as complete evidence. Exceeding
sample, frame, timeline, metadata or subprocess resource limits rejects the media;
partial inspection never produces a verified duration. Container, codec, channels,
rate, MIME, byte size and governed duration must all pass. Stored bytes are
unchanged. Validator version and verified duration are persisted. Native FFmpeg is
an explicit alternative for provisioned operators;
configure `AUDIO_FFPROBE_EXECUTABLE`/`AUDIO_FFMPEG_EXECUTABLE`, retain the bounded
process settings, and verify the actual installed tools separately.

Actual audio start/finalize requires private S3 and bucket readiness, including in
development. Standards' existing explicit development-memory behavior is retained.
An unavailable validator or durable store fails closed; no evidence persistence
is claimed for memory storage.

## Commit boundaries and recovery

Lock order is attempt, recording, then response. Start persists a stable private
object key. Finalize reads through a bounded spool without trusting Content-Length,
inspects original media, and commits its checksum/size/duration receipt **before**
the nontransactional object PUT. Repeated finalization verifies the same stored
object rather than creating another authoritative asset. A READY asset is not an
attached response. Submission rejects unfinished recordings, unattached ready
assets, and missing required audio. Committed operation replays remain readable
after submission; new mutation does not.

If a response is lost, candidate recording metadata can identify RECORDING,
UPLOADING, READY or attached evidence. Within the governed upload deadline, retry
the same upload or use **Recover stored upload** to verify the stable object's
size/checksum and complete a previously failed database receipt. **Attach uploaded
audio** retries the exact server-confirmed response binding. An unavailable object
fails closed. **Cancel unfinished recording** releases the unfinished-operation
interlock without deleting objects; accepted recording attempts are still counted.
An expired/rejected receipt is terminal for further upload, and the server remains
authoritative even if a browser is disconnected.

After a reload, persisted unresolved upload state keeps navigation and submission
blocked even if recovery temporarily fails. Retry recovery and attach the
server-confirmed asset, or use the existing server-confirmed cancellation action;
a failed request alone does not clear the pending operation.

An unfinished receipt created by the earlier prefix-only validator cannot be
promoted to new READY evidence. Recovery and upload replay require the persisted
complete-input validation proof; otherwise the candidate must confirm cancellation
and make a new recording within the existing attempt policy. The old receipt,
private object and already committed READY history are preserved rather than
rewritten or deleted.
Older READY evidence also cannot receive a new attachment or a new submission
without complete-input proof on both the asset and recording receipt. A pending
attempt may replace its current response with a new fully validated immutable
revision under the existing recording policy. Historical reads and replays of
already committed bindings/submissions remain available without rewriting their
original provenance.

The candidate recording metadata endpoint is the non-destructive diagnostic path:
`GET /api/v1/candidate/attempts/{attempt_id}/items/{binding_id}/audio-evidence`.
It reports safe stage/error
categories and public evidence IDs, never the private key. Institution reviewers
inspect only bound evidence through their authorized organisation audio metadata
routes and `/institution/speaking-reviews/audio/{asset_id}`. The existing review
detail also exposes authorized playback. There are no new assessment controls.

## Playback and browser acceptance

Both players fetch authenticated binary audio and create revocable browser Blob
URLs. They buffer the complete authorized object, with explicit load/error states;
they do not claim native progressive Range playback. Tokens, private URLs and keys
are absent from audio URLs. Logout, organisation/account change, navigation and
unmount abort fetches, stop playback and revoke retained URLs. Capture/upload/
attachment interlocks prevent binding one item's session to another. **Audio saved
to this response** appears only after the exact server attachment acknowledgement.
Queued or failed saves are not presented as saved audio; disconnected raw capture
is held only in the current page, not promised as an offline durable upload.
Recording auto-stop uses the server's remaining allowance, conservatively adjusted
for request time, and monotonic browser elapsed time. A device's wall-clock offset
does not increase or immediately consume the recording window. Unknown timing
fails safely and requires refreshed server evidence.

The server's authenticated stream contract remains 200 full body, 206 explicit,
open-ended and suffix ranges, and 416 for malformed/multiple/unsatisfiable ranges.
206 calls the storage adapter's actual bounded Range GET rather than full GET
followed by slicing. The required combined integration test instruments actual S3
requests, checks anonymous denial and reopens persisted objects with a new adapter.

For an isolated browser database, export the real storage/media settings above and
the documented `DATABASE_URL` and `PRUFAI_BROWSER_TOOL_DIR`, then run
`bash scripts/run-integrated-browser.sh`. This migrates/seeds the dedicated database
and runs prior objective/Writing journeys plus the recorded-audio journey against
real Next.js, FastAPI, PostgreSQL and MinIO. The audio test uses explicitly labelled
Chromium automated microphone media, not a physical microphone or recognition.
`seed_demo` publishes two original AUDIO tasks with policy via real governance
services, but creates no audio assignment, capture, transcript, assessment or score.
The administrator assigns the exam through the product during acceptance.

The isolated browser runner additionally uses `scripts/browser_api.py` to test
real failed-receipt and recovery paths. This launcher requires explicit browser,
development, demo, local PostgreSQL and local S3 settings, and binds only to
loopback. A scoped test header can fail one database receipt after actual PUT, or
one storage-readiness operation during recovery. It cannot fabricate successful
responses. The product entry point remains `app.main:app` and does not import or
enable this instrumentation. Browser tests verify reload → failed recovery →
successful retry → authoritative attachment, cancellation, and controlled client
clock offsets against the unchanged backend clock.

## Retention and abandoned stages

Retention/deletion policy is **DEFERRED** for a separate governance decision.
Private objects, cancelled/abandoned receipts and historical assessment references
are retained. No object purge, destructive reconciliation, shared-object
deduplication or guessed retention period is introduced. Recovery is bounded to a
known recording's persisted identity and upload deadline. Expired or mismatched
objects require operator investigation using safe IDs and restricted database/
storage tools; diagnostics must not publish keys, credentials, audio or candidate
text. Historical evidence is never deleted by these development or CI scripts.
