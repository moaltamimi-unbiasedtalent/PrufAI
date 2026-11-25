"""D-01 uses disposable Git/receipt fixtures; no GitHub credentials or services."""

import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release-evidence-index/index.py"
SPEC = importlib.util.spec_from_file_location("release_evidence_index", SCRIPT)
index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(index)
ENV = {
    "PATH": os.defpath,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Synthetic Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Synthetic Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
}


def git(root, *args, data=None):
    return (
        subprocess.run(
            ["git", "-C", str(root), *args],
            env=ENV,
            input=data,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)


def put_json(root, name, data):
    put(root, name, index.canonical(data))


def get(root, name):
    return json.loads((root / name).read_bytes())


def packed(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
    return result.getvalue()


def unpacked(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        socket.socket, "connect", lambda *_a, **_k: pytest.fail("network forbidden")
    )
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    put(source, "README.md", b"fixture base\n")
    put(
        source,
        ".github/workflows/ci.yml",
        b'print({"required_evaluation_foundation_tests": 1, "required_skips": 0})\n'
        b'python ../../scripts/check-required-evaluation-junit.py "$RUNNER_TEMP/fixture.xml" '
        b"test_fixture_postgresql_integration\n",
    )
    put(
        source,
        "scripts/check-required-evaluation-junit.py",
        b'MODULES = ("test_fixture_postgresql_integration",)\n',
    )
    put(
        source,
        "services/api/alembic/versions/0001_fixture.py",
        b'revision = "0001_fixture"\ndown_revision = None\n',
    )
    for name in [
        "docs/releases/v1-readiness-register.md",
        "docs/operations/pre-production-review-checklist.md",
        "docs/releases/v1-e06a/README.md",
    ]:
        put(source, name, b"fixture documentation\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "synthetic base")
    base = git(source, "rev-parse", "HEAD")
    put(source, "README.md", b"fixture reviewed head\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "synthetic head")
    head, tree = git(source, "rev-parse", "HEAD"), git(source, "rev-parse", "HEAD^{tree}")
    merge = git(source, "commit-tree", tree, "-p", base, "-p", head, data=b"synthetic merge\n")
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    request = {
        "schema": index.INPUT_SCHEMA,
        "repository": index.REPOSITORY,
        "as_of": "2026-11-25T00:00:00Z",
        "profile": "SYNTHETIC_TEST",
        "example_only": True,
        "sources": {"head": {"sha": head, "tree": tree}, "merge": {"sha": merge, "tree": tree}},
        "runs": {
            "head": {"application": 101, "supply-chain": 102},
            "merge": {"application": 201, "supply-chain": 202},
        },
    }
    put_json(bundle, "index-input.json", request)
    for slot in index.SLOTS:
        identity = request["sources"][slot]
        for kind, run_id in request["runs"][slot].items():
            put_json(
                bundle,
                f"{slot}-{kind}-run.json",
                {
                    "id": run_id,
                    "head_sha": identity["sha"],
                    "repository": {"full_name": index.REPOSITORY},
                    "path": index.WORKFLOWS[kind],
                    "event": "pull_request" if slot == "head" else "push",
                    "status": "completed",
                    "conclusion": "success",
                    "run_attempt": 1,
                },
            )
            jobs = [
                {
                    "id": run_id * 10 + i,
                    "run_id": run_id,
                    "head_sha": identity["sha"],
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                }
                for i, name in enumerate(index.JOBS[kind])
            ]
            put_json(bundle, f"{slot}-{kind}-jobs.json", {"total_count": len(jobs), "jobs": jobs})
        log = (
            identity["sha"] + '\n{"required_evaluation_foundation_tests": 3, "required_skips": 0}\n'
            '{"required_modules_executed": {"test_fixture_postgresql_integration": 2}, '
            '"required_skips": 0}\n'
            "{'alembic_head': [('0001_fixture',)]}\n"
        ).encode()
        put(bundle, slot + "-python.log", log)
        run_id = request["runs"][slot]["supply-chain"]
        proof = {
            "source": {"commit": identity["sha"], "tree": tree},
            "alembic_head": "0001_fixture",
            "profile": "SYNTHETIC_TEST",
            "status": "PASS",
        }
        config = {"schema": index.MANIFEST_SCHEMA, "environment": "test"}
        manifest = {
            "schema": index.MANIFEST_SCHEMA,
            "SOURCE_IDENTITY": proof["source"],
            "CONFIGURATION_IDENTITY": {
                "value": config,
                "sha256": index.sha(index.canonical(config)),
                "alembic_head": "0001_fixture",
            },
            "TEST_EVIDENCE": {
                "evidence_sha256": index.sha(index.canonical(proof)),
                "supply_chain_run": str(run_id),
                "supply_chain_attempt": "1",
                "classification": "technical execution evidence",
            },
            "CONTENT_APPROVAL": {
                "BERLIN_FORM_APPROVAL": "NOT VERIFIED",
                "REAL_FORM_MANIFEST": "UNAVAILABLE",
                "CONTENT_PUBLICATION": "NOT AUTHORIZED",
            },
            "OPERATIONAL_AUTHORIZATION": {
                p: {"status": "PASS" if p == "SYNTHETIC_TEST" else "NOT_AUTHORIZED"}
                for p in ["SYNTHETIC_TEST", "STAGING_PROPOSED", "REAL_PILOT"]
            },
        }
        meta = []
        for i, kind in enumerate(["containers", "language"]):
            summary = {
                "source_sha": identity["sha"],
                "scope": "container" if kind == "containers" else "language",
                "status": "COMPLETED",
            }
            files = {"summary.json": index.canonical(summary)}
            if kind == "containers":
                files.update(
                    {
                        "v1-e06a-release-manifest.json": index.canonical(manifest),
                        "v1-e06a-rehearsal.json": index.canonical(proof),
                        "v1-e06a-release-manifest.sha256": (
                            index.sha(index.canonical(manifest))
                            + "  v1-e06a-release-manifest.json\n"
                        ).encode(),
                    }
                )
            data = packed(files)
            put(bundle, f"{slot}-{kind}.zip", data)
            meta.append(
                {
                    "id": run_id * 10 + i,
                    "name": f"o1a-{kind}-{identity['sha']}",
                    "workflow_run": {"id": run_id, "head_sha": identity["sha"]},
                    "digest": "sha256:" + index.sha(data),
                    "expired": False,
                    "created_at": "2026-11-24T00:00:00Z",
                    "expires_at": "2026-12-24T00:00:00Z",
                }
            )
        put_json(bundle, slot + "-artifacts.json", {"total_count": 2, "artifacts": meta})
    return bundle, source


def generate(evidence):
    bundle, source = evidence
    reader = index.Bundle(bundle)
    try:
        return index.generate(reader, index.Source(source))
    finally:
        reader.close()


def mutate(evidence, name, function):
    root, _ = evidence
    value = get(root, name)
    function(value)
    put_json(root, name, value)


def archive_mutation(evidence, slot, function, kind="containers"):
    root, _ = evidence
    name = f"{slot}-{kind}.zip"
    files = unpacked(root / name)
    function(files)
    data = packed(files)
    put(root, name, data)
    meta = get(root, slot + "-artifacts.json")
    next(v for v in meta["artifacts"] if v["name"].startswith("o1a-" + kind))["digest"] = (
        "sha256:" + index.sha(data)
    )
    put_json(root, slot + "-artifacts.json", meta)


def change_manifest(files, function, checksum=True):
    data = json.loads(files["v1-e06a-release-manifest.json"])
    function(data)
    files["v1-e06a-release-manifest.json"] = index.canonical(data)
    if checksum:
        files["v1-e06a-release-manifest.sha256"] = (
            index.sha(index.canonical(data)) + "  v1-e06a-release-manifest.json\n"
        ).encode()


def test_valid_deterministic_redacted_projection_does_not_modify_inputs(evidence, tmp_path):
    bundle, source = evidence
    before = {p.name: index.sha(p.read_bytes()) for p in bundle.iterdir()}
    original_status = git(source, "status", "--porcelain")
    first, second = generate(evidence), generate(evidence)
    assert index.canonical(first) == index.canonical(second)
    assert index.markdown(first) == index.markdown(second)
    assert first["github_authenticity"] == first["security_acceptance"] == "NOT VERIFIED"
    assert first["production_readiness"] == "NO" and first["pilot_launch"] == "NOT AUTHORIZED"
    for revision in first["revisions"]:
        assert revision["required_integrations"] == {
            "verification": "LOCAL_PARSED",
            "report_count": 2,
            "expected_report_count": 2,
            "required_skips": 0,
            "applied_migration_head": "0001_fixture",
        }
        assert revision["release"]["verification"] == "LOCAL_CHECKSUMS_MATCHED"
        assert all(a["availability"] == "LOCAL_AVAILABLE" for a in revision["artifacts"])
    for name in ["out1", "out2"]:
        index.publish(tmp_path / name, bundle, source, first)
    assert (tmp_path / "out1/release-evidence-index.sha256").read_bytes() == (
        tmp_path / "out2/release-evidence-index.sha256"
    ).read_bytes()
    assert before == {p.name: index.sha(p.read_bytes()) for p in bundle.iterdir()}
    assert git(source, "status", "--porcelain") == original_status
    for line in (tmp_path / "out1/release-evidence-index.sha256").read_text().splitlines():
        digest, name = line.split()
        assert hashlib.sha256((tmp_path / "out1" / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unknown/2"),
        ("repository", "other/repository"),
        ("profile", "REAL_PILOT"),
        ("as_of", "yesterday"),
        ("example_only", "yes"),
        ("authorization", "ACCEPTED"),
    ],
)
def test_unsupported_input_rejected_without_authorization(evidence, field, value):
    mutate(evidence, "index-input.json", lambda d: d.update({field: value}))
    with pytest.raises((index.EvidenceError, ValueError)):
        generate(evidence)


@pytest.mark.parametrize(
    "field,value",
    [("sha", "not-a-sha"), ("sha", "0" * 40), ("tree", "a" * 40), ("tree", "../outside")],
)
def test_source_identity_rejection(evidence, field, value):
    mutate(evidence, "index-input.json", lambda d: d["sources"]["head"].update({field: value}))
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_run",
        "head_as_merge",
        "wrong_event",
        "wrong_sha",
        "wrong_workflow",
        "wrong_repository",
        "inconsistent_conclusion",
    ],
)
def test_conflicting_ci_rejected(evidence, case):
    if case == "duplicate_run":
        mutate(evidence, "index-input.json", lambda d: d["runs"]["merge"].update(application=101))
    elif case == "head_as_merge":
        mutate(
            evidence, "index-input.json", lambda d: d["sources"].update(merge=d["sources"]["head"])
        )
    else:
        updates = {
            "wrong_event": {"event": "push"},
            "wrong_sha": {"head_sha": "f" * 40},
            "wrong_workflow": {"path": ".github/workflows/other.yml"},
            "wrong_repository": {"repository": {"full_name": "other/repository"}},
            "inconsistent_conclusion": {"status": "in_progress"},
        }
        mutate(evidence, "head-application-run.json", lambda d: d.update(updates[case]))
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize(
    "case", ["duplicate", "wrong_run", "wrong_sha", "unknown", "false_success"]
)
def test_job_identity_and_success_consistency(evidence, case):
    def change(d):
        if case == "duplicate":
            d["jobs"].append(copy.deepcopy(d["jobs"][0]))
            d["total_count"] += 1
        else:
            d["jobs"][0].update(
                {
                    "wrong_run": {"run_id": 999},
                    "wrong_sha": {"head_sha": "b" * 40},
                    "unknown": {"name": "unknown-job"},
                    "false_success": {"conclusion": "failure"},
                }[case]
            )

    mutate(evidence, "head-application-jobs.json", change)
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize("case", ["missing", "expired_flag", "expired_date", "missing_metadata"])
def test_missing_and_expired_artifacts_never_pass(evidence, case):
    root, _ = evidence
    if case == "missing":
        (root / "head-containers.zip").unlink()
    elif case == "missing_metadata":
        (root / "head-artifacts.json").unlink()
    else:
        mutate(
            evidence,
            "head-artifacts.json",
            lambda d: d["artifacts"][0].update(
                {"expired": True}
                if case == "expired_flag"
                else {"expires_at": "2026-11-24T00:00:00Z"}
            ),
        )
    result = generate(evidence)["revisions"][0]
    assert result["artifacts"][0]["availability"] == "UNAVAILABLE"
    assert result["artifacts"][0]["verification"] == "NOT VERIFIED"
    assert result["release"]["verification"] == "NOT VERIFIED"


@pytest.mark.parametrize("case", ["digest", "name", "run", "duplicate", "checksum"])
def test_artifact_binding_rejections(evidence, case):
    root, _ = evidence
    if case == "checksum":
        put(root, "head-containers.zip", b"wrong archive")
    else:

        def change(d):
            if case == "duplicate":
                d["artifacts"].append(copy.deepcopy(d["artifacts"][0]))
            else:
                d["artifacts"][0].update(
                    {
                        "digest": {"digest": "not-sha256"},
                        "name": {"name": "o1a-containers-" + "a" * 40},
                        "run": {"workflow_run": {"id": 999, "head_sha": "a" * 40}},
                    }[case]
                )

        mutate(evidence, "head-artifacts.json", change)
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize(
    "case",
    [
        "schema",
        "source",
        "tree",
        "run",
        "rehearsal_hash",
        "config_hash",
        "migration",
        "human_approval",
        "official_content",
        "manifest_hash",
    ],
)
def test_nested_e06a_integrity_rejections(evidence, case):
    def alter(d):
        if case == "schema":
            d["schema"] = "unsupported/2"
        if case == "source":
            d["SOURCE_IDENTITY"]["commit"] = "a" * 40
        if case == "tree":
            d["SOURCE_IDENTITY"]["tree"] = "a" * 40
        if case == "run":
            d["TEST_EVIDENCE"]["supply_chain_run"] = "999"
        if case == "rehearsal_hash":
            d["TEST_EVIDENCE"]["evidence_sha256"] = "a" * 64
        if case == "config_hash":
            d["CONFIGURATION_IDENTITY"]["sha256"] = "a" * 64
        if case == "migration":
            d["CONFIGURATION_IDENTITY"]["alembic_head"] = "0002_other"
        if case == "human_approval":
            d["OPERATIONAL_AUTHORIZATION"]["REAL_PILOT"]["status"] = "PASS"
        if case == "official_content":
            d["CONTENT_APPROVAL"]["CONTENT_PUBLICATION"] = "AUTHORIZED"
        if case == "manifest_hash":
            d["TEST_EVIDENCE"]["classification"] = "changed"

    archive_mutation(
        evidence,
        "head",
        lambda files: change_manifest(files, alter, checksum=case != "manifest_hash"),
    )
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize("kind", ["containers", "language"])
def test_archive_summary_source_must_match(evidence, kind):
    archive_mutation(
        evidence,
        "head",
        lambda files: files.update(
            {
                "summary.json": index.canonical(
                    {"source_sha": "f" * 40, "scope": "language", "status": "COMPLETED"}
                )
            }
        ),
        kind,
    )
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize(
    "case",
    ["duplicate", "conflict", "unknown", "missing", "wrong_source", "multiple_heads", "malformed"],
)
def test_required_report_integrity_and_partial_status(evidence, case):
    root, _ = evidence
    data = (root / "head-python.log").read_bytes()
    extra = {
        "duplicate": b'{"required_evaluation_foundation_tests": 3, "required_skips": 0}\n',
        "conflict": b'{"required_evaluation_foundation_tests": 7, "required_skips": 0}\n',
        "unknown": b'{"required_other_tests": 1, "required_skips": 0}\n',
        "multiple_heads": b"{'alembic_head': [('0001_fixture',)]}\n",
        "malformed": b'{"required_skips": 0\n',
    }
    if case == "missing":
        put(root, "head-python.log", b"\n".join(data.splitlines()[:2]))
        proof = generate(evidence)["revisions"][0]["required_integrations"]
        assert proof["verification"] == "NOT VERIFIED" and proof["required_skips"] is None
        return
    if case == "wrong_source":
        data = data.replace(
            get(root, "index-input.json")["sources"]["head"]["sha"].encode(), b"b" * 40
        )
    else:
        data += extra[case]
    put(root, "head-python.log", data)
    with pytest.raises(index.EvidenceError):
        generate(evidence)


def test_required_skips_cannot_be_combined_with_successful_run(evidence):
    root, _ = evidence
    data = (
        (root / "head-python.log")
        .read_bytes()
        .replace(b'"required_skips": 0', b'"required_skips": 1', 1)
    )
    put(root, "head-python.log", data)
    with pytest.raises(index.EvidenceError):
        generate(evidence)
    mutate(evidence, "head-application-run.json", lambda d: d.update(conclusion="failure"))
    mutate(
        evidence, "head-application-jobs.json", lambda d: d["jobs"][0].update(conclusion="failure")
    )
    assert generate(evidence)["revisions"][0]["required_integrations"]["required_skips"] == 1


def test_draft_has_no_invented_post_merge_evidence(evidence):
    root, _ = evidence
    mutate(
        evidence,
        "index-input.json",
        lambda d: (d["sources"].update(merge=None), d["runs"].update(merge=None)),
    )
    for path in root.glob("merge-*"):
        path.unlink()
    value = generate(evidence)
    assert value["revisions"][1] == {
        "scope": "POST_MERGE",
        "source": None,
        "verification": "NOT VERIFIED",
    }
    assert b"No merge is asserted" in index.markdown(value)


def test_missing_workflow_never_becomes_verified(evidence):
    root, _ = evidence
    for suffix in ["run", "jobs"]:
        (root / f"head-application-{suffix}.json").unlink()
    (root / "head-python.log").unlink()
    run = generate(evidence)["revisions"][0]["workflows"][0]
    assert run["verification"] == "NOT VERIFIED" and run["reported_conclusion"] is None


@pytest.mark.parametrize("case", ["malformed", "duplicate_key", "encoding"])
def test_malformed_json_fail_closed(evidence, case):
    root, _ = evidence
    data = {
        "malformed": b'{"schema":',
        "duplicate_key": b'{"schema":"one", "schema":"two"}',
        "encoding": b"\xff",
    }[case]
    put(root, "index-input.json", data)
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize("case", ["symlink", "hardlink", "fifo", "public_root", "symlink_root"])
def test_unsafe_bundle_paths_are_refused(evidence, tmp_path, case):
    root, source = evidence
    if case == "public_root":
        root.chmod(0o755)
        with pytest.raises(index.EvidenceError):
            generate(evidence)
    elif case == "symlink_root":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        with pytest.raises(index.EvidenceError):
            index.Bundle(alias)
    else:
        target = root / "head-python.log"
        target.unlink()
        if case == "symlink":
            target.symlink_to(tmp_path / "outside")
        if case == "hardlink":
            os.link(source / "README.md", target)
        if case == "fifo":
            os.mkfifo(target)
        with pytest.raises(index.EvidenceError):
            generate(evidence)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "folder/file", ".."])
def test_unsafe_archive_members_are_rejected(evidence, name):
    archive_mutation(evidence, "head", lambda files: files.update({name: b"never extracted"}))
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize("location", ["bundle", "repository", "existing", "symlink_parent"])
def test_output_cannot_overwrite_inputs_or_existing_state(evidence, tmp_path, location):
    root, source = evidence
    value = generate(evidence)
    paths = {
        "bundle": root / "output",
        "repository": source / "output",
        "existing": tmp_path / "existing",
        "symlink_parent": tmp_path / "alias/output",
    }
    (tmp_path / "existing").mkdir()
    (tmp_path / "existing/sentinel").write_text("unchanged")
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    before = (root / "index-input.json").read_bytes()
    with pytest.raises(index.EvidenceError):
        index.publish(paths[location], root, source, value)
    assert (root / "index-input.json").read_bytes() == before
    assert (tmp_path / "existing/sentinel").read_text() == "unchanged"


def test_private_canary_negative_control_and_strict_output_projection(evidence):
    root, _ = evidence
    canary = secrets.token_urlsafe(32)

    def detector(data):
        if canary.encode() in data:
            raise AssertionError("unsafe canary detected")

    with pytest.raises(AssertionError, match="unsafe canary detected"):
        detector(index.canonical({"token": canary, "candidate_name": canary}))
    for name in ["head-application-run.json", "head-application-jobs.json", "head-artifacts.json"]:
        mutate(
            evidence,
            name,
            lambda d: d.update(
                candidate_name=canary,
                bearer_token=canary,
                answers=[canary],
                storage_key=canary,
                private_url=canary,
                credentials=canary,
            ),
        )
    archive_mutation(
        evidence,
        "head",
        lambda f: change_manifest(
            f, lambda d: d.update(candidate_name=canary, answer_text=canary, storage_key=canary)
        ),
    )
    value = generate(evidence)
    detector(index.canonical(value))
    detector(index.markdown(value))
    assert all(
        status == "OPEN"
        for gate, status in value["decisions"].items()
        if gate not in {"E03A", "E05-historical-timeout"}
    )
    assert value["decisions"]["E03A"] == "BLOCKED"


def private_process(arguments, environment):
    __tracebackhide__ = True
    try:
        return subprocess.run(
            arguments, env=environment, capture_output=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        raise AssertionError("private diagnostic subprocess did not complete") from None


def require_safe_cli_failure(result, canary):
    __tracebackhide__ = True
    if canary.encode() in result.stdout + result.stderr:
        raise AssertionError("diagnostic exposed input")
    if result.returncode != 2:
        raise AssertionError("expected controlled CLI rejection")
    if result.stderr != b"EVIDENCE_INDEX_REJECTED: invalid or inconsistent local evidence\n":
        raise AssertionError("unexpected CLI diagnostic category")


def test_cli_failure_diagnostics_never_echo_secret_or_path(evidence, tmp_path):
    root, source = evidence
    canary = secrets.token_urlsafe(32)
    put(root, "index-input.json", ('{"token":"' + canary).encode())
    result = private_process(
        [
            sys.executable,
            str(SCRIPT),
            "--bundle",
            str(root),
            "--repository",
            str(source),
            "--output",
            str(tmp_path / canary),
        ],
        {"PATH": os.defpath},
    )
    require_safe_cli_failure(result, canary)
    if (tmp_path / canary).exists():
        raise AssertionError("rejected invocation created output")


def test_ordering_and_canonical_revision_links(evidence):
    root, source = evidence
    mutate(evidence, "index-input.json", lambda d: d.update(example_only=False))
    first = generate(evidence)
    for slot in index.SLOTS:
        for kind in index.WORKFLOWS:
            mutate(evidence, f"{slot}-{kind}-jobs.json", lambda d: d["jobs"].reverse())
        mutate(evidence, slot + "-artifacts.json", lambda d: d["artifacts"].reverse())
    second = generate(evidence)
    assert index.canonical(first) == index.canonical(second)
    for url in re.findall(r"\]\((https://[^)]+)\)", index.markdown(first).decode()):
        parsed = urlsplit(url)
        assert parsed.netloc == "github.com" and parsed.path.startswith(
            "/" + index.REPOSITORY + "/"
        )
        relative = parsed.path.split(index.REPOSITORY + "/", 1)[1]
        if relative.startswith("blob/"):
            _, sha, path = relative.split("/", 2)
            assert git(source, "show", sha + ":" + path)
        elif relative.startswith("commit/"):
            assert git(source, "rev-parse", relative.split("/", 1)[1] + "^{commit}")
        else:
            assert re.fullmatch(r"actions/runs/\d+(?:/(?:artifacts|job)/\d+)?", relative)


def test_partial_clone_cannot_fetch_missing_evidence_objects(evidence):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    root, source = evidence
    requests = []

    class MissingObject(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(True)
            self.send_error(404)

    server = HTTPServer(("127.0.0.1", 0), MissingObject)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        git(source, "config", "remote.origin.url", f"http://127.0.0.1:{server.server_port}/fixture")
        git(source, "config", "remote.origin.promisor", "true")
        git(source, "config", "remote.origin.partialclonefilter", "blob:none")
        head = get(root, "index-input.json")["sources"]["head"]["sha"]
        blob = git(source, "rev-parse", head + ":README.md")
        (source / ".git/objects" / blob[:2] / blob[2:]).unlink()
        # Unsafe control proves Git attempts a lazy fetch without the boundary.
        control = subprocess.run(
            ["git", "-C", str(source), "show", head + ":README.md"],
            env=ENV | {"GIT_TERMINAL_PROMPT": "0"},
            capture_output=True,
            check=False,
            timeout=15,
        )
        assert control.returncode != 0 and bool(requests)
        requests.clear()
        with pytest.raises(index.EvidenceError, match="SOURCE_UNAVAILABLE"):
            index.Source(source).file(head, "README.md")
        assert requests == []
        assert not (source / ".git/objects" / blob[:2] / blob[2:]).exists()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("case", ["duplicate_entry", "symlink_entry"])
def test_zip_duplicate_and_symlink_members_are_refused(evidence, case):
    root, _ = evidence
    files = unpacked(root / "head-containers.zip")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        info = zipfile.ZipInfo("summary.json" if case == "duplicate_entry" else "symlink")
        info.create_system = 3
        if case == "duplicate_entry":
            info.external_attr = 0o100600 << 16
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(info, files["summary.json"])
        else:
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"outside")
    data = buffer.getvalue()
    put(root, "head-containers.zip", data)
    mutate(
        evidence,
        "head-artifacts.json",
        lambda d: d["artifacts"][0].update(digest="sha256:" + index.sha(data)),
    )
    with pytest.raises(index.EvidenceError):
        generate(evidence)


def test_privacy_wrapper_is_inspected_without_execution(evidence):
    _, source = evidence
    workflow = source / ".github/workflows/ci.yml"
    workflow.write_bytes(
        workflow.read_bytes() + b'python scripts/run-privacy-integration.py "fixture.xml"\n'
    )
    put(
        source,
        "scripts/run-privacy-integration.py",
        b'raise AssertionError("must not execute")\n'
        b'module = "test_privacy_postgresql_minio_integration"\n',
    )
    git(source, "add", ".")
    git(source, "commit", "-qm", "synthetic wrapper")
    revision = git(source, "rev-parse", "HEAD")
    signatures = index.Source(source).report_signatures(revision)
    assert len(signatures) == 3
    assert ("modules", "test_privacy_postgresql_minio_integration") in signatures


def test_committed_example_is_synthetic_deterministic_and_has_no_fake_links(evidence):
    expected = index.markdown(generate(evidence))
    assert (ROOT / "docs/releases/v1-evidence-index/example.md").read_bytes() == expected
    assert b"EXAMPLE_ONLY" in expected and b"](https://" not in expected


def test_reviewer_documentation_links_resolve_inside_repository():
    for relative in [
        "docs/releases/v1-evidence-index/README.md",
        "docs/releases/v1-evidence-index/example.md",
    ]:
        document = ROOT / relative
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("https://", "#")):
                continue
            file = (document.parent / target.split("#", 1)[0]).resolve()
            assert file.is_relative_to(ROOT) and file.is_file()
    assert "docs/releases/v1-evidence-index/README.md" in (ROOT / "README.md").read_text()


@pytest.mark.parametrize("identifier", [True, 1.0, "1"])
def test_raw_workflow_ids_are_not_coerced(evidence, identifier):
    mutate(evidence, "index-input.json", lambda d: d["runs"]["head"].update(application=1))
    mutate(evidence, "head-application-run.json", lambda d: d.update(id=identifier))
    with pytest.raises(index.EvidenceError):
        generate(evidence)


def test_truncated_artifact_listing_is_not_verified(evidence):
    mutate(evidence, "head-artifacts.json", lambda d: d.update(total_count=3))
    with pytest.raises(index.EvidenceError):
        generate(evidence)


@pytest.mark.parametrize("arguments", [["--unknown"], ["--output"]])
def test_argument_parser_does_not_echo_unknown_credentials(arguments):
    canary = secrets.token_urlsafe(32)
    code = """
import importlib.util,os,sys
spec=importlib.util.spec_from_file_location('private_index',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sys.argv=['private-'+os.environ['CANARY'],*sys.argv[2:],os.environ['CANARY']]
raise SystemExit(m.main())
"""
    result = private_process(
        [sys.executable, "-c", code, str(SCRIPT), *arguments],
        {"PATH": os.defpath, "CANARY": canary},
    )
    require_safe_cli_failure(result, canary)


def test_git_timeout_diagnostic_does_not_echo_command_paths(evidence, tmp_path):
    root, source = evidence
    canary = secrets.token_urlsafe(32)
    unsafe = subprocess.TimeoutExpired(["git", canary], 15)
    if canary not in str(unsafe):
        raise AssertionError("unsafe diagnostic control did not execute")
    code = """
import importlib.util,os,subprocess,sys
script,bundle,source,output=sys.argv[1:]
spec=importlib.util.spec_from_file_location('private_index',script)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def timeout(*_a,**_k): raise subprocess.TimeoutExpired(['git',os.environ['CANARY']],15)
m.Source.git=timeout
sys.argv=['tool','--bundle',bundle,'--repository',source,'--output',output]
raise SystemExit(m.main())
"""
    result = private_process(
        [sys.executable, "-c", code, str(SCRIPT), str(root), str(source), str(tmp_path / canary)],
        {"PATH": os.defpath, "CANARY": canary},
    )
    require_safe_cli_failure(result, canary)
    if (tmp_path / canary).exists():
        raise AssertionError("rejected invocation created output")


def test_forced_parser_regression_fails_without_public_canary(tmp_path):
    __tracebackhide__ = True
    canary = secrets.token_urlsafe(32)
    plugin = """
def pytest_runtest_call(item):
 if "test_argument_parser_does_not_echo_unknown_credentials" in item.name:
  item.module.secrets.token_urlsafe=lambda *_a: CANARY
  original=item.module.private_process
  def unsafe(arguments,environment):
   args=list(arguments)
   args[2]=args[2].replace("raise SystemExit(m.main())",
       "import argparse; m.SafeParser=argparse.ArgumentParser; raise SystemExit(m.main())")
   return original(args,environment)
  item.module.private_process=unsafe
""".replace("CANARY", repr(canary))
    control = tmp_path / "private_d01_control.py"
    control.write_text(plugin)
    control.chmod(0o600)
    result = private_process(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__).resolve()),
            "-k",
            "test_argument_parser_does_not_echo_unknown_credentials",
            "-p",
            "private_d01_control",
            "-p",
            "no:cacheprovider",
            "-q",
            "--basetemp=" + str(tmp_path / "child-pytest"),
        ],
        {
            "PATH": os.defpath,
            "PYTHONPATH": str(tmp_path) + os.pathsep + str(ROOT / "services/api"),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if result.returncode == 0 or b"2 failed" not in result.stdout:
        raise AssertionError("unsafe parser control was not rejected by both probes")
    if canary.encode() in result.stdout + result.stderr:
        raise AssertionError("forced-regression diagnostic escaped private capture")
