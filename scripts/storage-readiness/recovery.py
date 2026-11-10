"""Synthetic PostgreSQL and current-object backup, restore and reconciliation."""

import hashlib
import json
import stat
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from app.authorization.context import TenantContext
from app.authorization.errors import AuthorizationError
from app.knowledge.storage import StorageError
from app.models import KnowledgeDocumentVersion, User
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
)
from app.services.knowledge import KnowledgeRegistryService
from botocore.exceptions import ClientError
from network import rejected, request
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

CONTENTS = (
    b"original synthetic recovery evidence one\n",
    b"synthetic recovery evidence two\n",
)
PERMISSIONS = frozenset(
    (
        "standards.authority.manage",
        "standards.source.manage",
        "standards.document.manage",
        "standards.document.read",
    )
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(data):
    return (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()


def engine_for(database):
    url = urlsplit(database.url)
    assert url.hostname == "127.0.0.1" and url.path == f"/{database.name}"
    assert database.name.startswith("o1b2_")
    engine = create_engine(database.url, hide_parameters=True)
    with engine.connect() as connection:
        assert connection.scalar(text("select current_database()")) == database.name
    return engine


def seed(database, storage):
    engine = engine_for(database)
    store = storage.adapter(prefix="standards/*")
    registry = KnowledgeRegistryService(storage=store)
    with Session(engine) as db:
        creator = User(
            email="o1b2-creator@example.invalid",
            normalized_email="o1b2-creator@example.invalid",
        )
        reviewer = User(
            email="o1b2-reviewer@example.invalid",
            normalized_email="o1b2-reviewer@example.invalid",
        )
        db.add_all((creator, reviewer))
        db.flush()
        ctx = TenantContext(creator.id, None, None, platform_permissions=PERMISSIONS)
        authority = registry.create_authority(
            db,
            ctx,
            code="O1B2_SYNTHETIC",
            name="Synthetic test only",
            authority_type=KnowledgeAuthorityType.INTERNAL,
        )
        source = registry.create_source(
            db,
            ctx,
            authority_id=authority.id,
            source_code="O1B2_SYNTHETIC",
            title="Synthetic evidence",
            document_type=KnowledgeDocumentType.STANDARD,
            assessment_family=AssessmentFamily.LANGUAGE,
        )
        records = []
        for index, content in enumerate(CONTENTS, 1):
            row = registry.create_version(
                db,
                ctx,
                source_id=source.id,
                version_label=f"synthetic-{index}",
                content_mode="ORIGINAL_SYNTHETIC",
            )
            registry.review_rights(
                db,
                TenantContext(
                    reviewer.id,
                    None,
                    None,
                    platform_permissions=frozenset({"standards.document.review"}),
                ),
                version_id=row.id,
                expected_metadata_checksum=row.metadata_checksum,
                decision="ALLOW_SYNTHETIC_INGESTION",
                basis_reference="synthetic:o1b2",
                rights_holder="Synthetic test only",
                licence_type="TEST ONLY",
                retention_policy="Disposable O1B.2 rehearsal",
                expected_sha256=sha(content),
            )
            db.commit()
            row = registry.register_file(
                db,
                ctx,
                version_id=row.id,
                filename=f"synthetic-{index}.txt",
                content=content,
                declared_mime_type="text/plain",
            )
            registry.verify_file(db, ctx, version_id=row.id)
            record = {
                "version": str(row.id),
                "source": str(row.source_id),
                "key": row.storage_key,
                "sha256": row.sha256,
                "size": row.file_size,
                "type": row.mime_type,
                "owner": "platform-shared",
                "classification": "ORIGINAL_SYNTHETIC",
            }
            operator = storage.operator()
            operator.copy_object(
                Bucket=storage.bucket,
                Key=record["key"],
                CopySource={"Bucket": storage.bucket, "Key": record["key"]},
                ContentType=record["type"],
                MetadataDirective="REPLACE",
                Metadata=object_metadata(record),
            )
            record["object_version"] = operator.head_object(
                Bucket=storage.bucket, Key=record["key"]
            )["VersionId"]
            records.append(record)
        return records, creator.id


def object_metadata(record):
    return {
        key: record[key] for key in ("owner", "classification", "source", "version")
    }


def inventory(storage):
    page = storage.operator().list_objects_v2(Bucket=storage.bucket)
    assert not page.get("IsTruncated"), "synthetic inventory exceeded its bound"
    return {obj["Key"] for obj in page.get("Contents", [])}


def compare(storage, database, records, creator_id):
    """Reconcile restored rows against objects and pinned reference metadata."""
    findings = {
        key: []
        for key in ("missing", "orphaned", "checksum", "provenance", "authorization")
    }
    expected = {r["version"]: r for r in records}
    adapter = storage.adapter(prefix="standards/*", write=False)
    registry = KnowledgeRegistryService(storage=adapter)
    ctx = TenantContext(creator_id, None, None, platform_permissions=PERMISSIONS)
    engine = engine_for(database)
    with Session(engine) as db:
        rows = list(db.scalars(select(KnowledgeDocumentVersion)))
        if {str(r.id) for r in rows} != set(expected):
            findings["provenance"].append("database-version-set")
        keys = inventory(storage)
        refs = {r.storage_key for r in rows}
        findings["missing"] = sorted(refs - keys)
        findings["orphaned"] = sorted(keys - refs)
        for row in rows:
            record = expected.get(str(row.id))
            if record is None or (
                row.storage_bucket,
                row.storage_key,
                str(row.source_id),
                row.content_mode,
                row.sha256,
                row.file_size,
                row.mime_type,
            ) != (
                storage.bucket,
                record["key"],
                record["source"],
                record["classification"],
                record["sha256"],
                record["size"],
                record["type"],
            ):
                findings["provenance"].append(str(row.id))
                continue
            if row.storage_key not in keys:
                continue
            metadata = storage.operator().head_object(
                Bucket=storage.bucket, Key=row.storage_key
            )
            # HTTP header names are case insensitive. MinIO can return title case.
            raw_metadata = metadata["Metadata"]
            normalized = {key.lower(): value for key, value in raw_metadata.items()}
            if (
                metadata["ContentType"] != record["type"]
                or len(normalized) != len(raw_metadata)
                or normalized != object_metadata(record)
            ):
                findings["provenance"].append(str(row.id))
            content = adapter.get(row.storage_key)
            if sha(content) != record["sha256"] or len(content) != record["size"]:
                findings["checksum"].append(str(row.id))
                continue
            registry.verify_file(db, ctx, version_id=row.id)
            try:
                registry.verify_file(
                    db, TenantContext(creator_id, uuid.uuid4(), None), version_id=row.id
                )
            except AuthorizationError:
                pass
            else:
                findings["authorization"].append(str(row.id))
            if (
                request(storage.port(), "GET", f"/{storage.bucket}/{row.storage_key}")[
                    0
                ]
                != 403
            ):
                findings["authorization"].append(str(row.id))
    engine.dispose()
    return {key: len(items) for key, items in findings.items()}


def assert_private(storage):
    try:
        storage.operator().get_bucket_policy(Bucket=storage.bucket)
    except ClientError as exc:
        assert exc.response["Error"]["Code"] == "NoSuchBucketPolicy"
    else:
        raise AssertionError("unexpected bucket access policy")


def make_backup(path, storage, database, records):
    assert_private(storage)
    assert inventory(storage) == {r["key"] for r in records}
    assert (
        storage.operator()
        .list_multipart_uploads(Bucket=storage.bucket)
        .get("Uploads", [])
        == []
    )
    for index, record in enumerate(records):
        payload = storage.adapter(prefix="standards/*", write=False).get(record["key"])
        assert len(payload) == record["size"] and sha(payload) == record["sha256"]
        (path / f"object-{index}").write_bytes(payload)
    dump = database.dump()
    assert dump.startswith(b"PGDMP")
    (path / "database.dump").write_bytes(dump)
    manifest = encode(
        {"database_sha256": sha(dump), "records": records, "access": "private"}
    )
    (path / "manifest.json").write_bytes(manifest)
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in path.iterdir())
    return sha(manifest)


def validate_backup(path, manifest_sha, records):
    expected_files = {
        "database.dump",
        "manifest.json",
        *(f"object-{i}" for i in range(len(records))),
    }
    if {p.name for p in path.iterdir()} != expected_files:
        raise ValueError("BACKUP_INCOMPLETE")
    manifest = (path / "manifest.json").read_bytes()
    if sha(manifest) != manifest_sha:
        raise ValueError("BACKUP_MANIFEST_MISMATCH")
    decoded = json.loads(manifest)
    if decoded["records"] != records or decoded["access"] != "private":
        raise ValueError("BACKUP_PROVENANCE_MISMATCH")
    dump = (path / "database.dump").read_bytes()
    if sha(dump) != decoded["database_sha256"]:
        raise ValueError("BACKUP_DATABASE_CHECKSUM_MISMATCH")
    payloads = [(path / f"object-{i}").read_bytes() for i in range(len(records))]
    if any(
        sha(payload) != row["sha256"] or len(payload) != row["size"]
        for payload, row in zip(payloads, records, strict=True)
    ):
        raise ValueError("BACKUP_OBJECT_CHECKSUM_MISMATCH")
    return dump, payloads


def expected_backup_failure(path, manifest_sha, records, case):
    try:
        validate_backup(path, manifest_sha, records)
    except ValueError as exc:
        assert str(exc) == case
    else:
        raise AssertionError(f"{case} went undetected")


def restore(path, manifest_sha, records, storage, database):
    dump, payloads = validate_backup(path, manifest_sha, records)
    assert inventory(storage) == set()
    database.restore(dump)
    assert_private(storage)
    mapping = []
    for record, payload in zip(records, payloads, strict=True):
        restored = storage.operator().put_object(
            Bucket=storage.bucket,
            Key=record["key"],
            Body=payload,
            ContentType=record["type"],
            Metadata=object_metadata(record),
        )
        mapping.append(
            {
                "reference": record["version"],
                "from": record["object_version"],
                "to": restored["VersionId"],
            }
        )
    return mapping


def rehearse(rig, source, source_db, target, target_db, records, creator_id):
    assert source.volume != target.volume and source.network != target.network
    assert (
        source_db.container != target_db.container and source_db.name != target_db.name
    )
    original = compare(source, source_db, records, creator_id)
    assert not any(original.values()), original
    rig.evidence["recovery"] = report = {
        "source_reconciliation": original,
        "objects": [
            {
                "reference": row["version"],
                "key_sha256": sha(row["key"].encode()),
                "sha256": row["sha256"],
                "size": row["size"],
                "type": row["type"],
                "owner": row["owner"],
                "classification": row["classification"],
            }
            for row in records
        ],
    }
    with tempfile.TemporaryDirectory(prefix="o1b2-private-backup-") as folder:
        path = Path(folder)
        manifest_sha = make_backup(path, source, source_db, records)
        dump, payloads = validate_backup(path, manifest_sha, records)
        (path / "object-0").unlink()
        expected_backup_failure(path, manifest_sha, records, "BACKUP_INCOMPLETE")
        (path / "object-0").write_bytes(b"synthetic corrupt backup")
        expected_backup_failure(
            path, manifest_sha, records, "BACKUP_OBJECT_CHECKSUM_MISMATCH"
        )
        (path / "object-0").write_bytes(payloads[0])
        validate_backup(path, manifest_sha, records)

        unavailable = target.adapter(prefix="standards/*")
        rig.docker("stop", target.container)
        try:
            unavailable.put(records[0]["key"], payloads[0], records[0]["type"])
        except StorageError:
            pass
        else:
            raise AssertionError("unavailable restore destination accepted a write")
        rig.docker("start", target.container)
        target.wait()
        rejected(
            lambda: target.invalid_client().put_object(
                Bucket=target.bucket, Key=records[0]["key"], Body=payloads[0]
            ),
            status=403,
        )
        assert inventory(target) == set()
        mapping = restore(path, manifest_sha, records, target, target_db)

    clean = compare(target, target_db, records, creator_id)
    assert not any(clean.values()), clean
    first = records[0]
    operator = target.operator()
    # Each corruption is restricted to the newly restored synthetic volume or DB.
    operator.delete_object(Bucket=target.bucket, Key=first["key"])
    assert compare(target, target_db, records, creator_id)["missing"] == 1
    operator.put_object(
        Bucket=target.bucket,
        Key=first["key"],
        Body=b"x" * first["size"],
        ContentType=first["type"],
        Metadata=object_metadata(first),
    )
    assert compare(target, target_db, records, creator_id)["checksum"] == 1
    operator.put_object(
        Bucket=target.bucket,
        Key=first["key"],
        Body=payloads[0],
        ContentType="application/octet-stream",
        Metadata=object_metadata(first),
    )
    assert compare(target, target_db, records, creator_id)["provenance"] == 1
    repaired = operator.put_object(
        Bucket=target.bucket,
        Key=first["key"],
        Body=payloads[0],
        ContentType=first["type"],
        Metadata=object_metadata(first),
    )
    # The actual database trigger must prohibit rewriting a restored reference.
    engine = engine_for(target_db)
    with Session(engine) as db:
        row = db.get(KnowledgeDocumentVersion, uuid.UUID(first["version"]))
        assert row is not None
        row.storage_key = first["key"] + "-wrong-reference"
        try:
            db.flush()
        except DBAPIError as exc:
            assert "SOURCE_OBJECT_IDENTITY_IMMUTABLE" in str(exc.orig)
            db.rollback()
        else:
            raise AssertionError("restored database allowed a reference rewrite")
    engine.dispose()
    # Reconcile a correctly restored database against an unrelated object mapping.
    # This exercises wrong-backup pairing without bypassing the immutable trigger.
    mismatched_records = [
        {**first, "key": first["key"] + "-wrong-reference"},
        *records[1:],
    ]
    assert compare(target, target_db, mismatched_records, creator_id)["provenance"] == 1
    operator.put_object(Bucket=target.bucket, Key="synthetic-orphan", Body=b"synthetic")
    assert compare(target, target_db, records, creator_id)["orphaned"] == 1
    operator.delete_object(Bucket=target.bucket, Key="synthetic-orphan")
    final = compare(target, target_db, records, creator_id)
    assert final == clean
    report.update(
        {
            "object_count": len(records),
            "backup_manifest_sha256": manifest_sha,
            "database_dump_sha256": sha(dump),
            "backup_access": "0700 directory; 0600 files; plaintext synthetic fixture",
            "database_method": "pg_dump custom archive and pg_restore into a second empty PostgreSQL instance",
            "restored_versions": mapping,
            "first_object_version_after_failure_recovery": repaired["VersionId"],
            "restore_scope": "current objects, exact app version references; historical S3 versions and multipart sessions excluded",
            "reconciliation": final,
            "negative_cases": [
                "incomplete archive",
                "corrupted archive object",
                "unavailable destination",
                "wrong credential",
                "missing restored object",
                "restored checksum mismatch",
                "wrong content type",
                "wrong archive/database reference association",
                "database reference rewrite rejected by immutable trigger",
                "orphaned synthetic object",
            ],
        }
    )
