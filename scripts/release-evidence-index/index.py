#!/usr/bin/env python3
"""Offline, read-only D-01 projection of operator-staged synthetic release evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import lzma
import os
import re
import stat
import subprocess
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "moaltamimi-unbiasedtalent/PrufAI"
WEB = "https://github.com/" + REPOSITORY
INPUT_SCHEMA = "prufai-release-evidence-input/1"
SCHEMA = "prufai-release-evidence-index/1"
MANIFEST_SCHEMA = "prufai-v1-e06a/1"
PYTHON_CAPTURE_SCHEMA = "prufai-python-log-capture/1"
LIMIT = 32 * 1024 * 1024
SLOTS = ("head", "merge")
WORKFLOWS = {
    "application": ".github/workflows/ci.yml",
    "supply-chain": ".github/workflows/supply-chain.yml",
}
JOBS = {
    "application": {"python": "Python", "web": "Web", "browser": "Browser"},
    "supply-chain": {
        "Language SBOMs and audits (informational findings)": "Language scans (informational)",
        "Container and MinIO binary scans (informational findings)": (
            "Container/Go scans (informational)"
        ),
    },
}
STATUSES = {"queued", "in_progress", "completed", "waiting", "pending", "requested"}
CONCLUSIONS = {
    None,
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "skipped",
    "neutral",
    "action_required",
    "stale",
    "startup_failure",
}
COUNT_KEYS = {
    "required_evaluation_foundation_tests",
    "required_writing_execution_tests",
    "required_recorded_audio_tests",
    "required_human_speaking_tests",
    "required_speaking_results_tests",
    "required_transcription_execution_tests",
    "required_speaking_execution_tests",
}
OPEN_GATES = tuple(
    [f"E{i:02}" for i in range(1, 7)]
    + [f"H{i:02}" for i in range(1, 13)]
    + [f"O{i:02}" for i in range(1, 8)]
    + [f"A{i:02}" for i in range(1, 4)]
    + [
        "R81-01",
        "backend-guard-P2",
        "legacy-fence-P2",
        "P1-acceptance",
        "PR60-P2",
        "E02A-S1",
        "merge-review-exceptions",
    ]
)
KNOWN_FILES = {"index-input.json"}
DEFERRED_GATES = ("H08", "H11", "A03")
OPEN_GATES = tuple(gate for gate in OPEN_GATES if gate not in DEFERRED_GATES)
for _slot in SLOTS:
    KNOWN_FILES.update(
        {_slot + "-python.log", _slot + "-python-log-capture.json", _slot + "-artifacts.json"}
    )
    KNOWN_FILES.update(
        _slot + "-" + kind + suffix for kind in WORKFLOWS for suffix in ("-run.json", "-jobs.json")
    )
    KNOWN_FILES.update(_slot + "-" + kind + ".zip" for kind in ("containers", "language"))


class EvidenceError(ValueError):
    """Fixed diagnostic codes deliberately exclude paths, raw input and exceptions."""


def need(condition, code="INVALID_EVIDENCE"):
    if not condition:
        raise EvidenceError(code)


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def hex_value(value, size):
    need(
        isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(size) + "}", value) is not None,
        "INVALID_DIGEST",
    )
    return value


def number(value):
    need(type(value) is int and 0 < value <= 10**15, "INVALID_IDENTIFIER")
    return value


def timestamp(value):
    need(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) is not None,
        "INVALID_TIMESTAMP",
    )
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode(data):
    try:
        value = json.loads(
            data,
            object_pairs_hook=unique_object,
            parse_constant=lambda _: need(False, "INVALID_JSON"),
        )
        need(isinstance(value, dict), "INVALID_JSON")
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise EvidenceError("INVALID_JSON") from None


def directory(path):
    """Pin canonical directory components; never follow a symlink supplied by a caller."""
    path = Path(path)
    need(path.is_absolute() and ".." not in path.parts, "UNSAFE_PATH")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except OSError:
        os.close(fd)
        raise EvidenceError("UNSAFE_PATH") from None


class Bundle:
    def __init__(self, root):
        self.root = Path(root)
        self.fd = directory(root)
        info = os.fstat(self.fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            self.close()
            raise EvidenceError("BUNDLE_MUST_BE_OWNER_ONLY")

    def close(self):
        os.close(self.fd)

    def read(self, name, optional=False):
        need(name in KNOWN_FILES, "UNSAFE_PATH")
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            if optional:
                return None
            raise EvidenceError("MISSING_INPUT") from None
        except OSError:
            raise EvidenceError("UNSAFE_INPUT") from None
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_INPUT")
            need(info.st_uid == os.getuid() and not info.st_mode & 0o022, "UNSAFE_INPUT")
            need(info.st_size <= LIMIT, "INPUT_TOO_LARGE")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(LIMIT + 1)
            need(len(data) <= LIMIT, "INPUT_TOO_LARGE")
            return data
        finally:
            os.close(fd)


class Source:
    def __init__(self, repository):
        self.root = Path(repository)
        self.files = {}
        self.heads = {}
        self.signatures = {}
        fd = directory(repository)
        os.close(fd)

    def git(self, *args):
        env = {
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "-c",
                "log.showSignature=false",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "protocol.allow=never",
                "-C",
                str(self.root),
                *args,
            ],
            env=env,
            capture_output=True,
            check=False,
            timeout=15,
        )
        need(result.returncode == 0, "SOURCE_UNAVAILABLE")
        return result.stdout

    def file(self, revision, name):
        hex_value(revision, 40)
        key = (revision, name)
        if key not in self.files:
            self.files[key] = self.git("show", revision + ":" + name)
        return self.files[key]

    def identity(self, value):
        need(isinstance(value, dict) and set(value) == {"sha", "tree"}, "INVALID_SOURCE")
        revision, tree = hex_value(value["sha"], 40), hex_value(value["tree"], 40)
        need(
            self.git("rev-parse", revision + "^{commit}").decode().strip() == revision,
            "SOURCE_MISMATCH",
        )
        need(
            self.git("rev-parse", revision + "^{tree}").decode().strip() == tree,
            "SOURCE_MISMATCH",
        )
        return {"sha": revision, "tree": tree}

    def migration_head(self, revision):
        if revision in self.heads:
            return self.heads[revision]
        names = (
            self.git(
                "ls-tree",
                "-r",
                "--name-only",
                revision,
                "services/api/alembic/versions",
            )
            .decode()
            .splitlines()
        )
        graph = {}
        for name in names:
            if not re.fullmatch(r"services/api/alembic/versions/\d{4}_[a-z0-9_]+\.py", name):
                continue
            values = {}
            for node in ast.parse(self.file(revision, name)).body:
                target = (
                    node.target
                    if isinstance(node, ast.AnnAssign)
                    else node.targets[0]
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    else None
                )
                if isinstance(target, ast.Name) and target.id in {
                    "revision",
                    "down_revision",
                }:
                    values[target.id] = ast.literal_eval(node.value)
            current = values.get("revision")
            need(set(values) == {"revision", "down_revision"}, "INVALID_MIGRATION_GRAPH")
            need(
                isinstance(current, str) and re.fullmatch(r"\d{4}_[a-z0-9_]+", current),
                "INVALID_MIGRATION_GRAPH",
            )
            need(current not in graph, "INVALID_MIGRATION_GRAPH")
            down = values.get("down_revision")
            graph[current] = (
                () if down is None else (down,) if isinstance(down, str) else tuple(down)
            )
        parents = {x for values in graph.values() for x in values}
        heads = set(graph) - parents
        need(len(heads) == 1 and parents <= graph.keys(), "INVALID_MIGRATION_GRAPH")
        head = next(iter(heads))
        seen, active = set(), set()

        def visit(node):
            need(node not in active, "INVALID_MIGRATION_GRAPH")
            if node not in seen:
                active.add(node)
                for parent in graph[node]:
                    visit(parent)
                active.remove(node)
                seen.add(node)

        visit(head)
        need(seen == graph.keys(), "INVALID_MIGRATION_GRAPH")
        self.heads[revision] = head
        return head

    def report_signatures(self, revision):
        if revision in self.signatures:
            return self.signatures[revision]
        workflow = self.file(revision, WORKFLOWS["application"]).decode()
        keys = set(re.findall(r"[\"'](required_[a-z_]+_tests)[\"']", workflow))
        need(keys <= COUNT_KEYS, "UNSUPPORTED_REPORT_SCHEMA")
        signatures = {("count", key) for key in keys}
        checker = self.file(revision, "scripts/check-required-evaluation-junit.py")
        defaults = None
        for node in ast.parse(checker).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MODULES" for t in node.targets
            ):
                defaults = ast.literal_eval(node.value)
        for match in re.finditer(
            r"python ../../scripts/check-required-evaluation-junit\.py[^\n]*", workflow
        ):
            modules = tuple(sorted(re.findall(r"\btest_[a-z0-9_]+\b", match.group()))) or tuple(
                sorted(defaults or ())
            )
            need(modules, "UNSUPPORTED_REPORT_SCHEMA")
            signature = ("modules", *modules)
            need(signature not in signatures, "DUPLICATE_REPORT")
            signatures.add(signature)
        need(signatures, "UNSUPPORTED_REPORT_SCHEMA")
        if "python scripts/run-privacy-integration.py" in workflow:
            privacy = self.file(revision, "scripts/run-privacy-integration.py").decode()
            modules = tuple(sorted(set(re.findall(r'"(test_[a-z0-9_]+)"', privacy))))
            need(modules, "UNSUPPORTED_REPORT_SCHEMA")
            signature = ("modules", *modules)
            need(signature not in signatures, "DUPLICATE_REPORT")
            signatures.add(signature)
        self.signatures[revision] = signatures
        return signatures


def integration(log, source, revision):
    result = {
        "verification": "NOT VERIFIED",
        "report_count": None,
        "required_skips": None,
        "applied_migration_head": None,
    }
    if log is None:
        return result
    expected = source.report_signatures(revision)
    head = source.migration_head(revision)
    try:
        text = log.decode("utf-8")
    except UnicodeError:
        raise EvidenceError("INVALID_LOG") from None
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in text.splitlines()]
    lines = [re.sub(r"^\d{4}-\d\d-\d\dT\S+\s+", "", line) for line in lines]
    need(revision in lines, "LOG_SOURCE_MISMATCH")
    reports, applied = {}, []
    for line in lines:
        if not line.startswith("{") or not any(
            x in line for x in ("required_skips", "alembic_head")
        ):
            continue
        need(line.endswith("}"), "INVALID_LOG")
        need(len(line) <= 8192, "INVALID_LOG")
        try:
            tree = ast.parse(line, mode="eval")
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    keys = [ast.literal_eval(key) for key in node.keys]
                    need(all(isinstance(key, str) for key in keys), "INVALID_LOG")
                    need(len(keys) == len(set(keys)), "DUPLICATE_REPORT_FIELD")
            data = ast.literal_eval(tree)
        except (ValueError, SyntaxError, RecursionError):
            raise EvidenceError("INVALID_LOG") from None
        need(isinstance(data, dict), "INVALID_LOG")
        if "alembic_head" in data:
            need(data["alembic_head"] == [(head,)], "MIGRATION_MISMATCH")
            applied.append(head)
            continue
        need(
            type(data["required_skips"]) is int and 0 <= data["required_skips"] <= 1000000,
            "INVALID_REPORT",
        )
        if "required_modules_executed" in data:
            need(
                set(data) == {"required_skips", "required_modules_executed"},
                "UNSUPPORTED_REPORT_SCHEMA",
            )
            counts = data["required_modules_executed"]
            need(isinstance(counts, dict), "INVALID_REPORT")
            signature = ("modules", *sorted(counts))
        else:
            keys = set(data) - {"required_skips"}
            need(len(keys) == 1 and keys <= COUNT_KEYS, "UNSUPPORTED_REPORT_SCHEMA")
            key = next(iter(keys))
            counts, signature = {key: data[key]}, ("count", key)
        need(signature in expected, "UNSUPPORTED_REPORT_SCHEMA")
        need(signature not in reports, "DUPLICATE_REPORT")
        need(
            all(type(n) is int and 0 < n <= 1000000 for n in counts.values()),
            "INVALID_REPORT",
        )
        reports[signature] = data["required_skips"]
    need(len(applied) <= 1, "MIGRATION_MISMATCH")
    complete = reports.keys() == expected
    result.update(
        verification="LOCAL_PARSED" if complete else "NOT VERIFIED",
        report_count=len(reports),
        required_skips=sum(reports.values()) if complete else None,
        expected_report_count=len(expected),
        applied_migration_head=head if applied else None,
    )
    return result


def captured_integration(bundle, slot, source, revision, app):
    log = bundle.read(slot + "-python.log", optional=True)
    raw = bundle.read(slot + "-python-log-capture.json", optional=True)
    need(log is None or app.get("attempt") is not None, "INCONSISTENT_RUN")
    need(raw is None or log is not None, "CAPTURE_LOG_MISSING")
    if log is None or raw is None:
        return integration(None, source, revision)
    capture = decode(raw)
    need(
        set(capture)
        == {"schema", "repository", "source_sha", "run_id", "run_attempt", "job_id", "sha256"}
        and capture["schema"] == PYTHON_CAPTURE_SCHEMA,
        "UNSUPPORTED_CAPTURE_SCHEMA",
    )
    need(capture["repository"] == REPOSITORY, "CAPTURE_REPOSITORY_MISMATCH")
    need(hex_value(capture["source_sha"], 40) == revision, "CAPTURE_SOURCE_MISMATCH")
    need(number(capture["run_id"]) == app["run_id"], "CAPTURE_RUN_MISMATCH")
    need(number(capture["run_attempt"]) == app["attempt"], "CAPTURE_ATTEMPT_MISMATCH")
    python_jobs = [job for job in app["jobs"] if job["name"] == "Python"]
    need(len(python_jobs) == 1, "CAPTURE_PYTHON_JOB_MISSING")
    need(number(capture["job_id"]) == python_jobs[0]["id"], "CAPTURE_JOB_MISMATCH")
    need(hex_value(capture["sha256"], 64) == sha(log), "CAPTURE_CHECKSUM_MISMATCH")
    return integration(log, source, revision)


def workflow(bundle, slot, kind, revision, run_id):
    files = {
        suffix: bundle.read(f"{slot}-{kind}-{suffix}.json", optional=True)
        for suffix in ("run", "jobs")
    }
    result = {
        "kind": kind,
        "run_id": run_id,
        "url": f"{WEB}/actions/runs/{run_id}" if run_id is not None else None,
        "reported_conclusion": None,
        "verification": "NOT VERIFIED",
        "jobs": [],
        "findings_policy": "INFORMATIONAL_NOT_SECURITY_ACCEPTANCE"
        if kind == "supply-chain"
        else "TECHNICAL_EXECUTION_ONLY",
    }
    if files["run"] is None:
        need(files["jobs"] is None, "INCONSISTENT_RUN")
        return result
    need(run_id is not None, "INCONSISTENT_RUN")
    run = decode(files["run"])
    number(run.get("id"))
    need(
        run.get("id") == run_id and run.get("head_sha") == revision,
        "RUN_SOURCE_MISMATCH",
    )
    need(
        run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("path") == WORKFLOWS[kind],
        "RUN_REPOSITORY_MISMATCH",
    )
    need(
        run.get("event") == ("pull_request" if slot == "head" else "push"),
        "RUN_EVENT_MISMATCH",
    )
    need(slot != "merge" or run.get("head_branch") == "main", "RUN_BRANCH_MISMATCH")
    need(
        run.get("status") in STATUSES and run.get("conclusion") in CONCLUSIONS,
        "INVALID_RUN_STATUS",
    )
    number(run.get("run_attempt"))
    need((run["status"] == "completed") == (run["conclusion"] is not None), "INVALID_RUN_STATUS")
    result.update(
        reported_conclusion=run["conclusion"],
        status=run["status"],
        attempt=run["run_attempt"],
    )
    if files["jobs"] is None:
        return result
    payload = decode(files["jobs"])
    need(isinstance(payload.get("jobs"), list), "INVALID_JOBS")
    seen = set()
    job_ids = set()
    for job in payload["jobs"]:
        need(isinstance(job, dict) and job.get("name") in JOBS[kind], "UNSUPPORTED_JOB")
        name = job["name"]
        need(name not in seen, "DUPLICATE_JOB")
        seen.add(name)
        job_id = number(job.get("id"))
        number(job.get("run_id"))
        number(job.get("run_attempt"))
        need(job["run_attempt"] == run["run_attempt"], "JOB_ATTEMPT_MISMATCH")
        need(job_id not in job_ids, "DUPLICATE_JOB")
        job_ids.add(job_id)
        need(
            job.get("run_id") == run_id and job.get("head_sha") == revision,
            "JOB_SOURCE_MISMATCH",
        )
        need(
            job.get("status") in STATUSES and job.get("conclusion") in CONCLUSIONS,
            "INVALID_JOB_STATUS",
        )
        need(
            (job["status"] == "completed") == (job["conclusion"] is not None), "INVALID_JOB_STATUS"
        )
        result["jobs"].append(
            {
                "name": JOBS[kind][name],
                "id": job_id,
                "url": f"{WEB}/actions/runs/{run_id}/job/{job_id}",
                "status": job["status"],
                "conclusion": job["conclusion"],
            }
        )
    need(
        type(payload.get("total_count")) is int and payload["total_count"] == len(seen),
        "INCOMPLETE_JOBS",
    )
    complete = seen == JOBS[kind].keys()
    if run["status"] == "completed" and complete:
        need(all(j["status"] == "completed" for j in result["jobs"]), "INCONSISTENT_RUN")
        if run["conclusion"] == "success":
            need(
                all(j["conclusion"] == "success" for j in result["jobs"]),
                "INCONSISTENT_RUN",
            )
        result["verification"] = "LOCAL_METADATA_CHECKED"
    result["jobs"].sort(key=lambda j: j["name"])
    return result


def zip_members(archive, selected):
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            names = z.namelist()
            need(len(names) <= 256 and len(set(names)) == len(names), "UNSAFE_ARCHIVE")
            for entry in z.infolist():
                need(
                    re.fullmatch(r"[A-Za-z0-9_.-]+", entry.filename) is not None
                    and entry.filename not in {".", ".."},
                    "UNSAFE_ARCHIVE",
                )
                need(not stat.S_ISLNK(entry.external_attr >> 16), "UNSAFE_ARCHIVE")
            result = {}
            for name in selected:
                need(z.getinfo(name).file_size <= LIMIT, "INPUT_TOO_LARGE")
                result[name] = z.read(name)
            return result
    except (
        zipfile.BadZipFile,
        KeyError,
        UnicodeError,
        RuntimeError,
        zlib.error,
        lzma.LZMAError,
        OSError,
        EOFError,
    ):
        raise EvidenceError("INVALID_RELEASE_ARCHIVE") from None


def release_files(archive, identity, run_id, attempt, migration):
    files = zip_members(
        archive,
        (
            "v1-e06a-release-manifest.json",
            "v1-e06a-rehearsal.json",
            "v1-e06a-release-manifest.sha256",
        ),
    )
    manifest_bytes, proof_bytes = (
        files["v1-e06a-release-manifest.json"],
        files["v1-e06a-rehearsal.json"],
    )
    digest = files["v1-e06a-release-manifest.sha256"].decode("ascii")
    need(
        digest == sha(manifest_bytes) + "  v1-e06a-release-manifest.json\n",
        "MANIFEST_CHECKSUM_MISMATCH",
    )
    manifest, proof = decode(manifest_bytes), decode(proof_bytes)
    need(manifest.get("schema") == MANIFEST_SCHEMA, "UNSUPPORTED_MANIFEST_SCHEMA")
    for data in [manifest.get("SOURCE_IDENTITY", {}), proof.get("source", {})]:
        need(
            data.get("repository") == REPOSITORY
            and data.get("commit") == identity["sha"]
            and data.get("tree") == identity["tree"],
            "MANIFEST_SOURCE_MISMATCH",
        )
    test = manifest.get("TEST_EVIDENCE", {})
    need(test.get("evidence_file") == "v1-e06a-rehearsal.json", "REHEARSAL_REFERENCE_MISMATCH")
    need(test.get("classification") == "technical execution evidence", "UNSUPPORTED_REHEARSAL")
    need(test.get("evidence_sha256") == sha(proof_bytes), "REHEARSAL_CHECKSUM_MISMATCH")
    need(
        str(test.get("supply_chain_run")) == str(run_id)
        and str(test.get("supply_chain_attempt")) == str(attempt),
        "REHEARSAL_RUN_MISMATCH",
    )
    config = manifest.get("CONFIGURATION_IDENTITY", {})
    need(
        config.get("value", {}).get("schema") == MANIFEST_SCHEMA
        and config["value"].get("environment") == "test",
        "UNSUPPORTED_REHEARSAL",
    )
    need(config.get("alembic_head") == proof.get("alembic_head") == migration, "MIGRATION_MISMATCH")
    need(
        config.get("sha256") == sha(canonical(config.get("value"))),
        "CONFIGURATION_CHECKSUM_MISMATCH",
    )
    need(
        proof.get("profile") == "SYNTHETIC_TEST" and proof.get("status") == "PASS",
        "UNSUPPORTED_REHEARSAL",
    )
    policy = manifest.get("OPERATIONAL_AUTHORIZATION", {})
    need(
        policy.get("SYNTHETIC_TEST", {}).get("status") == "PASS"
        and all(
            policy.get(p, {}).get("status") == "NOT_AUTHORIZED"
            for p in ["STAGING_PROPOSED", "REAL_PILOT"]
        ),
        "UNSUPPORTED_REHEARSAL",
    )
    content = manifest.get("CONTENT_APPROVAL", {})
    need(
        content.get("BERLIN_FORM_APPROVAL") == "NOT VERIFIED"
        and content.get("REAL_FORM_MANIFEST") == "UNAVAILABLE"
        and content.get("CONTENT_PUBLICATION") == "NOT AUTHORIZED",
        "UNSUPPORTED_REHEARSAL",
    )
    return {
        "verification": "LOCAL_CHECKSUMS_MATCHED",
        "manifest_file": "v1-e06a-release-manifest.json",
        "manifest_sha256": sha(manifest_bytes),
        "rehearsal_file": "v1-e06a-rehearsal.json",
        "rehearsal_sha256": sha(proof_bytes),
        "reported_technical_rehearsal_status": "PASS",
        "migration_head": migration,
    }


def artifacts(bundle, slot, identity, run, as_of, migration):
    payload = bundle.read(slot + "-artifacts.json", optional=True)
    result, release = [], {"verification": "NOT VERIFIED"}
    metadata = None if payload is None else decode(payload)
    entries = [] if metadata is None else metadata.get("artifacts")
    need(isinstance(entries, list), "INVALID_ARTIFACTS")
    if metadata is not None:
        need(
            type(metadata.get("total_count")) is int and metadata["total_count"] == len(entries),
            "INCOMPLETE_ARTIFACTS",
        )
    need(not entries or run.get("attempt") is not None, "ARTIFACT_RUN_MISMATCH")
    seen, ids = {}, set()
    for item in entries:
        need(isinstance(item, dict), "INVALID_ARTIFACTS")
        candidates = {f"o1a-{kind}-{identity['sha']}": kind for kind in ("containers", "language")}
        need(item.get("name") in candidates, "ARTIFACT_SOURCE_MISMATCH")
        kind = candidates[item["name"]]
        need(kind not in seen, "DUPLICATE_ARTIFACT")
        identifier = number(item.get("id"))
        number(item.get("workflow_run", {}).get("id"))
        need(identifier not in ids, "DUPLICATE_ARTIFACT")
        ids.add(identifier)
        need(
            item.get("workflow_run", {}).get("id") == run["run_id"]
            and item["workflow_run"].get("head_sha") == identity["sha"],
            "ARTIFACT_RUN_MISMATCH",
        )
        digest = item.get("digest")
        need(isinstance(digest, str) and digest.startswith("sha256:"), "INVALID_DIGEST")
        hex_value(digest[7:], 64)
        need(type(item.get("expired")) is bool, "INVALID_ARTIFACTS")
        timestamp(item.get("created_at"))
        expiry = timestamp(item.get("expires_at"))
        need(timestamp(item["created_at"]) <= expiry, "INVALID_TIMESTAMP")
        need(timestamp(item["created_at"]) <= as_of, "INVALID_TIMESTAMP")
        seen[kind] = item
    for kind in ("containers", "language"):
        item = seen.get(kind)
        row = {
            "kind": kind,
            "availability": "UNAVAILABLE",
            "verification": "NOT VERIFIED",
            "remote_live_availability": "NOT VERIFIED",
        }
        if item:
            row.update(
                id=item["id"],
                url=f"{WEB}/actions/runs/{run['run_id']}/artifacts/{item['id']}",
                expected_sha256=item["digest"][7:],
                expires_at=item["expires_at"],
            )
            if item["expired"] or timestamp(item["expires_at"]) <= as_of:
                row["reason"] = "EXPIRED"
            else:
                data = bundle.read(f"{slot}-{kind}.zip", optional=True)
                if data is None:
                    row["reason"] = "LOCAL_ARCHIVE_MISSING"
                else:
                    digest = sha(data)
                    need(digest == row["expected_sha256"], "ARTIFACT_CHECKSUM_MISMATCH")
                    need(run.get("attempt") is not None, "ARTIFACT_RUN_MISMATCH")
                    summary = decode(zip_members(data, ("summary.json",))["summary.json"])
                    need(
                        summary.get("source_sha") == identity["sha"]
                        and summary.get("scope")
                        == ("container" if kind == "containers" else "language"),
                        "ARCHIVE_SOURCE_MISMATCH",
                    )
                    need(
                        summary.get("status") in {"COMPLETED", "NOT_VERIFIED"},
                        "UNSUPPORTED_SUMMARY",
                    )
                    need(
                        run["reported_conclusion"] != "success" or summary["status"] == "COMPLETED",
                        "INCONSISTENT_SCAN_EVIDENCE",
                    )
                    row.update(
                        availability="LOCAL_AVAILABLE",
                        verification="LOCAL_CHECKSUM_MATCHED",
                        sha256=digest,
                        reported_scan_evidence=summary["status"],
                    )
                    if kind == "containers":
                        release = release_files(
                            data, identity, run["run_id"], run["attempt"], migration
                        )
        else:
            row["reason"] = "METADATA_MISSING"
        result.append(row)
    return result, release


def generate(bundle, source):
    request = decode(bundle.read("index-input.json"))
    need(
        set(request)
        == {
            "schema",
            "repository",
            "as_of",
            "profile",
            "example_only",
            "sources",
            "runs",
        },
        "UNSUPPORTED_INPUT_SCHEMA",
    )
    need(
        request["schema"] == INPUT_SCHEMA and request["repository"] == REPOSITORY,
        "UNSUPPORTED_INPUT_SCHEMA",
    )
    need(
        request["profile"] == "SYNTHETIC_TEST" and type(request["example_only"]) is bool,
        "UNSUPPORTED_PROFILE",
    )
    as_of = timestamp(request["as_of"])
    need(
        isinstance(request["sources"], dict) and set(request["sources"]) == set(SLOTS),
        "INVALID_SOURCE",
    )
    need(
        isinstance(request["runs"], dict) and set(request["runs"]) == set(SLOTS),
        "INVALID_RUNS",
    )
    ids, sources = set(), {}
    for slot in SLOTS:
        if slot == "merge" and request["sources"][slot] is None:
            need(request["runs"][slot] is None, "INVALID_RUNS")
            sources[slot] = None
            need(
                not any(
                    bundle.read(name, optional=True) is not None
                    for name in KNOWN_FILES
                    if name.startswith("merge-")
                ),
                "INCONSISTENT_RUN",
            )
            continue
        sources[slot] = source.identity(request["sources"][slot])
        need(set(request["runs"][slot]) == set(WORKFLOWS), "INVALID_RUNS")
        for run_id in request["runs"][slot].values():
            if run_id is None:
                continue
            number(run_id)
            need(run_id not in ids, "DUPLICATE_RUN")
            ids.add(run_id)
    if sources["merge"] is not None:
        need(
            sources["head"]["sha"] != sources["merge"]["sha"]
            and sources["head"]["tree"] == sources["merge"]["tree"],
            "MERGE_TREE_MISMATCH",
        )
        parents = source.git("show", "-s", "--format=%P", sources["merge"]["sha"]).decode().split()
        need(len(parents) == 2 and parents[1] == sources["head"]["sha"], "MERGE_PARENT_MISMATCH")
    index = {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "as_of": request["as_of"],
        "profile": "SYNTHETIC_TEST",
        "evidence_classification": "EXAMPLE_ONLY"
        if request["example_only"]
        else "TECHNICAL_TESTED",
        "github_authenticity": "NOT VERIFIED",
        "input_custody": "OPERATOR_STAGED_NOT_AUTHENTICATED_BY_GENERATOR",
        "remote_availability": "NOT VERIFIED",
        "security_acceptance": "NOT VERIFIED",
        "production_readiness": "NO",
        "pilot_launch": "NOT AUTHORIZED",
        "decisions": {gate: "OPEN" for gate in OPEN_GATES},
        "deferred_capability_gates": dict.fromkeys(DEFERRED_GATES, "DEFERRED"),
        "revisions": [],
    }
    index["decisions"].update(
        {"E03A": "BLOCKED", "E05-historical-timeout": "ROOT CAUSE UNRESOLVED"}
    )
    for slot in SLOTS:
        identity = sources[slot]
        if identity is None:
            index["revisions"].append(
                {"scope": "POST_MERGE", "source": None, "verification": "NOT VERIFIED"}
            )
            continue
        app = workflow(
            bundle,
            slot,
            "application",
            identity["sha"],
            request["runs"][slot]["application"],
        )
        security = workflow(
            bundle,
            slot,
            "supply-chain",
            identity["sha"],
            request["runs"][slot]["supply-chain"],
        )
        parsed = captured_integration(bundle, slot, source, identity["sha"], app)
        need(
            app["reported_conclusion"] != "success" or parsed["required_skips"] in {None, 0},
            "INCONSISTENT_RUN",
        )
        archive, release = artifacts(
            bundle,
            slot,
            identity,
            security,
            as_of,
            source.migration_head(identity["sha"]),
        )
        if parsed["applied_migration_head"] is not None and "migration_head" in release:
            need(
                parsed["applied_migration_head"] == release["migration_head"],
                "MIGRATION_MISMATCH",
            )
        index["revisions"].append(
            {
                "scope": "PR_HEAD" if slot == "head" else "POST_MERGE",
                "source": identity | {"url": f"{WEB}/commit/{identity['sha']}"},
                "workflows": [app, security],
                "required_integrations": parsed,
                "artifacts": archive,
                "release": release,
            }
        )
    if request["example_only"]:
        for revision in index["revisions"]:
            if revision["source"] is None:
                continue
            revision["source"]["url"] = None
            for run in revision["workflows"]:
                run["url"] = None
                for job in run["jobs"]:
                    job["url"] = None
            for item in revision["artifacts"]:
                if "url" in item:
                    item["url"] = None
    return index


def markdown(index):
    lines = [
        "# Redacted V1 release-evidence index",
        "",
        f"Classification: **{index['evidence_classification']}** · Profile: **SYNTHETIC_TEST**",
        "",
        f"Availability snapshot: `{index['as_of']}`. Local checks do not authenticate "
        "GitHub records or establish current remote availability.",
        "",
        "**Production readiness: NO. Pilot launch: NOT AUTHORIZED. E03A: BLOCKED.**",
        "",
        "Informational vulnerability scans do not establish independent security acceptance. "
        "Historical review records remain unchanged; no human approval is inferred.",
        "",
    ]
    for revision in index["revisions"]:
        source = revision["source"]
        lines += ["## " + revision["scope"], ""]
        if source is None:
            lines += ["Source and post-merge evidence: **NOT VERIFIED**. No merge is asserted.", ""]
            continue
        lines += [
            (
                f"Source: [{source['sha']}]({source['url']}) · Tree: `{source['tree']}`"
                if source["url"]
                else f"Example source: `{source['sha']}` · Tree: `{source['tree']}`"
            ),
            "",
            "| Workflow | Reported conclusion | Verification |",
            "| --- | --- | --- |",
        ]
        for run in revision["workflows"]:
            link = (
                f"[{run['kind']} / {run['run_id']}]({run['url']})"
                if run["url"]
                else run["kind"] + " / " + str(run["run_id"] or "NOT VERIFIED")
            )
            lines.append(
                f"| {link} | {run['reported_conclusion'] or 'NOT VERIFIED'} | "
                f"{run['verification']} |"
            )
        for run in revision["workflows"]:
            for job in run["jobs"]:
                label = f"[{job['name']}]({job['url']})" if job["url"] else job["name"]
                lines += ["", f"- {label}: {job['status']} / {job['conclusion'] or 'NOT VERIFIED'}"]
        proof = revision["required_integrations"]
        count = proof["report_count"] if proof["report_count"] is not None else "NOT VERIFIED"
        skips = proof["required_skips"] if proof["required_skips"] is not None else "NOT VERIFIED"
        lines += [
            "",
            f"Required reports: **{count}**; required skips: **{skips}**; "
            f"verification: **{proof['verification']}**.",
            "",
            f"Applied migration head: `{proof['applied_migration_head'] or 'NOT VERIFIED'}`. "
            "General-suite skips are outside these required-report totals.",
            "",
            "| Artifact | Availability / verification | SHA-256 |",
            "| --- | --- | --- |",
        ]
        for item in revision["artifacts"]:
            label = (
                f"[{item['kind']} / {item['id']}]({item['url']})"
                if item.get("url")
                else item["kind"]
            )
            availability = item["availability"]
            if "reason" in item:
                availability += " (" + item["reason"] + ")"
            lines.append(
                f"| {label} | {availability} / {item['verification']} | "
                f"`{item.get('sha256', 'NOT VERIFIED')}` |"
            )
        for item in revision["artifacts"]:
            if "expires_at" in item:
                lines += ["", f"{item['kind']} recorded expiry: `{item['expires_at']}`."]
        release = revision["release"]
        lines += ["", f"E06A manifest/rehearsal verification: **{release['verification']}**."]
        if "manifest_sha256" in release:
            lines += [
                "",
                f"- `{release['manifest_file']}` SHA-256: `{release['manifest_sha256']}`",
                f"- `{release['rehearsal_file']}` SHA-256: `{release['rehearsal_sha256']}`",
            ]
        links = [
            ("Pinned readiness register", "docs/releases/v1-readiness-register.md"),
            (
                "Deferred pre-production reviews",
                "docs/operations/pre-production-review-checklist.md",
            ),
            ("E06A verification instructions", "docs/releases/v1-e06a/README.md"),
        ]
        for label, path in [] if index["evidence_classification"] == "EXAMPLE_ONLY" else links:
            lines += ["", f"[{label}]({WEB}/blob/{source['sha']}/{path})"]
        lines.append("")
    lines += ["## Open decisions", "", "| Gate | Disposition |", "| --- | --- |"]
    lines += [f"| {gate} | {value} |" for gate, value in sorted(index["decisions"].items())]
    lines += [
        "",
        "Outside-pilot capability gates H08, H11 and A03 remain DEFERRED "
        "in the existing register. No capability or decision is enabled here.",
    ]
    lines += [
        "",
        "Artifact links can expire or be deleted before their recorded expiry. "
        "A local retained archive is not proof of current remote access. "
        "Refresh authenticated metadata and repeat local checks for a new snapshot. "
        "Missing or expired evidence is never a pass.",
        "",
    ]
    if index["evidence_classification"] == "EXAMPLE_ONLY":
        lines.insert(
            2,
            "**Synthetic illustration only. Source commits and run/artifact IDs "
            "are fixture values, not hosted execution evidence. "
            "External evidence links are omitted.**\n",
        )
    return "\n".join(lines).encode()


def publish(output, bundle_root, repository, index):
    output = Path(output)
    need(output.is_absolute() and output.name not in {"", ".", ".."}, "UNSAFE_OUTPUT")
    parent = output.parent
    fd = directory(parent)
    try:
        for protected in (Path(bundle_root), Path(repository)):
            need(
                not output.is_relative_to(protected) and not protected.is_relative_to(output),
                "OUTPUT_OVERLAPS_INPUT",
            )
        os.mkdir(output.name, mode=0o700, dir_fd=fd)
        target = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            payloads = {
                "release-evidence-index.json": canonical(index),
                "release-evidence-index.md": markdown(index),
            }
            payloads["release-evidence-index.sha256"] = "".join(
                sha(value) + "  " + name + "\n" for name, value in sorted(payloads.items())
            ).encode()
            for name, data in payloads.items():
                file = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=target,
                )
                with os.fdopen(file, "wb") as stream:
                    stream.write(data)
        finally:
            os.close(target)
    except OSError:
        raise EvidenceError("OUTPUT_MUST_BE_NEW") from None
    finally:
        os.close(fd)


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        raise EvidenceError("INVALID_ARGUMENTS") from None


def main():
    parser = SafeParser(prog="prufai-evidence-index", description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    bundle = None
    try:
        args = parser.parse_args()
        bundle = Bundle(args.bundle)
        result = generate(bundle, Source(args.repository))
        publish(args.output, args.bundle, args.repository, result)
    except (
        EvidenceError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        SyntaxError,
        subprocess.SubprocessError,
    ):
        print(
            "EVIDENCE_INDEX_REJECTED: invalid or inconsistent local evidence",
            file=sys.stderr,
        )
        return 2
    finally:
        if bundle:
            bundle.close()
    print("REDACTED_EVIDENCE_INDEX_WRITTEN; production readiness NO; pilot launch NOT AUTHORIZED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
