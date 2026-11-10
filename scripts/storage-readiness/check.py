"""Run O1B.2 against named, labelled, disposable Docker resources."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import boto3
from app.knowledge.storage import S3CompatibleObjectStorage
from botocore.config import Config
from network import (
    addresses,
    faults,
    peer,
    rejected,
    request,
    topology,
    unsafe_requests,
)
from recovery import assert_private, inventory, rehearse, seed

ROOT = Path(__file__).resolve().parents[2]
POSTGRES = "pgvector/pgvector:pg16@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f"
PEER_BASE = "python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39"
LABEL = "org.prufai.o1b2.disposable"
CONFIG = Config(
    connect_timeout=1,
    read_timeout=2,
    retries={"total_max_attempts": 1},
    s3={"addressing_style": "path"},
)


def run(*argv, data=None, env=None, cwd=ROOT, timeout=90):
    result = subprocess.run(
        argv,
        check=False,
        input=data,
        env=env,
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
    )
    if result.returncode:
        print(result.stderr.decode(errors="replace")[-2000:], file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, argv)
    return result.stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Rig:
    def __init__(self, stack, image):
        self.stack = stack
        self.mark = "o1b2-" + secrets.token_hex(6)
        self.storage_image = image
        self.evidence = {
            "status": "FAIL",
            "scope": "disposable Docker rehearsal",
            "run_id": self.mark,
        }

    def docker(self, *args, **kwargs):
        return run("docker", *args, **kwargs)

    def inspect(self, kind, name):
        return json.loads(self.docker(kind, "inspect", name))[0]

    def owned(self, kind, name):
        obj = self.inspect(kind, name)
        labels = obj["Config"]["Labels"] if kind == "container" else obj["Labels"]
        assert labels[LABEL] == self.mark
        return obj

    def remove(self, kind, name, *flags):
        probe = subprocess.run(
            ["docker", kind, "inspect", name],
            check=False,
            capture_output=True,
            timeout=15,
        )
        if probe.returncode:
            error = probe.stderr.decode(errors="replace")
            if not any(
                marker in error
                for marker in (f"No such {kind}: {name}", f"{kind} {name} not found")
            ):
                probe.check_returncode()
            return
        self.owned(kind, name)
        self.docker(kind, "rm", *flags, name)

    def create_network(self, name, *, register_cleanup=True):
        self.docker(
            "network", "create", "--ipv6", "--label", f"{LABEL}={self.mark}", name
        )
        info = self.owned("network", name)
        assert info["EnableIPv6"] and info["Driver"] == "bridge"
        if register_cleanup:
            self.stack.callback(self.remove, "network", name)

    def create_volume(self, name):
        self.docker("volume", "create", "--label", f"{LABEL}={self.mark}", name)
        self.owned("volume", name)
        self.stack.callback(self.remove, "volume", name)

    def launch(self, name, *args, env=None):
        self.docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"{LABEL}={self.mark}",
            *args,
            env=env,
        )
        self.owned("container", name)
        self.stack.callback(self.remove, "container", name, "-f")

    def build_peer(self):
        self.peer_image = self.mark + "-probe"
        # Use the same SDK closure as the current API environment.
        names = (
            "boto3",
            "botocore",
            "jmespath",
            "s3transfer",
            "python-dateutil",
            "six",
            "urllib3",
        )
        packages = " ".join(f"{n}=={importlib.metadata.version(n)}" for n in names)
        recipe = (
            f"FROM {PEER_BASE}\n"
            "WORKDIR /opt/probe\nCOPY app/knowledge/storage.py storage.py\n"
            f"RUN python -m pip install --disable-pip-version-check --no-cache-dir {packages}\n"
        )
        self.docker(
            "build",
            "-q",
            "-f",
            "-",
            "-t",
            self.peer_image,
            "services/api",
            data=recipe.encode(),
            timeout=300,
        )
        self.stack.callback(self.docker, "image", "rm", self.peer_image)
        self.evidence["test_peer_image"] = self.inspect("image", self.peer_image)["Id"]


class Storage:
    bucket = "o1b2-synthetic"

    def __init__(self, rig, role):
        self.rig = rig
        self.network = rig.mark + f"-{role}-bridge"
        self.container = rig.mark + f"-{role}-s3"
        self.volume = rig.mark + f"-{role}-objects"
        rig.create_network(self.network)
        rig.create_volume(self.volume)
        self.key = "synthetic-operator"
        self.secret = secrets.token_hex(20)
        self.sessions = {}
        env = {
            **os.environ,
            "MINIO_ROOT_USER": self.key,
            "MINIO_ROOT_PASSWORD": self.secret,
        }
        rig.launch(
            self.container,
            "--network",
            self.network,
            "--network-alias",
            "guarded-storage",
            "--publish",
            "127.0.0.1::9000",
            "--publish",
            "[::1]::9000",
            "--publish",
            "127.0.0.1::9001",
            "--publish",
            "[::1]::9001",
            "--env",
            "MINIO_ROOT_USER",
            "--env",
            "MINIO_ROOT_PASSWORD",
            "--mount",
            f"type=volume,source={self.volume},target=/data",
            rig.storage_image,
            env=env,
        )
        mount = rig.owned("container", self.container)["Mounts"]
        assert (
            len(mount) == 1
            and mount[0]["Name"] == self.volume
            and mount[0]["Destination"] == "/data"
        )
        self.wait()
        operator = self.operator()
        operator.create_bucket(Bucket=self.bucket)
        operator.put_bucket_versioning(
            Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        assert_private(self)

    def port(self, target=9000, address="127.0.0.1"):
        bindings = self.rig.owned("container", self.container)["NetworkSettings"][
            "Ports"
        ][f"{target}/tcp"]
        return next(int(b["HostPort"]) for b in bindings if b["HostIp"] == address)

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.port()}"

    def wait(self):
        for _ in range(30):
            try:
                if request(self.port(), "GET", "/minio/health/ready")[0] == 200:
                    return
            except (OSError, HTTPException, KeyError, StopIteration, TypeError):
                pass
            time.sleep(1)
        raise AssertionError("disposable guarded storage did not become ready")

    def client(self, service, key, secret, token=None, endpoint=None, *, retry=False):
        config = (
            CONFIG.merge(Config(signature_version="s3v4"))
            if service == "s3"
            else CONFIG
        )
        if retry:
            config = config.merge(
                Config(retries={"mode": "standard", "total_max_attempts": 2})
            )
        return boto3.client(
            service,
            endpoint_url=endpoint or self.endpoint,
            region_name="us-east-1",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            aws_session_token=token,
            config=config,
        )

    def operator(self):
        return self.client("s3", self.key, self.secret)

    def invalid_client(self):
        return self.client("s3", "invalid-synthetic", "invalid-secret")

    def scoped(self, prefix, *, write=True):
        identity = (prefix, write)
        if identity not in self.sessions:
            actions = ["s3:GetObject"]
            if write:
                actions.append("s3:PutObject")
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": actions,
                        "Resource": f"arn:aws:s3:::{self.bucket}/{prefix}",
                    }
                ],
            }
            self.sessions[identity] = self.client(
                "sts", self.key, self.secret
            ).assume_role(
                RoleArn="arn:aws:iam::123456789012:role/synthetic-storage",
                RoleSessionName="disposable-o1b2",
                Policy=json.dumps(policy),
                DurationSeconds=900,
            )["Credentials"]
        credentials = self.sessions[identity]
        return (
            credentials["AccessKeyId"],
            credentials["SecretAccessKey"],
            credentials["SessionToken"],
        )

    def adapter(self, prefix, *, write=True, retry=False):
        key, secret, token = self.scoped(prefix, write=write)
        return S3CompatibleObjectStorage(
            endpoint=self.endpoint,
            bucket=self.bucket,
            access_key=key,
            secret_key=secret,
            secure=False,
            client=self.client("s3", key, secret, token, retry=retry),
        )


class Database:
    def __init__(self, rig, role):
        self.rig = rig
        self.name = f"o1b2_{role}_{rig.mark.removeprefix('o1b2-')}"
        self.container = rig.mark + f"-{role}-pg"
        self.volume = rig.mark + f"-{role}-rows"
        password = secrets.token_hex(20)
        rig.create_volume(self.volume)
        rig.launch(
            self.container,
            "--publish",
            "127.0.0.1::5432",
            "--env",
            f"POSTGRES_DB={self.name}",
            "--env",
            "POSTGRES_USER=synthetic_operator",
            "--env",
            "POSTGRES_PASSWORD",
            "--mount",
            f"type=volume,source={self.volume},target=/var/lib/postgresql/data",
            POSTGRES,
            env={**os.environ, "POSTGRES_PASSWORD": password},
        )
        obj = rig.owned("container", self.container)
        assert obj["Mounts"][0]["Name"] == self.volume
        bindings = obj["NetworkSettings"]["Ports"]["5432/tcp"]
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        self.url = f"postgresql+psycopg://synthetic_operator:{password}@127.0.0.1:{bindings[0]['HostPort']}/{self.name}"
        for _ in range(30):
            try:
                self.command("pg_isready", "-q", "-h", "127.0.0.1")
                assert (
                    self.sql("select current_database()").decode().strip() == self.name
                )
                break
            except subprocess.CalledProcessError:
                time.sleep(1)
        else:
            raise AssertionError("disposable PostgreSQL did not become ready")

    def command(self, name, *args, data=None):
        self.rig.owned("container", self.container)
        return self.rig.docker(
            "exec",
            "-i",
            self.container,
            name,
            "-U",
            "synthetic_operator",
            "-d",
            self.name,
            *args,
            data=data,
        )

    def sql(self, statement):
        return self.command("psql", "-At", "-v", "ON_ERROR_STOP=1", "-c", statement)

    def migrate(self):
        assert self.sql("select current_database()").decode().strip() == self.name
        run(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            cwd=ROOT / "services/api",
            env={**os.environ, "DATABASE_URL": self.url},
            timeout=120,
        )
        return self.sql("select version_num from alembic_version").decode().strip()

    def dump(self):
        assert self.sql("select current_database()").decode().strip() == self.name
        return self.command("pg_dump", "-Fc", "--no-owner", "--no-privileges")

    def restore(self, dump):
        assert self.sql("select current_database()").decode().strip() == self.name
        assert self.sql("select to_regclass('public.alembic_version')").strip() == b""
        self.command(
            "pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", data=dump
        )


def signed_contract(rig, storage, record):
    adapter = storage.adapter("standards/*")
    operator = storage.operator()
    data = adapter.get(record["key"])
    assert sha(data) == record["sha256"]
    assert adapter.size(record["key"]) == record["size"]
    assert adapter.read_range(record["key"], 1, 4) == data[1:5]
    assert (
        operator.head_object(Bucket=storage.bucket, Key=record["key"])["ContentType"]
        == record["type"]
    )
    assert_private(storage)
    assert (
        request(storage.port(), "GET", f"/{storage.bucket}/{record['key']}")[0] == 403
    )
    assert request(storage.port(), "PUT", f"/{storage.bucket}/anonymous")[0] == 403
    assert "anonymous" not in inventory(storage)
    other = storage.adapter("tenant-other/*")
    other.put("tenant-other/proof", b"synthetic", "text/plain")
    rejected(
        lambda: other.client.get_object(Bucket=storage.bucket, Key=record["key"]),
        status=403,
    )
    rejected(
        lambda: other.client.put_object(
            Bucket=storage.bucket, Key=record["key"], Body=b"synthetic"
        ),
        status=403,
    )
    rejected(lambda: adapter.client.list_buckets(), status=403)
    rejected(
        lambda: adapter.client.delete_object(Bucket=storage.bucket, Key=record["key"]),
        status=403,
    )
    rejected(
        lambda: storage.invalid_client().get_object(
            Bucket=storage.bucket, Key=record["key"]
        ),
        status=403,
    )
    operator.delete_object(Bucket=storage.bucket, Key="tenant-other/proof")
    upload = adapter.client.create_multipart_upload(
        Bucket=storage.bucket, Key="standards/synthetic-multipart"
    )
    part = adapter.client.upload_part(
        Bucket=storage.bucket,
        Key="standards/synthetic-multipart",
        UploadId=upload["UploadId"],
        PartNumber=1,
        Body=data,
    )
    adapter.client.complete_multipart_upload(
        Bucket=storage.bucket,
        Key="standards/synthetic-multipart",
        UploadId=upload["UploadId"],
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
    )
    assert adapter.get("standards/synthetic-multipart") == data
    operator.delete_object(Bucket=storage.bucket, Key="standards/synthetic-multipart")
    key, secret, token = storage.scoped("standards/*", write=False)
    v4, v6 = addresses(rig, storage.container, storage.network)
    peer(
        rig,
        storage.network,
        tcp=[(a, p, False) for a in (v4, v6) for p in (19000, 19001)],
        adapter={
            "endpoint": "http://guarded-storage:9000",
            "bucket": storage.bucket,
            "key": key,
            "secret": secret,
            "token": token,
            "object": record["key"],
            "sha256": record["sha256"],
        },
    )
    signed = storage.client(
        "s3", key, secret, token, "http://guarded-storage:9000"
    ).generate_presigned_url(
        "get_object",
        Params={"Bucket": storage.bucket, "Key": record["key"]},
        ExpiresIn=60,
    )
    assert peer(rig, storage.network, signed_url=signed) == record["sha256"]
    expired_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=2)
    with patch("botocore.auth.get_current_datetime", return_value=expired_at):
        expired_url = storage.client("s3", key, secret, token).generate_presigned_url(
            "get_object",
            Params={"Bucket": storage.bucket, "Key": record["key"]},
            ExpiresIn=60,
        )
    try:
        with urlopen(expired_url, timeout=5):
            raise AssertionError("expired presigned read was accepted")
    except HTTPError as error:
        with error:
            assert error.code == 403 and b"Request has expired" in error.read()
    assert sha(adapter.get(record["key"])) == record["sha256"]
    rig.evidence["private_s3_expiration_and_delete_denial"] = "PASS"
    rig.evidence["private_s3"] = (
        "PASS: scoped application adapter, cross-prefix denial, IPv4/IPv6 peer, Range, multipart, presign, content type"
    )


def provenance(rig, storage, out, local):
    obj = rig.inspect("image", rig.storage_image)
    assert obj["Config"]["Labels"]["org.prufai.storage-ingress"] == "guarded-v1"
    hashes = (
        rig.docker(
            "exec",
            storage.container,
            "sha256sum",
            "/usr/local/bin/minio",
            "/usr/local/bin/prufai-minio-ingress",
        )
        .decode()
        .splitlines()
    )
    hashes = {Path(line.split()[1]).name: line.split()[0] for line in hashes}
    result = {
        "image": obj["Id"],
        "platform": obj["Os"] + "/" + obj["Architecture"],
        "binaries": hashes,
    }
    if not local:
        assert result["platform"] == "linux/amd64"
        for name, binary in (
            ("minio-container-binary", "minio"),
            ("minio-ingress-binary", "prufai-minio-ingress"),
        ):
            assert hashes[binary] == (out / f"{name}.sha256").read_text().split()[0]
        result["decoder_image"] = json.loads(
            (out / "decoder.identity.json").read_text()
        )["image_id"]
        result["trivy"] = json.loads((out / "trivy-db.json").read_text())
        result["minio_source_and_go"] = (
            (out / "minio-container-binary.go-build-info.txt")
            .read_text()
            .splitlines()[:3]
        )
        result["guard_go"] = (
            (out / "minio-ingress-binary.go-build-info.txt").read_text().splitlines()[0]
        )
        result["sbom_sha256"] = {
            name: sha((out / f"{name}.cdx.json").read_bytes())
            for name in ("minio-source", "decoder")
        }
        result["scan_identity_match"] = "PASS"
    rig.evidence["provenance"] = result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--local-image")
    args = parser.parse_args()
    if not __debug__:
        parser.error("storage readiness requires Python assertions to be enabled")
    local = args.local_image is not None
    if not (
        (local and os.getenv("PRUFAI_DISPOSABLE_STORAGE_TEST") == "1")
        or (not local and os.getenv("GITHUB_ACTIONS") == "true")
    ):
        parser.error(
            "select a disposable local Docker context or run in GitHub Actions"
        )
    os.umask(0o077)
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if local:
        image = json.loads(run("docker", "image", "inspect", args.local_image))[0]["Id"]
    else:
        image = json.loads((out / "minio-source.identity.json").read_text())["image_id"]
    with ExitStack() as stack:
        rig = Rig(stack, image)
        try:
            rig.evidence["source_sha"] = (
                run("git", "rev-parse", "HEAD").decode().strip()
            )
            rig.evidence["worktree_clean"] = not run(
                "git", "status", "--porcelain", "--untracked-files=normal"
            ).strip()
            if not local:
                assert rig.evidence["worktree_clean"]
            rig.build_peer()
            source = Storage(rig, "source")
            isolated = rig.mark + "-outside"
            rig.create_network(isolated)
            provenance(rig, source, out, local)
            topology(rig, source, isolated)

            source_db = Database(rig, "source")
            expected_head = (
                run(sys.executable, "-m", "alembic", "heads", cwd=ROOT / "services/api")
                .decode()
                .strip()
                .splitlines()
            )
            assert len(expected_head) == 1
            head = source_db.migrate()
            assert expected_head == [head + " (head)"]
            rig.evidence["alembic_head"] = head
            records, creator = seed(source_db, source)
            signed_contract(rig, source, records[0])

            def versions():
                history = source.operator().list_object_versions(Bucket=source.bucket)
                assert not history.get("IsTruncated")
                return {
                    (entry["Key"], entry["VersionId"], entry["IsLatest"])
                    for group in ("Versions", "DeleteMarkers")
                    for entry in history.get(group, [])
                }

            expected_versions = versions()

            def original():
                assert inventory(source) == {r["key"] for r in records}
                assert versions() == expected_versions
                for row in records:
                    current = source.operator().head_object(
                        Bucket=source.bucket, Key=row["key"]
                    )
                    assert current["VersionId"] == row["object_version"]
                    assert (
                        sha(source.adapter("standards/*", write=False).get(row["key"]))
                        == row["sha256"]
                    )

            rig.evidence["unsafe_headers"] = unsafe_requests(
                source, records[0], original
            )
            rig.evidence["faults"] = faults(
                rig,
                source,
                records[0],
                lambda: source.adapter("standards/*", write=False).get(
                    records[0]["key"]
                ),
                original,
            )
            target = Storage(rig, "restore")
            target_db = Database(rig, "restore")
            rehearse(rig, source, source_db, target, target_db, records, creator)
            assert (
                target_db.sql("select version_num from alembic_version")
                .decode()
                .strip()
                == head
            )
            original()
            stack.close()
            rig.evidence["status"] = "PASS"
        finally:
            (out / "storage-readiness.json").write_text(
                json.dumps(rig.evidence, indent=2) + "\n"
            )
        print(
            "O1B.2 topology, fault recovery and synthetic database/object restore verified"
        )


if __name__ == "__main__":
    main()
