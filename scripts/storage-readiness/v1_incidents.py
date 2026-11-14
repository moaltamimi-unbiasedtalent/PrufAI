"""Exercise actual V1 failures and relay bounded observations to a loopback receiver."""

from __future__ import annotations

import json
import sys
from uuid import uuid4

from app.config import Settings
from app.operations.readiness import (
    CANARY_BYTES,
    CANARY_KEY,
    measure_readiness,
)
from check import POSTGRES, run
from network import (
    BACK,
    FRONT,
    GUARD_DENIAL,
    UNSAFE,
    addresses,
    listeners,
    request,
)
from operations import AUTH, SYSTEM, WORKERS, call, db_ready, eventually
from ops_recovery import snapshot


def exercise_incidents(
    rig,
    database,
    storage,
    redis,
    redis_ready,
    env,
    direct,
    web,
    actors,
    paths,
    alerts,
    *,
    password,
    org,
    assets,
    job_tables,
):
    candidate, staff, outsider = actors
    token = call(
        direct,
        "POST",
        AUTH,
        json={
            "email": "platform.admin@demo.prufai.local",
            "password": password,
        },
    ).json()["access_token"]
    platform = {"Authorization": f"Bearer {token}"}

    def ensure():
        return call(direct, "GET", SYSTEM, headers=platform).json()

    call(direct, "GET", SYSTEM, 403, headers=staff)
    call(direct, "GET", SYSTEM, 403, headers=candidate)
    call(direct, "GET", "/ready")
    state = ensure()
    assert state["alert_delivery"] == "NOT_CONFIGURED"
    assert state["workers"]["process_health"] == "NOT_VERIFIED"
    assert state["workers"]["execution_scope"] == dict.fromkeys(
        ("transcription", "writing", "speaking", "external_providers"), "EXCLUDED"
    )
    assert state["object_storage"]["canary"] == "VERIFIED"

    # Reuse the same bounded dependency probes without relying on a failed DB for authentication.
    probes = Settings(
        _env_file=None,
        database_url=env["DATABASE_URL"],
        redis_url=env["REDIS_URL"],
        auth_rate_limit_backend="redis",
        environment="test",
    )
    for component, container, ready in (
        ("postgresql", database.container, lambda: db_ready(database)),
        ("redis", redis, redis_ready),
    ):
        rig.owned("container", container)
        rig.docker("stop", "--time", "3", container)
        try:
            call(direct, "GET", "/health")
            assert call(direct, "GET", "/ready", 503).json()["status"] == "unavailable"
            measurement = measure_readiness(probes)
            failure = (
                measurement.postgres if component == "postgresql" else measurement.redis
            ).failure
            assert failure is not None
            event = alerts.raise_alert(component + ".readiness", failure.value)
            if component == "postgresql":
                call(web, "GET", paths["private"], 503, headers=candidate)
        finally:
            rig.docker("start", container)
            eventually(ready)
        call(direct, "GET", "/ready")
        assert measure_readiness(probes).ready
        alerts.resolve(event)
        call(web, "GET", paths["release"], 404, headers=outsider)

    assert_guarded(rig, storage)
    rig.owned("container", storage.container)
    rig.docker("kill", "--signal", "KILL", storage.container)
    try:
        call(direct, "GET", "/ready")
        guard = ensure()["object_storage"]
        assert guard["guard"] == "UNAVAILABLE" and guard["health"] == "UNAVAILABLE"
        guard_event = alerts.raise_alert("storage.guard", guard["failure_category"])
    finally:
        rig.docker("start", storage.container)
        storage.wait()
    assert_guarded(rig, storage)
    assert ensure()["object_storage"]["canary"] == "VERIFIED"
    alerts.resolve(guard_event)

    s3 = storage.operator()
    for code, expected, inject in (
        (
            "storage.private_operation",
            "UNAVAILABLE",
            lambda: s3.delete_object(
                Bucket=storage.bucket,
                Key=CANARY_KEY,
            ),
        ),
        (
            "storage.integrity",
            "INTEGRITY_MISMATCH",
            lambda: s3.put_object(
                Bucket=storage.bucket,
                Key=CANARY_KEY,
                Body=b"x" * len(CANARY_BYTES),
            ),
        ),
    ):
        version = inject()["VersionId"]
        try:
            observed = ensure()["object_storage"]
            assert observed["guard"] == "REACHABLE"
            assert observed["failure_category"] == expected, observed
            incident = alerts.raise_alert(code, observed["failure_category"])
        finally:
            s3.delete_object(Bucket=storage.bucket, Key=CANARY_KEY, VersionId=version)
        assert ensure()["object_storage"]["canary"] == "VERIFIED"
        alerts.resolve(incident)

    before = snapshot(
        database, audit=False, extra_tables=job_tables, allow_empty=job_tables[1:]
    )
    assert before[job_tables[0]]["rows"] == 1
    previous = ensure()["operational_failures"]
    worker_denials = previous.get("worker.activation", {}).get("POLICY_DENIED", 0)
    provider_denials = previous.get("provider.execution", {}).get("POLICY_DENIED", 0)
    root = f"/api/v1/organisations/{org}"
    for actor in (candidate, staff):
        for path in (
            f"{root}/candidate-media/{assets[0]}/transcriptions",
            f"{root}/writing-executions",
            f"{root}/speaking-executions",
        ):
            denied = call(web, "POST", path, 403, headers=actor, json={})
            assert denied.json() == {"detail": "PILOT_SCOPE_DENIED"}
            worker_denials += 1
            observed = ensure()["operational_failures"]
            assert (
                observed.get("worker.activation", {}).get("POLICY_DENIED")
                == worker_denials
            ), (path.split("/")[-1], observed)
        denied = call(
            web,
            "POST",
            f"/api/v1/platform/ai/workflow-versions/{uuid4()}/run",
            403,
            headers=actor,
            json={},
        )
        assert denied.json() == {"detail": "PILOT_SCOPE_DENIED"}
        provider_denials += 1
        observed = ensure()["operational_failures"]
        assert (
            observed.get("provider.execution", {}).get("POLICY_DENIED")
            == provider_denials
        ), observed
    for worker in WORKERS:
        output = run(
            sys.executable,
            "-m",
            f"app.{worker}.worker",
            "--once",
            env=env,
            timeout=15,
        )
        assert output == b""
    worker_event = alerts.raise_alert("worker.activation", "POLICY_DENIED")
    provider_event = alerts.raise_alert("provider.execution", "POLICY_DENIED")
    assert (
        snapshot(
            database, audit=False, extra_tables=job_tables, allow_empty=job_tables[1:]
        )
        == before
    )
    alerts.resolve(worker_event)
    alerts.resolve(provider_event)

    # A second operation against an already released exact result must fail and log safely.
    call(
        web,
        "POST",
        paths["publish"],
        409,
        headers=staff,
        json={"operation_id": str(uuid4())},
    )
    assert (
        ensure()["operational_failures"]["result_release.operation"]["OPERATION_FAILED"]
        >= 1
    )
    failure = alerts.raise_alert("result_release.operation", "OPERATION_FAILED")
    response = call(web, "GET", paths["private"], headers=candidate)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["raw_correct"] == 15
    assert web.get(paths["private"], headers=outsider).status_code in {403, 404}
    alerts.resolve(failure)

    summary = ensure()
    assert summary["alert_delivery"] == "NOT_CONFIGURED"
    assert summary["workers"]["process_health"] == "NOT_VERIFIED"
    for code in (
        "postgresql.readiness",
        "redis.readiness",
        "storage.guard",
        "storage.private_operation",
        "storage.integrity",
    ):
        assert code in summary["operational_failures"], code
    sentinels = [
        password,
        env["AUTH_JWT_SECRET"],
        env["DATABASE_URL"],
        env["REDIS_URL"],
        storage.secret,
        candidate["Authorization"].split()[-1],
        staff["Authorization"].split()[-1],
        alerts.token,
        token,
    ]
    assert all(
        s not in json.dumps(alerts.evidence) + json.dumps(summary) for s in sentinels
    )
    return {
        "worker_claims_and_provider_requests_denied": "PASS",
        "worker_process_health": "NOT_VERIFIED",
        "direct_backend_ipv4_ipv6_denied_before_and_after_restart": "PASS",
        "unauthorized_control_centre_access": "DENIED",
        "synthetic_sentinels": sentinels,
    }


def assert_guarded(rig, storage):
    bindings = rig.owned("container", storage.container)["HostConfig"]["PortBindings"]
    assert set(bindings) == {f"{port}/tcp" for port in FRONT}
    assert not any(f"{port}/tcp" in bindings for port in BACK)
    listeners(rig, storage.container)
    for address in addresses(rig, storage.container, storage.network):
        verdict = rig.docker(
            "run",
            "--rm",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--network",
            storage.network,
            "--entrypoint",
            "/bin/bash",
            POSTGRES,
            "-ec",
            "host=$1; shift; for port; do "
            """if timeout 2 bash -c 'exec 3<>"/dev/tcp/$1/$2"' _ "$host" "$port" 2>/dev/null; """
            'then printf "open\\n"; else printf "closed\\n"; fi; done',
            "probe",
            address,
            *map(str, (*FRONT, *BACK)),
        )
        assert verdict.splitlines() == [b"open"] * len(FRONT) + [b"closed"] * len(BACK)
    assert (
        request(
            storage.port(),
            "PUT",
            "/synthetic/unauthorized-write",
            raw_headers=(("x-amz-content-sha256", UNSAFE),),
        )
        == GUARD_DENIAL
    )
