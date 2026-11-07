# S5 editorial initial review authority

This is a code and operations contract for a private workspace that has no
active holder of `platform.role.assign`. A technical pass establishes that
the mechanism works. Live business authority requires independently verified
people, keys, and approvals. The prior proposed S5B-RP digest is planning
evidence. It does not authorize any operation here.

## Administrative inventory and scope

The ordinary platform role assignment service requires `platform.role.assign`.
The built-in Super Admin holds that permission. Standard source authors have
document management capabilities and cannot assign platform roles. Tenant
invitations confer tenant membership and tenant roles only. The repository's
synthetic demo seed is explicit test/demo tooling. The earlier S5 workspace
bootstrap was limited to establishing a clean editorial workspace and its
Standards Admin. None provides supported recovery of an issuer in a populated
editorial workspace.

The recovery command records one signed authorization for an existing
individual account. It adds no role, membership, new account, password,
or authority to make rights decisions. The beneficiary must sign in normally
and separately submit the exact approved request to the existing scoped grant
API. The payload pins the organization, reviewer, operation UUID, separately
approved business manifest digest, purpose, allowed actions, metadata field
projections, version/checksum pairs, and a review expiry no later than the
recovery expiry. Existing global platform authorization and access remain
unchanged. Recovery refuses to establish authority if an active platform
role already supplies `platform.role.assign`.

## Before any live change

1. Designate an accountable independent organizational approver and a separate
   technical executor. Verify each person's mandate and identity through an
   organizational channel independent of the request. Verify the issuer's and
   reviewer's real individual account ownership and source independence.
   Require four distinct people and account UUIDs. Record the approver's
   decision, revocation ownership, and evidence references in restricted
   storage. Any unresolved identity or authority leaves recovery blocked.
2. Verify the approver and executor Ed25519 public-key fingerprints directly
   with their owners over a separately authenticated channel. The approver
   and executor each control their own private signing key. A separate host
   custodian installs the verified public keys and identity bindings in
   `/etc/prufai/initial-review-authority.json`. This fixed path is shared by
   the API and the offline tool. The directory and regular file must be owned
   by root and writable only by root. The profile pins the named executor's
   non-root UID. The custodian must keep the directory under the trusted OS
   configuration tree and prevent substitution of the runtime or its imports.
   The tool never generates or enrolls a trusted key. Keep private keys,
   artifacts, identity dossiers and credentials outside Git.
3. Obtain separate approval for any live migration. Independently verify the
   target's dedicated loopback listener at port 55448, database
   `prufai_s5_editorial`, PostgreSQL system identifier, database OID, exact
   organization UUID, single current Alembic head, and editorial inventory.
   Confirm the database is a writable primary and distinct from the incident,
   production and test databases. Compare the target tuple with the signed
   artifact and trusted configuration. Give the service and executor the
   minimal access necessary to read `pg_catalog.pg_control_system()` and
   relevant catalog identity. Do not give them PostgreSQL superuser solely
   for this probe. Deploy the vetted code and migration only through the
   separately approved operator process.
4. Before any authorized live migration or apply, take a fresh encrypted
   backup using the workspace's established backup workflow. Verify the
   encrypted artifact and checksum and restore into a separately approved
   scratch database for integrity and inventory verification. Store the
   verified backup receipt with the approval dossier. Keep the restored
   database isolated. A backup restore can roll back one-time consumption,
   so the custodian must disable trust before any restoration and reconcile
   the operation against the external execution and revocation record.
5. The approver reviews the entire recovery manifest and separately reviews
   the exact reviewer onboarding manifest. The executor checks the signatures,
   approval references, database identity, backup receipt, and intended exact
   outcome. Both sign the recovery manifest independently. A checksum or a
   key introduced by the beneficiary during recovery provides no authorization.

## Private artifact contract

The trust file has `target`, `organisation_id`, `approver_id`,
`approver_public_key`, `executor_id`, `executor_public_key`, `executor_uid`
and optional `revoked_nonces`. Keys are lowercase hex encoded raw Ed25519
public keys. The target comprises `environment`, `database`, `port`,
`cluster_id` (decimal string) and `database_oid`. The live environment is
`S5_EDITORIAL`. `DISPOSABLE_TEST` accepts only the repository's guarded
uniquely named test child databases. The application refuses the recovery
path under production settings.

The envelope contains the complete `manifest`, `manifest_digest`,
`approver_signature` and `executor_signature`. The manifest includes version 1,
action, the exact target and organization, approver, executor and beneficiary
account UUIDs, private identity and approval evidence SHA-256 references,
revocation responsibility, purpose code, a unique UUID nonce and explicit
offset-aware start and expiry. Use `ESTABLISH` and
`INITIAL_SCOPED_REVIEW_AUTHORITY` with the complete exact `grant` payload
defined by the [scoped review contract](standards-foundation.md). Do not put
private dossier contents into the grant purpose or evidence references. The
maximum recovery window is seven days and the review grant must end within it.
The approver owns revocation.

Compute the manifest digest from UTF-8 JSON with recursively sorted object
keys, compact separators and no ASCII escaping. Sign the byte sequence
`ROLE + NUL + "PrufAI initial review authority v1" + NUL + raw_digest`,
where ROLE is ASCII `APPROVER` or `EXECUTOR` and raw_digest is the 32 decoded
digest bytes. Supply the detached 64-byte signatures as lowercase hex. Sign
with organizational tooling. Do not place private keys or real example
manifests in the repository.

## Dry run, apply and verify

Run the reviewed command interactively under the designated executor's local
non-root account. Store the envelope in an owner-only regular file and provide
the database URL through the hidden prompt. Confirm the exact digest from
the approved external dossier before running.

```sh
python -m app.scripts.recover_initial_review_authority \
  --envelope /private/path/to/approved-envelope.json \
  --database prufai_s5_editorial --confirm-sha256 APPROVED_DIGEST --dry-run
```

The dry run takes the same database locks, validates the same scope, verifies
the postcondition and rolls back the transaction. Record its result in the
external restricted journal. Repeat with `--apply` only under separate live
authorization. Keep the safe result and reconcile the immutable database audit
events for preparation, request, environment and approval verification,
establishment, consumption and postcondition. Rejected requests to a verified
target also have a safe receipt. For refusals before target identity is proven
or when the database is unavailable, retain the safe console result in the
external restricted journal. The command does not write audit records to an
unverified database.

Independently verify that the beneficiary's active platform roles did not
change. Sign in with the existing account and verify denial of global role
assignment, unrelated organizations, source lists, rights decisions, document
ingestion, publication, private candidate evidence and certification
administration. Only after the separate reviewer onboarding authorization may
the beneficiary submit the exact approved scoped grant. Verify the operation
receipt and access boundary before the independent reviewer personally
considers any source.

## Suspension, revocation and failures

The approver and executor sign a new `SUSPEND` or `REVOKE` envelope with
`END_SCOPED_REVIEW_AUTHORITY`, the original manifest digest, a different nonce,
the exact same target and beneficiary, a current window, and no grant payload.
Use the same dry-run and apply sequence. Either action ends the initial
authority permanently. If that authority issued the exact scoped review grant,
the transaction also revokes that grant. Replays of the exact envelope are
receipt checks. There is no restore or second bootstrap command. At expiry,
both windows are enforced by the API even for sessions already issued.

The host custodian can deny new issuance immediately by revoking the original
nonce or taking the trusted profile offline. To terminate a review grant
already issued, use the signed termination workflow while the trusted profile
is available or have an independently authorized existing administrator revoke
the grant through the ordinary path. If the trust profile is unavailable,
escalate to the independent custodian. The grant still expires within its
approved window. After any failure, inspect the immutable operation and
decision evidence before retrying the identical signed envelope. The
transaction either commits consumption and the authority together or rolls
back. Stop on ambiguous output, target drift or unexplained record changes.
Do not edit receipts, reset the singleton, restore an earlier snapshot to
retry, or assign a broad role as a workaround.

## Threat model

| Threat | Control and residual risk |
| --- | --- |
| Untrusted local user or stolen environment variables | Fixed OS-owned trust root, non-root executor UID, interactive prompt and two pinned signatures. There is no environment-variable grant, network bootstrap or password seed. |
| Compromised executor or leaked envelope | The actor still needs separately signed exact scope, the intended executor UID and database credentials. An attacker who controls the authorized executor environment and obtains both signatures could consume that exact approval. Revoke the nonce and handle it as an incident. |
| Forged business approval or reviewer identity | Distinct pinned keys sign the full immutable manifest. The actual mandate, account ownership and independence must be established outside the app. Two compromised signers or a compromised root custodian defeat this control. |
| Wrong database or production | Approved target tuple includes the verified PostgreSQL cluster and database identity, loopback listener and organization. Runtime and CLI refuse production settings. Keep DBA identity and database profiles out of untrusted control. |
| Replays, competing recovery or expiry race | PostgreSQL advisory lock and immutable singleton serialize recovery. The existing exact operation receipt prevents repeat grant creation. The runtime locks the recovery record, checks the latest trust policy and rechecks expiry before issuance. |
| Existing API escalation or unrelated role assignment | No platform permission is attached. Only the existing scoped grant creation endpoint checks the exact signed authorization. All other routes retain their normal authorization checks. |
| Revocation or audit deletion | The database seals the recovery receipt and audit rows. Signed termination and issuance serialize on the same row. Database owners and host root can alter the storage and code, so restrict those credentials and keep an independent execution record. A stolen database owner credential remains a database incident and cannot be contained by an application-level authorization check. |
