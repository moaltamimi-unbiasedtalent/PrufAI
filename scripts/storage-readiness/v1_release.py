"""E06A release identity, bounded preflight and disposable stop/restore proof."""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from app.config import Settings
from app.models import CandidateExamAssignment
from app.operations.readiness import (
    CANARY_BYTES,
    CANARY_KEY,
    measure_readiness,
    measure_storage,
)
from check import POSTGRES, ROOT, Database, Rig, Storage, provenance, run, sha
from operations import (
    AUDIO_USER,
    REDIS,
    WORKERS,
    call,
    end,
    eventually,
    launch_api,
    launch_redis,
    port,
    settings,
    sign_in,
)
from ops_recovery import (
    recover,
    seed_domains,
    session_for,
    snapshot,
    verify_restored_standards,
)
from recovery import assert_private, seed

PREVIOUS = "00e29905d60c0f0bae3173992c71428fc8b89303"
ACTIVE = "BERLIN_LID_SYNTHETIC"
STOPPED = "BERLIN_LID_LOCKED"
SCHEMA = "prufai-v1-e06a/1"
PROFILES = ("SYNTHETIC_TEST", "STAGING_PROPOSED", "REAL_PILOT")
CANDIDATE = "/api/v1/candidate"
CANDIDATE_EMAIL = "candidate@demo.prufai.local"
ADMIN = "admin@demo.prufai.local"
TABLES = ("candidate_exam_assignments", "lid_practice_forms", "statements_of_results")
JOBS = (
    "speech_transcription_jobs",
    "writing_execution_jobs",
    "speaking_execution_jobs",
    "ai_runs",
)
CONTENT = {
    "BERLIN_FORM_APPROVAL": "NOT VERIFIED",
    "REAL_FORM_MANIFEST": "UNAVAILABLE",
    "CONTENT_PUBLICATION": "NOT AUTHORIZED",
    "synthetic_form_classification": "TEST_ONLY",
}
GATES = (
    "H01_product_signoff",
    "approved_berlin_form",
    "source_rights",
    "independent_review",
    "privacy_and_retention",
    "approved_real_delivery",
    "manual_human_uat",
    "operational_ownership",
    "approved_hosting_and_storage",
    "security_disposition",
    "incident_escalation",
)


def canonical(data):
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def authorization(profile):
    assert profile in PROFILES
    return {
        "profile": profile,
        "status": "PASS" if profile == "SYNTHETIC_TEST" else "NOT_AUTHORIZED",
        "scope": "isolated disposable containers only",
        "real_launch_gates": dict.fromkeys(GATES, "NOT_VERIFIED"),
        "pilot_config": ACTIVE if profile == "SYNTHETIC_TEST" else STOPPED,
    }


def git(*args):
    return run("git", *args).decode().strip()


def source_identity():
    return {
        "repository": "moaltamimi-unbiasedtalent/PrufAI",
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "rollback_commit": PREVIOUS,
        "rollback_tree": git("rev-parse", PREVIOUS + "^{tree}"),
    }


def versions():
    return {
        "api": tomllib.loads((ROOT / "services/api/pyproject.toml").read_text())[
            "project"
        ]["version"],
        "ai": tomllib.loads((ROOT / "services/ai/pyproject.toml").read_text())[
            "project"
        ]["version"],
        "web": json.loads((ROOT / "apps/web/package.json").read_text())["version"],
        "python": sys.version.split()[0],
        "node": run("node", "--version").decode().strip(),
    }


def image_identity(rig, image):
    obj = rig.inspect("image", image)
    return {"id": obj["Id"], "repo_digests": sorted(obj.get("RepoDigests") or [])}


def package_files(rig, decoder, scratch, web_env):
    paths = (
        "apps/web/package-lock.json",
        "apps/web/next.config.ts",
        "services/api/pyproject.toml",
        "services/ai/pyproject.toml",
        "scripts/storage-readiness/v1_release.py",
    )
    checksums = {path: sha((ROOT / path).read_bytes()) for path in paths}
    clean_source = scratch / "package-source"
    extract_tree(
        ROOT, git("rev-parse", "HEAD"), clean_source, "services/api", "services/ai"
    )
    for app in ("api", "ai"):
        destination = scratch / app
        run(
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(destination),
            str(clean_source / "services" / app),
            timeout=120,
            env={
                **web_env,
                "SOURCE_DATE_EPOCH": git("show", "-s", "--format=%ct", "HEAD"),
            },
        )
        (wheel,) = destination.glob("*.whl")
        checksums[wheel.name] = sha(wheel.read_bytes())
        run(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(scratch / "runtime" / "services" / app),
            str(wheel),
            timeout=120,
            env=web_env,
        )
    web = ROOT / "apps/web"
    run("npm", "run", "build", cwd=web, env=web_env, timeout=420)
    build = web / ".next"
    checksums["web-build-id"] = sha((build / "BUILD_ID").read_bytes())
    checksums.update(web_checksums(build))
    hashes = (
        rig.docker(
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--entrypoint",
            "/usr/bin/sha256sum",
            decoder,
            "/usr/bin/ffmpeg",
            "/usr/bin/ffprobe",
        )
        .decode()
        .splitlines()
    )
    checksums.update({Path(row.split()[1]).name: row.split()[0] for row in hashes})
    checksums["guarded-storage-provenance"] = sha(canonical(rig.evidence["provenance"]))
    return {
        "versions": versions(),
        "images": {
            "guarded_minio": image_identity(rig, rig.storage_image),
            "decoder": image_identity(rig, decoder),
            "postgresql": image_identity(rig, POSTGRES),
            "redis": image_identity(rig, REDIS),
        },
        "guarded_storage": rig.evidence["provenance"]["binaries"],
        "artifact_sha256": checksums,
        "checkout_clean_at_start": True,
    }


def web_checksums(build):
    checksums = {}
    for area in ("server", "static"):
        tree = {
            path.relative_to(build).as_posix(): sha(path.read_bytes())
            for path in sorted((build / area).rglob("*"))
            if path.is_file()
        }
        assert tree, area
        checksums["web-" + area] = sha(canonical(tree))
    return checksums


def configuration():
    return {
        "schema": SCHEMA,
        "environment": "test",
        "application_profile": ACTIVE,
        "stop_profile": STOPPED,
        "storage": "guarded-private-s3-with-read-only-canary",
        "rate_limit": "redis",
        "enrollment_delivery": "DISABLED",
        "external_providers": "DISABLED",
        "certification": "DISABLED",
        "secrets": "generated in memory per disposable run",
    }


def preflight(
    env, database, storage, api_url, web_url, *, expected_head, expected_source, build
):
    # Check only this rehearsal's scoped targets with bounded read-only queries.
    config = Settings(
        _env_file=None,
        **{
            k.lower(): v
            for k, v in env.items()
            if k.lower() in Settings.model_fields and k != "V1_PILOT_PROFILE"
        },
    )
    readiness = measure_readiness(config)
    capability = measure_storage(config)
    assert_private(storage)
    rows = (
        database.sql(
            "BEGIN READ ONLY; SET LOCAL statement_timeout = '1500ms'; "
            "SELECT version_num FROM alembic_version ORDER BY version_num; COMMIT"
        )
        .decode()
        .splitlines()
    )
    revisions = [row for row in rows if row not in {"BEGIN", "SET", "COMMIT"}]
    with httpx.Client(timeout=4.5, trust_env=False) as client:
        liveness = client.get(api_url + "/health")
        ready = client.get(api_url + "/ready")
        web = client.get(web_url + "/login")
    return {
        "source_identity": status(source_identity() == expected_source),
        "build_identity": status(
            (ROOT / "apps/web/.next/BUILD_ID").read_text().strip()
            == expected_source["commit"]
            and sha((ROOT / "apps/web/.next/BUILD_ID").read_bytes())
            == build["artifact_sha256"]["web-build-id"]
            and web_checksums(ROOT / "apps/web/.next")["web-static"]
            == build["artifact_sha256"]["web-static"]
        ),
        "configuration": status(
            config.environment == "test"
            and env["V1_PILOT_PROFILE"] == ACTIVE
            and config.auth_enrollment_delivery_mode == "DISABLED"
            and config.auth_rate_limit_backend == "redis"
            and not config.certification_issuance_enabled
            and config.object_storage_backend == "s3"
            and config.audio_media_runtime == "docker"
            and config.object_storage_endpoint == storage.endpoint
            and config.database_url == database.url
        ),
        "credential_source": status(
            len(env["AUTH_JWT_SECRET"]) >= 64
            and env["OBJECT_STORAGE_SECRET_KEY"] == storage.secret
            and "PRUFAI_DEMO_PASSWORD" not in env
        ),
        "postgresql": status(readiness.postgres.status == "HEALTHY"),
        "redis": status(readiness.redis.status == "HEALTHY"),
        "migration": status(revisions == [expected_head]),
        "storage": status(
            capability.health == "HEALTHY" and capability.canary == "VERIFIED"
        ),
        "private_storage": "PASS",
        "worker_configuration": status(
            all(
                not getattr(config, flag)
                for flag in (
                    "speech_transcription_test_adapter_enabled",
                    "writing_test_adapter_enabled",
                    "speaking_test_adapter_enabled",
                    "speech_transcription_approved_profile_fingerprints",
                    "writing_approved_profile_fingerprints",
                    "speaking_approved_profile_fingerprints",
                    "knowledge_embedding_api_key",
                    "knowledge_reranker_api_key",
                )
            )
        ),
        "backup_restore_prerequisites": status(
            bool(
                database.rig.docker("exec", database.container, "pg_dump", "--version")
            )
            and bool(
                database.rig.docker(
                    "exec", database.container, "pg_restore", "--version"
                )
            )
        ),
        "api_liveness": status(liveness.status_code == 200),
        "api_readiness": status(ready.status_code == 200),
        "web_sign_in": status(web.status_code == 200),
        "public_probe_no_credentials": status(
            all(
                value.encode() not in liveness.content + ready.content
                for value in (
                    storage.key,
                    storage.secret,
                    env["AUTH_JWT_SECRET"],
                    database.url,
                    env["REDIS_URL"],
                )
            )
        ),
        "worker_liveness": "NOT_VERIFIED",
        "production_tls": "NOT_VERIFIED",
        "production_iam": "NOT_VERIFIED",
        "encrypted_backup": "NOT_VERIFIED",
        "alert_delivery": "NOT_CONFIGURED",
    }


def status(condition):
    return "PASS" if condition else "FAIL"


def canary(storage):
    client = storage.operator()
    version = client.put_object(
        Bucket=storage.bucket, Key=CANARY_KEY, Body=CANARY_BYTES
    )["VersionId"]

    def remove():
        client.delete_object(Bucket=storage.bucket, Key=CANARY_KEY, VersionId=version)

    return remove


def start_web(stack, env, listener):
    process = subprocess.Popen(
        (
            "node",
            "node_modules/next/dist/bin/next",
            "start",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(listener),
        ),
        cwd=ROOT / "apps/web",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    stack.callback(end, process)
    url = f"http://127.0.0.1:{listener}"

    def ready():
        assert process.poll() is None, "production Web process exited"
        try:
            return (
                httpx.get(url + "/login", timeout=0.5, trust_env=False).status_code
                == 200
            )
        except httpx.TransportError:
            return False

    eventually(ready, seconds=60)
    return process, url


def authenticate(client, email, password, organisation):
    # Operate through the production Web proxy and keep credentials in memory.
    response = call(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    return {
        "Authorization": "Bearer " + response.json()["access_token"],
        "X-PrufAI-Organisation-ID": str(organisation),
    }


def seed_berlin(database, env, password):
    data = run(
        sys.executable,
        "-m",
        "app.scripts.seed_lid_practice",
        "--prepare-demo",
        env={
            **env,
            "ENVIRONMENT": "development",
            "V1_PILOT_PROFILE": "DISABLED",
            "PRUFAI_DEMO_ENABLED": "1",
            "PRUFAI_DEMO_PASSWORD": password,
            "PRUFAI_DISPOSABLE_DATABASE": database.name,
        },
        timeout=120,
    )
    return json.loads(data.splitlines()[-1])


def seed_audio(client, ids, password):
    from operations import finalize, read_audio, recording, start_audio
    from tests.integration_support.audio_evidence import technical_tone_wav

    actor, attempt, path, items = start_audio(client, ids, password)
    content = technical_tone_wav()
    assets, revisions = [], []
    for item in items:
        endpoint = recording(client, actor, attempt, item)
        asset = finalize(client, actor, endpoint, content).json()
        assert asset["status"] == "READY" and asset["sha256"] == sha(content)
        assets.append(asset["media_asset_id"])
        saved = call(
            client,
            "PUT",
            path + "/responses/" + item,
            headers=actor,
            json={
                "client_operation_id": str(uuid4()),
                "expected_revision": 0,
                "payload": {"media_asset_id": asset["media_asset_id"]},
            },
        )
        revisions.append(saved.json()["candidate_response_revision_id"])
        read_audio(
            client,
            f"/api/v1/candidate/audio-assets/{asset['media_asset_id']}/stream",
            actor,
            content,
        )
    call(client, "POST", path + "/submit", headers=actor)
    org = ids["organisation_id"]
    admin = sign_in(client, ADMIN, password, org)
    job = call(
        client,
        "POST",
        f"/api/v1/organisations/{org}/candidate-media/{assets[0]}/transcriptions",
        201,
        headers=admin,
        json={
            "profile_id": ids["transcription_execution_profile_id"],
            "language": "de",
            "operation_id": str(uuid4()),
            "audio_sha256": sha(content),
            "candidate_response_revision_id": revisions[0],
        },
    ).json()
    assert job["status"] == "QUEUED" and job["attempt_count"] == 0 and job["test_only"]
    return assets


def assess(client, fixture, actors, org):
    candidate, staff, outsider = actors
    admin = f"/api/v1/organisations/{org}"
    assignment = f"{CANDIDATE}/exam-assignments/{fixture['candidate_assignment_id']}"
    assignment_record = call(client, "GET", assignment, headers=candidate).json()
    assert assignment_record["id"] == fixture["candidate_assignment_id"]
    assert assignment_record["lid_practice"]["bundeslaender"] == [
        {"available": True, "code": "DE-BE", "label": "Berlin"}
    ]
    call(
        client,
        "POST",
        assignment + "/attempts",
        403,
        headers=candidate,
        json={"bundesland": "DE-BY"},
    )
    attempt = call(
        client,
        "POST",
        assignment + "/attempts",
        201,
        headers=candidate,
        json={"bundesland": "DE-BE"},
    ).json()["id"]
    path = f"{CANDIDATE}/exam-attempts/{attempt}"
    state = call(client, "POST", path + "/start", headers=candidate).json()
    items = [
        item
        for module in state["runtime_plan"]["modules"]
        for section in module["sections"]
        for task in section["tasks"]
        for item in task["items"]
    ]
    assert len(items) == 33
    for index, item in enumerate(items):
        assert item["content"]["prompt"].startswith("ORIGINAL SYNTHETIC TEST CONTENT")
        number = int(item["version_label"].split("-")[-2])
        right = f"option-{(number - 1) % 4 + 1}"
        option = (
            right
            if index < 15
            else next(
                row["option_id"]
                for row in item["content"]["options"]
                if row["option_id"] != right
            )
        )
        call(
            client,
            "PUT",
            path + "/responses/" + item["binding_id"],
            headers=candidate,
            json={
                "client_operation_id": str(uuid4()),
                "expected_revision": 0,
                "payload": {"option_id": option},
            },
        )
    submitted = call(client, "POST", path + "/submit", headers=candidate).json()
    assert submitted["status"] == "SUBMITTED"
    score = f"{admin}/exam-attempts/{attempt}/score"
    scoring_run = call(client, "POST", score, headers=staff).json()
    rows = call(client, "GET", admin + "/exam-assignments", headers=staff).json()
    result = next(
        a["result"] for row in rows for a in row["attempts"] if a["id"] == attempt
    )
    assert scoring_run["id"] == result["scoring_run_id"]
    version = f"{admin}/result-versions/{result['version_id']}"
    private = path + "/lid-practice-result"
    legacy = f"{CANDIDATE}/results/{result['id']}"
    call(client, "GET", private, 404, headers=candidate)
    call(client, "GET", legacy, 404, headers=candidate)
    assert (
        call(client, "POST", version + "/request-review", headers=staff).json()[
            "status"
        ]
        == "PENDING"
    )
    assert (
        call(client, "POST", version + "/approve", headers=staff).json()["status"]
        == "APPROVED"
    )
    call(client, "GET", private, 404, headers=candidate)
    release_operation = {"operation_id": str(uuid4())}
    call(
        client,
        "POST",
        version + "/release",
        403,
        headers=candidate,
        json=release_operation,
    )
    released = call(
        client, "POST", version + "/release", headers=staff, json=release_operation
    ).json()
    assert released["release_status"] == "RELEASED"
    release = f"{CANDIDATE}/releases/{released['release_id']}"
    revoke = f"{admin}/releases/{released['release_id']}/revoke"
    statement = f"{admin}/releases/{released['release_id']}/statement"
    call(client, "POST", statement, headers=staff, json={"operation_id": str(uuid4())})
    assert call(client, "GET", release + "/statement", headers=candidate).json()[
        "currently_valid"
    ]
    call(client, "GET", release + "/statement", 404, headers=outsider)
    call(client, "GET", release, 404, headers=outsider)
    outcome = call(client, "GET", private, headers=candidate)
    assert outcome.headers["cache-control"] == "no-store"
    result = outcome.json()
    assert (
        result["raw_correct"] == 15
        and result["answered_count"] == result["total_items"] == 33
    )
    assert result["certification"] == "NOT_CERTIFIED"
    assert [
        (row["threshold"], row["numerical_outcome"]) for row in result["contexts"]
    ] == [
        (15, "THRESHOLD_MET"),
        (17, "BELOW_THRESHOLD"),
    ]
    return {
        "private": private,
        "legacy": legacy,
        "release": release,
        "revoke": revoke,
        "score": score,
        "scoring_run": scoring_run,
        "publish": version + "/release",
        "release_operation": release_operation,
    }


def assert_revoked(client, candidate, outsider, paths):
    for endpoint in ("private", "legacy"):
        call(client, "GET", paths[endpoint], 404, headers=candidate)
    statement = paths["release"] + "/statement"
    call(client, "GET", statement, 404, headers=candidate)
    call(client, "GET", paths["release"], 404, headers=outsider)
    response = call(client, "GET", paths["release"], headers=candidate)
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (
        body["effective_status"] == "REVOKED"
        and not body.get("result")
        and not body.get("statements")
    )


def stop_and_restore(
    api,
    rig,
    database,
    storage,
    decoder,
    secret,
    redis_url,
    head,
    env,
    listener,
    client,
    actors,
    paths,
    fixture,
    org,
    assets,
    password,
    sources,
    creator,
    scratch,
    remove_canary,
):
    candidate, staff, outsider = actors
    revocation = {
        "operation_id": str(uuid4()),
        "reason": "Synthetic release rollback rehearsal",
    }
    revoked = call(
        client, "POST", paths["revoke"], headers=staff, json=revocation
    ).json()
    assert revoked["status"] == "REVOKED"
    assert_revoked(client, candidate, outsider, paths)
    admin = f"/api/v1/organisations/{org}"
    assignment_payload = {
        "candidate_user_id": fixture["candidate_user_id"],
        "exam_version_id": fixture["exam_version_id"],
        "maximum_attempts": 2,
    }
    fresh = call(
        client,
        "POST",
        admin + "/exam-assignments",
        201,
        headers=staff,
        json=assignment_payload,
    ).json()
    new_attempt = f"{CANDIDATE}/exam-assignments/{fresh['id']}/attempts"
    prepared = call(
        client,
        "POST",
        new_attempt,
        201,
        headers=candidate,
        json={"bundesland": "DE-BE"},
    ).json()
    assert prepared["status"] == "READY"
    end(api)
    assert api.returncode in (0, -15)
    remove_canary()
    before_stop = snapshot(
        database, audit=False, extra_tables=TABLES + JOBS, allow_empty=JOBS[1:]
    )
    assert before_stop[JOBS[0]]["rows"] == 1
    stop_env = {**env, "V1_PILOT_PROFILE": STOPPED}
    stopped, _ = launch_api(
        rig, stop_env, listener=listener, source=scratch / "runtime"
    )
    call(client, "GET", paths["private"], 403, headers=candidate)
    call(client, "GET", paths["legacy"], 404, headers=candidate)
    call(client, "GET", paths["release"] + "/statement", 404, headers=candidate)
    call(
        client,
        "POST",
        f"{CANDIDATE}/exam-attempts/{prepared['id']}/start",
        403,
        headers=candidate,
    )
    call(
        client,
        "POST",
        new_attempt,
        403,
        headers=candidate,
        json={"bundesland": "DE-BE"},
    )
    call(
        client,
        "POST",
        paths["publish"],
        403,
        headers=staff,
        json={"operation_id": str(uuid4())},
    )
    call(
        client,
        "POST",
        admin + "/exam-assignments",
        403,
        headers=staff,
        json=assignment_payload,
    )
    for worker in WORKERS:
        run(
            sys.executable,
            "-m",
            "app." + worker + ".worker",
            "--once",
            env=stop_env,
            timeout=15,
        )
    assert (
        snapshot(database, audit=False, extra_tables=TABLES + JOBS, allow_empty=JOBS)
        == before_stop
    )
    end(stopped)
    assert stopped.returncode in (0, -15)
    print("E06A pilot stop denied new work and retained the queued job", flush=True)

    target_db = database.empty_sibling("prufai_eval2_restored_" + uuid4().hex)
    target_storage = Storage(rig, "release-restore", published_port=port())
    recovery = recover(
        database,
        storage,
        target_db,
        target_storage,
        head,
        audio_count=len(assets),
        extra_tables=TABLES + JOBS,
        allow_empty=JOBS[1:],
        shared_disposable_server=True,
    )
    verify_restored_standards(target_db, target_storage, sources, creator)
    prior = scratch / "previous"
    extract_tree(ROOT, PREVIOUS, prior, "services/api")
    assert migration_head(prior) == head
    restored_env = {
        **runtime_env(target_db, target_storage, redis_url, decoder, secret),
        "PYTHONPATH": str(prior / "services/api"),
    }
    remove_canary = canary(target_storage)
    restored, url = launch_api(rig, restored_env, listener=listener, source=prior)
    try:
        with httpx.Client(base_url=url, timeout=5, trust_env=False) as direct:
            assert call(direct, "GET", "/ready").json()["status"] == "ready"
        # The existing browser/API bearer remains valid after restoring auth state and exact signing config.
        assert_revoked(client, candidate, outsider, paths)
        assert (
            call(client, "POST", paths["score"], headers=staff).json()
            == paths["scoring_run"]
        )
        assert (
            call(client, "POST", paths["revoke"], headers=staff, json=revocation).json()
            == revoked
        )
        replayed = call(
            client,
            "POST",
            paths["publish"],
            409,
            headers=staff,
            json=paths["release_operation"],
        ).json()
        assert replayed["detail"] == "release is not active or is stale"
        assert (
            snapshot(
                target_db,
                audit=False,
                extra_tables=TABLES + JOBS,
                allow_empty=JOBS,
            )
            == before_stop
        )
    finally:
        end(restored)
    # Return briefly to the disposable non-pilot profile to verify restored
    # authorized Range playback. Do not start a worker or claim any job.
    diagnostic, url = launch_api(
        rig,
        {**restored_env, "V1_PILOT_PROFILE": "DISABLED"},
        listener=listener,
        source=prior,
    )
    try:
        from operations import read_audio
        from tests.integration_support.audio_evidence import technical_tone_wav

        with httpx.Client(base_url=url, timeout=5, trust_env=False) as direct:
            actor = sign_in(direct, AUDIO_USER, password, org)
            for asset in assets:
                stream = f"{CANDIDATE}/audio-assets/{asset}/stream"
                read_audio(direct, stream, actor, technical_tone_wav())
                call(direct, "GET", stream, 404, headers=outsider)
    finally:
        end(diagnostic)
        remove_canary()
    recovery["restored_authorized_audio_and_tenant_denial"] = "PASS"
    recovery["queued_transcription_preserved"] = 1
    return recovery


def migration_head(source):
    env = {**os.environ, "PYTHONPATH": str(source / "services/api")}
    (head,) = (
        run(
            sys.executable,
            "-m",
            "alembic",
            "heads",
            cwd=source / "services/api",
            env=env,
        )
        .decode()
        .splitlines()
    )
    revision, marker = head.split()
    assert marker == "(head)"
    return revision


def runtime_env(database, storage, redis_url, decoder, secret):
    # The disposable database guard requires both Actions markers in its
    # Linux branch. Pass those markers through the restricted child env.
    workflow = {
        name: os.environ[name]
        for name in ("CI", "GITHUB_ACTIONS")
        if os.environ.get(name) == "true"
    }
    return {
        **settings(database, storage, redis_url, decoder, secret),
        **workflow,
        "PRUFAI_DISPOSABLE_DATABASE": database.name,
        "V1_PILOT_PROFILE": ACTIVE,
        "CERTIFICATION_ISSUANCE_ENABLED": "false",
        "AUTH_ENROLLMENT_DELIVERY_MODE": "DISABLED",
    }


def exercise(stack, rig, decoder, scratch, out, local, source):
    head = migration_head(ROOT)
    assert head == migration_head_for_revision(PREVIOUS), (
        "prior Alembic history is incompatible"
    )
    database = Database(
        rig,
        "release-source",
        published_port=55438 if local else 5432,
        disposable_name="prufai_eval2_release_" + uuid4().hex,
    )
    assert database.migrate() == head
    storage = Storage(rig, "release-source", published_port=port())
    provenance(rig, storage, out, local)
    _, redis_url, redis_ready = launch_redis(rig)
    assert redis_ready()
    password = "E06a!Synthetic-" + secrets.token_hex(15)
    secret = secrets.token_hex(32)
    env = runtime_env(database, storage, redis_url, decoder, secret)
    fixture = seed_berlin(database, env, password)
    records, creator = seed(database, storage)
    ids, _, _, outside_org, outside_email = seed_domains(database, password)
    with session_for(database) as session:
        row = session.get(
            CandidateExamAssignment, UUID(fixture["candidate_assignment_id"])
        )
        assert row is not None and not row.attempts
        fixture["candidate_user_id"] = str(row.candidate_user_id)
    org = ids["organisation_id"]
    api_port, web_port = 58400, port()
    # Keep runtime credentials out of the frontend and package build environment.
    web_env = {k: os.environ[k] for k in ("HOME", "PATH", "CI") if k in os.environ} | {
        "NEXT_TELEMETRY_DISABLED": "1",
        "PRUFAI_API_BASE_URL": f"http://127.0.0.1:{api_port}",
        "PRUFAI_RELEASE_BUILD_ID": source["commit"],
    }
    build = package_files(rig, decoder, scratch, web_env)
    remove_canary = canary(storage)
    boot, url = launch_api(
        rig,
        {
            **env,
            "V1_PILOT_PROFILE": "DISABLED",
            "SPEECH_TRANSCRIPTION_TEST_ADAPTER_ENABLED": "1",
        },
        listener=api_port,
    )
    with httpx.Client(base_url=url, trust_env=False, timeout=10) as direct:
        assets = seed_audio(direct, ids, password)
    end(boot)
    assert boot.returncode in (0, -15)
    assert all(
        build["artifact_sha256"][name] == checksum
        for name, checksum in web_checksums(ROOT / "apps/web/.next").items()
    ), "Web artifacts changed before startup"
    runtime = scratch / "runtime"
    ai, _ = launch_api(
        rig,
        {"PATH": env["PATH"], "PYTHONPATH": str(runtime / "services/ai")},
        app_dir="ai",
        source=runtime,
    )
    env["PYTHONPATH"] = str(runtime / "services/api")
    api, api_url = launch_api(rig, env, listener=api_port, source=runtime)
    web, web_url = start_web(stack, web_env, web_port)
    probes = preflight(
        env,
        database,
        storage,
        api_url,
        web_url,
        expected_head=head,
        expected_source=source,
        build=build,
    )
    required = set(probes) - {
        "worker_liveness",
        "production_tls",
        "production_iam",
        "encrypted_backup",
        "alert_delivery",
    }
    assert all(probes[key] == "PASS" for key in required), probes
    print(
        "E06A disposable PostgreSQL, Redis and guarded storage preflight passed",
        flush=True,
    )
    with httpx.Client(base_url=web_url, trust_env=False, timeout=10) as client:
        actors = (
            authenticate(client, CANDIDATE_EMAIL, password, org),
            authenticate(client, ADMIN, password, org),
            authenticate(client, outside_email, password, outside_org),
        )
        paths = assess(client, fixture, actors, org)
        print(
            "E06A synthetic Berlin scoring, explicit release and tenant denial passed",
            flush=True,
        )
        # Induce a bounded failure on the owned source database and verify recovery.
        rig.owned("container", database.container)
        rig.docker("stop", "--time", "3", database.container)
        try:
            with httpx.Client(base_url=api_url, timeout=5, trust_env=False) as direct:
                call(direct, "GET", "/health")
                call(direct, "GET", "/ready", 503)
            call(client, "GET", paths["private"], 503, headers=actors[0])
        finally:
            rig.docker("start", database.container)
            eventually(
                lambda: (
                    httpx.get(
                        api_url + "/ready", timeout=5, trust_env=False
                    ).status_code
                    == 200
                )
            )
        # Close the active service before switching profiles and acquiring the coordinated archive.
        # Revocation is recorded first through the governed active API.
        recovery = stop_and_restore(
            api,
            rig,
            database,
            storage,
            decoder,
            secret,
            redis_url,
            head,
            env,
            api_port,
            client,
            actors,
            paths,
            fixture,
            org,
            assets,
            password,
            records,
            creator,
            scratch,
            remove_canary,
        )
    end(web)
    end(ai)
    assert web.returncode in (0, -15) and ai.returncode in (0, -15)
    return build, probes, recovery, head


def extract_tree(source, revision, target, *paths):
    target.mkdir(mode=0o700)
    payload = run("git", "archive", revision, *paths, cwd=source)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        archive.extractall(target, filter="data")


def migration_head_for_revision(revision):
    with tempfile.TemporaryDirectory(prefix="prufai-e06a-head-") as folder:
        source = Path(folder) / "source"
        extract_tree(ROOT, revision, source, "services/api")
        return migration_head(source)


def manifest(source, build, head, proof):
    config = configuration()
    return {
        "schema": SCHEMA,
        "SOURCE_IDENTITY": source,
        "BUILD_IDENTITY": build,
        "CONFIGURATION_IDENTITY": {
            "value": config,
            "sha256": sha(canonical(config)),
            "alembic_head": head,
        },
        "TEST_EVIDENCE": {
            "evidence_sha256": sha(canonical(proof)),
            "evidence_file": "v1-e06a-rehearsal.json",
            "supply_chain_run": os.getenv("GITHUB_RUN_ID"),
            "supply_chain_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
            "baseline_application_run": 43516788586,
            "baseline_supply_chain_run": 43516788660,
            "classification": "technical execution evidence",
        },
        "CONTENT_APPROVAL": CONTENT,
        "OPERATIONAL_AUTHORIZATION": {
            profile: authorization(profile) for profile in PROFILES
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--local-image")
    parser.add_argument("--local-decoder")
    args = parser.parse_args()
    policy = authorization(args.profile)
    if policy["status"] != "PASS":
        print(
            canonical(
                {"CONTENT_APPROVAL": CONTENT, "OPERATIONAL_AUTHORIZATION": policy}
            ).decode(),
            end="",
        )
        sys.exit(3)
    local = bool(args.local_image)
    if not __debug__:
        parser.error("E06A requires Python assertions")
    if local:
        if (
            os.getenv("PRUFAI_DISPOSABLE_OPERATIONS_TEST") != "1"
            or not args.local_decoder
        ):
            parser.error("select the disposable Docker profile and both local images")
    elif (os.getenv("GITHUB_ACTIONS"), os.getenv("CI")) != (
        "true",
        "true",
    ) or args.local_decoder:
        parser.error("select the disposable Docker profile or run in GitHub Actions")
    if any(
        (ROOT / relative).exists()
        for relative in (
            ".env",
            "services/api/.env",
            "services/ai/.env",
            "apps/web/.env",
            "apps/web/.env.local",
            "apps/web/.env.production",
            "apps/web/.env.production.local",
        )
    ):
        parser.error("use a checkout without local environment files")
    for blocked in ("STAGING_PROPOSED", "REAL_PILOT"):
        command = subprocess.run(
            (sys.executable, __file__, str(args.evidence), "--profile", blocked),
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert command.returncode == 3
        decision = json.loads(command.stdout)
        assert decision["CONTENT_APPROVAL"] == CONTENT
        assert decision["OPERATIONAL_AUTHORIZATION"]["status"] == "NOT_AUTHORIZED"
    clean = not git("status", "--porcelain", "--untracked-files=normal")
    if not local and not clean:
        parser.error("CI requires a clean committed checkout")
    os.umask(0o077)
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=True)
    storage_image = (
        args.local_image
        or json.loads((out / "minio-source.identity.json").read_text())["image_id"]
    )
    decoder_image = (
        args.local_decoder
        or json.loads((out / "decoder.identity.json").read_text())["image_id"]
    )
    source = source_identity()
    with ExitStack() as stack:
        scratch = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="prufai-v1-e06a-"))
        )
        rig = Rig(
            stack, json.loads(run("docker", "image", "inspect", storage_image))[0]["Id"]
        )
        decoder = rig.inspect("image", decoder_image)["Id"]
        build, probes, recovery, head = exercise(
            stack, rig, decoder, scratch, out, local, source
        )
    build["checkout_clean_at_start"] = clean
    proof = {
        "status": "PASS",
        "profile": args.profile,
        "source": source,
        "alembic_head": head,
        "preflight": probes,
        "activation": "PASS",
        "staging_and_real_pilot_fail_closed": "PASS",
        "database_failure_and_restart": "PASS",
        "safe_stop_and_worker_no_claim": "PASS",
        "previous_api_redeployed": "PASS",
        "result_confidentiality": "PASS",
        "assessment_replay_no_duplicates": "PASS",
        "guarded_storage_provenance": rig.evidence["provenance"],
        "reconciliation": recovery,
    }
    record = manifest(source, build, head, proof)
    packed = canonical(record)
    assert (
        packed
        == canonical(manifest(source, build, head, proof))
        == canonical(json.loads(packed))
    )
    (out / "v1-e06a-rehearsal.json").write_bytes(canonical(proof))
    (out / "v1-e06a-release-manifest.json").write_bytes(packed)
    (out / "v1-e06a-release-manifest.sha256").write_text(
        sha(packed) + "  v1-e06a-release-manifest.json\n"
    )
    print(
        "E06A synthetic release preflight, activation, safe stop, restore and confidentiality passed"
    )


if __name__ == "__main__":
    main()
