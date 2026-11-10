# O1B.3 private S3 compatibility and operator handoff

The existing S3 adapter sends ACL-free uploads for both guarded MinIO and a separately approved AWS trial. The SDK contract test intercepts signed HTTPS requests at the transport boundary. It checks the bucket and object identity, SigV4 region and session token, content type, CRC32 payload integrity and absent ACL/grant headers on PutObject and multipart operations. The negative transport fixture reproduces the documented AWS rejection of private/public canned ACLs and grants. Application evidence identity still uses SHA-256. AWS operational proof requires a separate authorized trial. [AWS Object Ownership](https://docs.aws.amazon.com/AmazonS3/latest/userguide/about-object-ownership.html) · [AWS error responses](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-ownership-error-responses.html)

## Operation and evidence inventory

| Caller or workflow | Operations and enforcement | Regression evidence |
| --- | --- | --- |
| API storage factory and adapter in `services/api/app/knowledge/storage.py` | PutObject, GetObject, HeadObject and bounded/range GetObject. Metadata-only HeadObject distinguishes absent, denied, mismatched and unavailable evidence. Normal reads retain the SDK's retry configuration. Privacy metadata probes use bounded timeouts and one attempt. The adapter supplies no ACL or grant. | `test_managed_s3_contract.py`, `test_object_storage_integration.py`, `test_transcription_bounded_storage.py` |
| Recording and playback in `speaking_audio/service.py`, `speaking_audio/api.py` and `runtime/api.py` | PutObject, HeadObject and authenticated GetObject or HTTP Range under `candidate-audio/<org>/<attempt>/<recording>`. Server-generated keys and checksums bind audio to the exact response and item. Candidate and reviewer authorization precede reads. | `test_recorded_audio_postgresql_minio_integration.py`, browser recording/playback journeys |
| Standards registry in `services/knowledge.py` and `knowledge/api.py` | Private upload and bounded integrity read. `standards/<source>/<version>/<sha256>`, MIME, length and SHA-256 are pinned to the source version. Access and source rights are checked by the registry. | `test_authoritative_standards_postgresql_minio_integration.py`, Standards and scoped-review browser journeys |
| Transcription, Speaking execution and evaluation in `speech_transcription/`, `speaking_execution/` and `evaluation/expert_evidence.py` | Workers read pinned originals with size limits and digest checks before conversion or provider handoff. Stale references, unavailable originals and wrong evidence identities fail closed. | Required transcription, Speaking execution and evaluation replay PostgreSQL/MinIO suites |
| Writing and released results | Writing works from persisted response, rubric and evidence IDs. Scoring and release preserve persisted assessment/evidence references. They do not create S3 ACLs or public object URLs. | Required Writing and result release suites and authenticated browser journeys |
| Operations and synthetic proof | Multipart initiate/part/complete, presigned GET and DeleteObject are explicit SDK calls in the disposable guard and storage readiness scripts. Bucket creation, policies, versioning, administrative copy and user metadata are operator/test concerns. The application adapter has no ACL setter, presigning, object deletion, bucket policy or multipart entry point. | `scripts/check-minio-ingress.py`, `scripts/storage-readiness/`, and offline multipart serialization test |

The pre-change ACL writers were the adapter's PutObject and the disposable restore's PutObject. Both now omit the canned ACL. The guarded rehearsal exercises anonymous read/write rejection, wrong-prefix denial, denied deletion by the application identity, expired presigned reads, multipart, bounded Range, retries, restart, and PostgreSQL/object reconciliation. Its original guard bypass and unsafe write probes remain required. Production access depends on approved backend IAM, the bucket policy, Block Public Access and PrüfAI's application authorization.

## Example Frankfurt trial configuration

Use a dedicated nonproduction AWS account and pre-existing approved private test bucket. This is a configuration reference for an authorized operator. Keep credentials in an approved short-lived secret source and give only the isolated synthetic process access to them.

```dotenv
ENVIRONMENT=test
OBJECT_STORAGE_BACKEND=aws-s3-trial
OBJECT_STORAGE_ENDPOINT=https://s3.eu-central-1.amazonaws.com
OBJECT_STORAGE_REGION=eu-central-1
OBJECT_STORAGE_SECURE=true
OBJECT_STORAGE_BUCKET=<approved-private-synthetic-bucket>
OBJECT_STORAGE_AWS_TRIAL_APPROVED=false
```

A real trial requires recorded approval of account, bucket, role, region, synthetic data scope, budget, processing terms and cleanup. Only after that approval and the preflight below should an operator enable the trial flag and inject `OBJECT_STORAGE_ACCESS_KEY`, `OBJECT_STORAGE_SECRET_KEY` and `OBJECT_STORAGE_SESSION_TOKEN`. The flag is a local interlock. It does not attest to an organization's approval, effective IAM, or actual bucket configuration. The factory requires test mode, the exact Frankfurt HTTPS endpoint, an explicit DNS-compatible bucket and temporary credentials. Guarded MinIO continues to use the server-controlled `s3` backend setting. Candidate requests have no provider selection field. [AWS S3 regional endpoints](https://docs.aws.amazon.com/general/latest/gr/s3.html)

Use an approved inspector role to confirm the STS account/role, bucket owner and region, Bucket owner enforced, all four Block Public Access settings, effective bucket policy, versioning, default encryption, retention and CloudTrail data events. Check identity and session policy plus any bucket policy, permissions boundary, organization policy and VPC endpoint policy before sending objects. A redirect, wrong region or mismatched bucket identity is a stop condition. Restrict outbound traffic to the approved regional endpoint. [Block Public Access](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html) · [Required S3 permissions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html)

Example identity policy for an adapter-only trial role. Substitute the approved bucket and exact disposable run prefix before independent review. A synthetic application journey uses different server-generated namespaces. For that journey, scope the role to the exact synthetic organization and Standards source identifiers instead.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::APPROVED_BUCKET/o1b3/APPROVED_RUN_ID/*",
      "Condition": {"StringEquals": {"aws:RequestedRegion": "eu-central-1"}}
    }
  ]
}
```

Scope the bucket policy to deny non-TLS traffic to bucket and object resources using `aws:SecureTransport`. Allow only the reviewed account and roles. Keep bucket administration, configuration inspection and any version-aware cleanup separate from application permissions. Give cleanup authority only for the approved prefix and tracked keys/versions. Review any needed list permission separately and limit it with an exact prefix. Use a short-lived GET presign only when expressly included in the trial. Never put its URL, object keys, candidate/source bytes or credentials into application logs or CI. Keep restricted CloudTrail data events with an approved log retention period. Record redacted operation status, safe request IDs, checksums, SDK versions and configuration fingerprints in trial evidence. Revoke the session and remove the role grant on completion. [Presigned URL lifetime](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html) · [CloudTrail for S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-cloudtrail-logging-for-s3.html)

## Encryption, privacy and recovery decisions

Require privacy and security approval for the region, storage and logging accounts, access from outside the region, processing agreements, service and support access, retention and deletion procedures. Use bucket default encryption and verify its effect on a synthetic write. SSE-S3 supplies baseline encryption. If policy requires customer-managed keys, use a scoped regional KMS key with reviewed data-key/decrypt grants, rotation and independent key recovery. The adapter does not send an encryption override. Avoid a bucket policy that rejects headerless writes when the approved design relies on default encryption. Plan versioning, backup isolation, restore drills and preservation holds before any data transfer. [S3 encryption controls](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingEncryption.html)

## Migration and rollback plan

1. Inventory MinIO namespaces, versions, delete markers, current and historical database references, byte lengths, SHA-256, MIME types, metadata and holds. Review existing volume and key custody. Save a coordinated, verified PostgreSQL backup and object manifest with encryption and separate recovery custody.
2. Rehearse with synthetic data and independent source/target storage and PostgreSQL. Use a source read identity and destination write identity scoped to the approved inventory. Preserve logical keys and metadata, and map storage-specific version IDs. Classify object history or deletion markers that cannot be reproduced. Re-read target bytes and compare SHA-256 and size against the manifest and database. ETags alone cannot establish payload identity.
3. Record one receipt per exact source version and target version. Retry only missing or explicitly rejected entries. Quarantine mismatches and keep the source intact. Reconcile missing, orphaned, checksum, provenance and authorization mismatches to zero before a cutover decision.
4. Schedule a write freeze for recording, ingestion and workers. Take a final reference/object inventory and copy the delta. Keep the original guarded storage read-only and verify live-equivalent private playback, Range, worker reads, rights, audit and denied access on approved synthetic checks. Move reads and then writes only with a named operator and a reviewed rollback threshold. Avoid uncontrolled dual writes.
5. If health, integrity, permissions or privacy checks fail, freeze target writes and restore service from the verified source configuration and coordinated recovery point. Reconcile writes accepted by the target before reopening intake. Keep backups and version mappings through the approved rollback window. Review privacy holds, retention and deletion receipts before any source retirement.

The [O1B.2 recovery rehearsal](../storage-readiness-o1b2/README.md) covers current synthetic objects and exact database references. Existing production volume compatibility, encrypted operational recovery, actual AWS and production IAM remain unverified. Both MinIO write-path advisories remain `PRESENT_CONTAINED` within the tested guarded topology. Keep the full [O1A inventory](../supply-chain-o1a/README.md) and [O1B.1 evidence](../storage-decoder-o1b1/README.md). Pilot security readiness remains **NO**.
