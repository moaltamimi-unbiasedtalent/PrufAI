"""Run O2A with synthetic evidence and newly labelled Docker resources."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from app.config import Settings
from app.operations.readiness import CANARY_BYTES, CANARY_KEY, measure_readiness
from app.scripts.seed_demo import GOLDEN_CANDIDATE_EMAIL, PLATFORM_ADMIN_EMAIL
from check import ROOT, Database, Rig, Storage, provenance, run, sha
from recovery import seed
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.engine import make_url

# Resolve the repository's integration fixtures, not a globally installed tests package.
sys.path.insert(0, str(ROOT / "services/api"))
from ops_recovery import recover, seed_domains, snapshot, verify_restored_standards
from tests.integration_support.audio_evidence import technical_tone_wav

REDIS = (
    "redis:7@sha256:2e3eb11462c4e3759e6972678dd051d150ff54b1e60714b3c33b701260172a90"
)
WORKERS = ("speech_transcription", "writing_execution", "speaking_execution")
AUDIO_USER = "audio.candidate@demo.prufai.local"
ADMIN = "admin@demo.prufai.local"
AUTH = "/api/v1/auth/login"
SYSTEM = "/api/v1/platform/control-center/system"


def eventually(check, *, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError("bounded readiness check timed out")


def end(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def child(rig, *argv, env):
    process = subprocess.Popen(
        (sys.executable, *argv),
        env=env,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rig.stack.callback(end, process)
    return process


def port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def launch_api(rig, env, *, app_dir="api", listener=None, source=ROOT):
    listener = listener or port()
    process = child(
        rig,
        "-m",
        "uvicorn",
        "app.main:app",
        "--app-dir",
        str(source / "services" / app_dir),
        "--host",
        "127.0.0.1",
        "--port",
        str(listener),
        "--no-access-log",
        env=env,
    )
    url = f"http://127.0.0.1:{listener}"
    expected_service = "prufai-o2a-api" if app_dir == "api" else "prufai-ai"

    def healthy():
        assert process.poll() is None, "service exited during startup"
        try:
            response = httpx.get(url + "/health", timeout=0.5, trust_env=False)
        except httpx.TransportError:
            return False
        return response.status_code == 200 and response.json() == {
            "service": expected_service,
            "status": "ok",
        }

    eventually(healthy)
    return process, url


def launch_redis(rig):
    name = rig.mark + "-ops-redis"
    volume = rig.mark + "-ops-redis-state"
    rig.create_volume(volume)
    published = port()
    password = secrets.token_hex(20)
    rig.launch(
        name,
        "--publish",
        f"127.0.0.1:{published}:6379",
        "--mount",
        f"type=volume,source={volume},target=/data",
        REDIS,
        "redis-server",
        "--requirepass",
        password,
    )
    binding = rig.owned("container", name)["NetworkSettings"]["Ports"]["6379/tcp"]
    assert len(binding) == 1 and binding[0]["HostIp"] == "127.0.0.1"

    url = f"redis://:{password}@127.0.0.1:{binding[0]['HostPort']}/0"

    def ready():
        try:
            with Redis.from_url(
                url, socket_connect_timeout=0.5, socket_timeout=0.5
            ) as client:
                return client.ping() is True
        except RedisError:
            return False

    eventually(ready)
    return name, url, ready


def settings(db, storage, redis_url, decoder, secret):
    parsed = urlsplit(db.url)
    assert parsed.hostname == "127.0.0.1" and parsed.path == "/" + db.name
    assert storage.endpoint.startswith("http://127.0.0.1:")
    environment = {
        k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST") if k in os.environ
    }
    return {
        **environment,
        "ENVIRONMENT": "test",
        "PYTHONPATH": str(ROOT / "services/api"),
        "SERVICE_NAME": "prufai-o2a-api",
        "DATABASE_URL": db.url,
        "REDIS_URL": redis_url,
        "AUTH_RATE_LIMIT_BACKEND": "redis",
        "AUTH_JWT_SECRET": secret,
        "OBJECT_STORAGE_BACKEND": "s3",
        "OBJECT_STORAGE_ENDPOINT": storage.endpoint,
        "OBJECT_STORAGE_BUCKET": storage.bucket,
        "OBJECT_STORAGE_ACCESS_KEY": storage.key,
        "OBJECT_STORAGE_SECRET_KEY": storage.secret,
        "OBJECT_STORAGE_SECURE": "false",
        "OPERATIONS_STORAGE_GUARD_PROBE_ENABLED": "true",
        "OPERATIONS_STORAGE_CANARY_ENABLED": "true",
        "AUDIO_MEDIA_RUNTIME": "docker",
        "AUDIO_MEDIA_IMAGE": decoder,
        "AWS_MAX_ATTEMPTS": "1",
        "AWS_EC2_METADATA_DISABLED": "true",
    }


def process_workers(rig, database, env):
    states = {}
    for worker in WORKERS:
        name = "o2a-" + worker
        url = make_url(database.url).update_query_dict({"application_name": name})
        process = child(
            rig,
            "-m",
            "app." + worker + ".worker",
            "--poll-interval",
            "0.2",
            env={**env, "DATABASE_URL": url.render_as_string(hide_password=False)},
        )

        def claimed_connection(process=process, name=name):
            assert process.poll() is None, (
                "worker exited before its first database poll"
            )
            return (
                database.sql(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    f"AND application_name = '{name}'"
                ).strip()
                == b"1"
            )

        eventually(claimed_connection)
        process.terminate()
        assert process.wait(timeout=5) == 0
        states[worker] = {"database_poll": "PASS", "graceful_sigterm_exit": 0}
    return states


def call(client, method, path, status=200, **kwargs):
    result = client.request(method, path, **kwargs)
    assert result.status_code == status, (method, status, result.status_code)
    return result


def sign_in(client, email, password, organisation):
    result = call(client, "POST", AUTH, json={"email": email, "password": password})
    return {
        "Authorization": "Bearer " + result.json()["access_token"],
        "X-PrufAI-Organisation-ID": str(organisation),
    }


def start_audio(client, ids, password):
    admin = sign_in(client, ADMIN, password, ids["organisation_id"])
    candidate = sign_in(client, AUDIO_USER, password, ids["organisation_id"])
    assignment = call(
        client,
        "POST",
        f"/api/v1/organisations/{ids['organisation_id']}/exam-assignments",
        201,
        headers=admin,
        json={
            "candidate_user_id": ids["audio_candidate_user_id"],
            "exam_version_id": ids["audio_exam_version_id"],
        },
    ).json()
    attempt = call(
        client,
        "POST",
        f"/api/v1/candidate/exam-assignments/{assignment['id']}/attempts",
        201,
        headers=candidate,
    ).json()["id"]
    path = f"/api/v1/candidate/exam-attempts/{attempt}"
    runtime = call(client, "POST", path + "/start", headers=candidate).json()
    items = [
        item["binding_id"]
        for module in runtime["runtime_plan"]["modules"]
        for section in module["sections"]
        for task in section["tasks"]
        for item in task["items"]
    ]
    assert len(items) == 2
    return candidate, attempt, path, items


def recording(client, headers, attempt, item):
    row = call(
        client,
        "POST",
        f"/api/v1/candidate/attempts/{attempt}/items/{item}/audio-recordings",
        headers=headers,
        json={"operation_id": str(uuid4())},
    ).json()
    return (
        "/api/v1/candidate/audio-recordings/"
        + row["recording_session_id"]
        + "/finalize"
    )


def finalize(client, headers, endpoint, content, status=200):
    return call(
        client,
        "POST",
        endpoint,
        status,
        headers={
            **headers,
            "Content-Type": "audio/wav",
            "X-Filename": "o2a-synthetic.wav",
        },
        content=content,
    )


def read_audio(client, path, headers, content):
    response = call(
        client, "GET", path, 206, headers={**headers, "Range": "bytes=100-199"}
    )
    assert response.content == content[100:200]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Content-Range"] == f"bytes 100-199/{len(content)}"
    assert call(client, "GET", path, headers=headers).content == content


def failures(
    rig, database, storage, redis, redis_url, ready, client, password, stream, actor, content
):
    prior = snapshot(database, audit=False)
    ai_runs = database.sql("SELECT count(*) FROM ai_runs")
    outcome = {}
    credentials = {"email": AUDIO_USER, "password": password}
    token = call(
        client, "POST", AUTH, json={"email": PLATFORM_ADMIN_EMAIL, "password": password}
    ).json()["access_token"]
    platform = {"Authorization": f"Bearer {token}"}
    call(client, "GET", SYSTEM, 403, headers={"Authorization": actor["Authorization"]})
    assert call(client, "GET", "/ready").json() == {"status": "ready"}
    detail = call(client, "GET", SYSTEM, headers=platform).json()
    assert detail["workers"]["process_health"] == "NOT_VERIFIED"
    assert set(detail["workers"]["queues"]) == {"transcription", "writing", "speaking"}
    assert detail["alert_delivery"] == "NOT_CONFIGURED"
    assert detail["object_storage"]["canary"] == "VERIFIED"
    assert detail["object_storage"]["provider_approval"] == "NOT_VERIFIED"
    wrong = storage.operator().put_object(
        Bucket=storage.bucket, Key=CANARY_KEY, Body=b"x" * len(CANARY_BYTES)
    )
    try:
        capability = call(client, "GET", SYSTEM, headers=platform).json()["object_storage"]
        assert capability["health"] == "DEGRADED"
        assert capability["failure_category"] == "INTEGRITY_MISMATCH"
    finally:
        storage.operator().delete_object(
            Bucket=storage.bucket, Key=CANARY_KEY, VersionId=wrong["VersionId"]
        )
    assert CANARY_KEY not in json.dumps(detail) and storage.secret not in json.dumps(detail)
    session_count = database.sql("SELECT count(*) FROM auth_sessions")
    rig.owned("container", database.container)
    rig.docker("stop", database.container)
    try:
        call(client, "GET", "/health")
        assert call(client, "GET", "/ready", 503).json() == {"status": "unavailable"}
        for _ in range(2):
            unavailable = call(client, "POST", AUTH, 503, json=credentials)
            assert unavailable.json() == {"detail": {"code": "AUTH_DATABASE_UNAVAILABLE"}}
            assert "set-cookie" not in unavailable.headers
        outcome["postgresql_unavailable"] = {
            "liveness": 200,
            "readiness": 503,
            "login": 503,
            "safe_response": True,
        }
    finally:
        rig.docker("start", database.container)
        eventually(lambda: db_ready(database))
    assert database.sql("SELECT count(*) FROM auth_sessions") == session_count
    call(client, "GET", "/ready")
    sign_in(client, AUDIO_USER, password, actor["X-PrufAI-Organisation-ID"])
    session_count = database.sql("SELECT count(*) FROM auth_sessions")
    rig.owned("container", redis)
    rig.docker("stop", redis)
    try:
        assert call(client, "GET", "/ready", 503).json() == {"status": "unavailable"}
        unavailable = call(client, "POST", AUTH, 503, json=credentials)
        assert "set-cookie" not in unavailable.headers
        assert unavailable.json() == {
            "detail": "authentication rate limiting is temporarily unavailable"
        }
        outcome["redis_unavailable"] = {"login": 503, "safe_response": True}
    finally:
        rig.docker("start", redis)
        eventually(ready)
    assert database.sql("SELECT count(*) FROM auth_sessions") == session_count
    call(client, "GET", "/ready")
    sign_in(client, AUDIO_USER, password, actor["X-PrufAI-Organisation-ID"])
    wrong_redis = redis_url.replace(urlsplit(redis_url).password, "wrong-synthetic")
    bad_credentials = Settings(
        _env_file=None, environment="test", database_url=database.url,
        redis_url=wrong_redis, auth_rate_limit_backend="redis",
    )
    assert measure_readiness(bad_credentials).redis.failure == "AUTHENTICATION_REJECTED"
    rig.owned("container", redis)
    rig.docker("pause", redis)
    try:
        started = time.monotonic()
        assert call(client, "GET", "/ready", 503).json() == {"status": "unavailable"}
        assert time.monotonic() - started < 5
        assert call(client, "GET", SYSTEM, headers=platform).json()["redis"][
            "failure_category"
        ] == "TIMEOUT"
    finally:
        rig.docker("unpause", redis)
        eventually(ready)
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: client.get("/ready").status_code, range(12))) == [200] * 12
    outcome["bounded_repeated_readiness"] = "PASS"
    outcome["redis_authentication_and_timeout_classification"] = "PASS"
    for label, command in (
        ("minio_container_stopped", ("stop", storage.container)),
        ("storage_guard_killed", ("kill", "--signal", "KILL", storage.container)),
    ):
        rig.owned("container", storage.container)
        rig.docker(*command)
        try:
            call(client, "GET", "/ready")
            storage_health = call(client, "GET", SYSTEM, headers=platform).json()["object_storage"]
            assert storage_health["health"] == "UNAVAILABLE"
            response = call(client, "GET", stream, 503, headers=actor)
            assert response.json() == {
                "detail": "private audio storage or upload recovery is unavailable"
            }
            outcome[label] = {"playback": 503, "safe_response": True}
        finally:
            rig.docker("start", storage.container)
            storage.wait()
        read_audio(client, stream, actor, content)
        assert call(client, "GET", SYSTEM, headers=platform).json()["object_storage"]["canary"] == "VERIFIED"
    assert snapshot(database, audit=False) == prior
    assert database.sql("SELECT count(*) FROM ai_runs") == ai_runs
    return outcome


def db_ready(database):
    result = subprocess.run(
        ("docker", "exec", database.container, "pg_isready", "-q", "-h", "127.0.0.1"),
        capture_output=True,
        check=False,
        timeout=5,
    )
    return result.returncode == 0


def exercise(rig, out, local, decoder):
    database = Database(rig, "ops-source", published_port=port())
    storage = Storage(rig, "ops-source", published_port=port())
    provenance(rig, storage, out, local)
    head = database.migrate()
    expected = run(
        sys.executable, "-m", "alembic", "heads", cwd=ROOT / "services/api"
    ).decode()
    assert expected.strip() == head + " (head)"
    rig.evidence["alembic_head"] = head
    redis, redis_url, redis_ready = launch_redis(rig)
    password, secret = "O2a!Synthetic-" + secrets.token_hex(12), secrets.token_hex(32)
    records, creator = seed(database, storage)
    ids, release, pending, foreign_org, foreign_email = seed_domains(database, password)
    ai_runs = database.sql("SELECT count(*) FROM ai_runs")
    canary = storage.operator().put_object(
        Bucket=storage.bucket, Key=CANARY_KEY, Body=CANARY_BYTES
    )
    env = settings(database, storage, redis_url, decoder, secret)
    ai, _ = launch_api(
        rig,
        {"PATH": env["PATH"], "PYTHONPATH": str(ROOT / "services/ai")},
        app_dir="ai",
    )
    ai.terminate()
    assert ai.wait(timeout=5) in (0, -15)
    rig.evidence["ai_process_liveness_and_stop"] = "PASS"
    rig.evidence["worker_idle_lifecycle"] = process_workers(rig, database, env)

    api, url = launch_api(rig, env)
    listener = urlsplit(url).port
    content = technical_tone_wav()
    with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
        actor, attempt, path, items = start_audio(client, ids, password)
        streams = []
        for item in items:
            endpoint = recording(client, actor, attempt, item)
            asset = finalize(client, actor, endpoint, content).json()
            assert asset["status"] == "READY" and asset["sha256"] == sha(content)
            call(
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
            streams.append(
                f"/api/v1/candidate/audio-assets/{asset['media_asset_id']}/stream"
            )
        call(client, "POST", path + "/submit", headers=actor)
        read_audio(client, streams[0], actor, content)
        other = sign_in(client, foreign_email, password, foreign_org)
        call(client, "GET", streams[0], 404, headers=other)
        call(client, "GET", f"/api/v1/candidate/releases/{release}", 404, headers=other)
        result_owner = sign_in(
            client, "candidate@demo.prufai.local", password, ids["organisation_id"]
        )
        call(
            client,
            "GET",
            f"/api/v1/candidate/results/{pending}",
            404,
            headers=result_owner,
        )
        rig.evidence["service_failures"] = failures(
            rig,
            database,
            storage,
            redis,
            redis_url,
            redis_ready,
            client,
            password,
            streams[0],
            actor,
            content,
        )
        end(api)
        assert api.returncode in (0, -15)
        client_s3 = storage.operator()
        versions = client_s3.list_object_versions(Bucket=storage.bucket, Prefix=CANARY_KEY)
        assert [(entry["Key"], entry["VersionId"]) for entry in versions["Versions"]] == [
            (CANARY_KEY, canary["VersionId"])
        ]
        assert not versions.get("DeleteMarkers")
        client_s3.delete_object(
            Bucket=storage.bucket, Key=CANARY_KEY, VersionId=canary["VersionId"]
        )
        # The next process can reach PostgreSQL and MinIO, but its decoder is absent.
        broken_env = {
            **env,
            "AUDIO_MEDIA_RUNTIME": "native",
            "AUDIO_FFPROBE_EXECUTABLE": "/nonexistent/o2a-ffprobe",
        }
        broken, _ = launch_api(rig, broken_env, listener=listener)
        with httpx.Client(base_url=url, timeout=10, trust_env=False) as restart_client:
            actor = sign_in(
                restart_client, AUDIO_USER, password, ids["organisation_id"]
            )
            read_audio(restart_client, streams[0], actor, content)
            second_actor, second_attempt, _, second_items = start_audio(
                restart_client, ids, password
            )
            failed_endpoint = recording(
                restart_client, second_actor, second_attempt, second_items[0]
            )
            denied = finalize(
                restart_client, second_actor, failed_endpoint, content, status=503
            )
            assert denied.json() == {"detail": "audio media validation is unavailable"}
        assert (
            database.sql("SELECT count(*) FROM candidate_media_assets").strip() == b"2"
        )
        assert database.sql("SELECT count(*) FROM ai_runs") == ai_runs
        end(broken)
    rig.evidence["service_failures"]["decoder_unavailable"] = {
        "finalize": 503,
        "assets_created": 0,
    }
    rig.evidence["api_restart_private_playback"] = "PASS"
    target_db, target_storage = (
        Database(rig, "ops-restore"),
        Storage(rig, "ops-restore"),
    )
    rig.evidence["restore"] = recover(
        database, storage, target_db, target_storage, head
    )
    verify_restored_standards(target_db, target_storage, records, creator)
    target_env = settings(target_db, target_storage, redis_url, decoder, secret)
    restored_api, restored_url = launch_api(rig, target_env)
    with httpx.Client(base_url=restored_url, timeout=10, trust_env=False) as client:
        actor = sign_in(client, AUDIO_USER, password, ids["organisation_id"])
        for stream in streams:
            read_audio(client, stream, actor, content)
        other = sign_in(client, foreign_email, password, foreign_org)
        call(client, "GET", streams[0], 404, headers=other)
        history = sign_in(
            client, GOLDEN_CANDIDATE_EMAIL, password, ids["organisation_id"]
        )
        call(client, "GET", f"/api/v1/candidate/releases/{release}", headers=history)
        call(client, "GET", f"/api/v1/candidate/releases/{release}", 404, headers=other)
        result_owner = sign_in(
            client, "candidate@demo.prufai.local", password, ids["organisation_id"]
        )
        call(
            client,
            "GET",
            f"/api/v1/candidate/results/{pending}",
            404,
            headers=result_owner,
        )
        retried = finalize(client, actor, failed_endpoint, content).json()
        assert retried["status"] == "READY" and retried["sha256"] == sha(content)
    end(restored_api)
    rig.evidence["decoder_recovered_finalization"] = "PASS"
    rig.evidence["restored_authorized_range_standards_release_and_tenant_denial"] = (
        "PASS"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--local-image")
    parser.add_argument("--local-decoder")
    args = parser.parse_args()
    local = bool(args.local_image)
    if not __debug__:
        parser.error("O2A requires Python assertions")
    if local:
        if (
            os.getenv("PRUFAI_DISPOSABLE_OPERATIONS_TEST") != "1"
            or not args.local_decoder
        ):
            parser.error(
                "select a disposable Docker profile and provide both local images"
            )
    elif os.getenv("GITHUB_ACTIONS") != "true" or args.local_decoder:
        parser.error("this mode is restricted to GitHub Actions")
    if (ROOT / ".env").exists() or (ROOT / "services/api/.env").exists():
        parser.error("use a clean checkout without local environment files")
    os.umask(0o077)
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if local:
        image = json.loads(run("docker", "image", "inspect", args.local_image))[0]["Id"]
        decoder = json.loads(run("docker", "image", "inspect", args.local_decoder))[0][
            "Id"
        ]
    else:
        image = json.loads((out / "minio-source.identity.json").read_text())["image_id"]
        decoder = json.loads((out / "decoder.identity.json").read_text())["image_id"]
    with ExitStack() as stack:
        rig = Rig(stack, image)
        rig.evidence["source_sha"] = run("git", "rev-parse", "HEAD").decode().strip()
        rig.evidence["decoder_image"] = decoder
        try:
            exercise(rig, out, local, decoder)
            stack.close()
            rig.evidence["status"] = "PASS"
        finally:
            (out / "operations-readiness.json").write_text(
                json.dumps(rig.evidence, indent=2) + "\n"
            )
    print(
        "O2A disposable process, fault and coordinated PostgreSQL/object restore proof passed"
    )


if __name__ == "__main__":
    main()
