"""Network and failure probes for the disposable O1B.2 rehearsal."""

import hashlib
import http.client
import ipaddress
import json
import socket
from urllib.parse import quote, urlencode

from app.knowledge.storage import StorageError
from botocore.exceptions import ClientError, ReadTimeoutError

FRONT = (9000, 9001)
BACK = (19000, 19001)
UNSAFE = "STREAMING-UNSIGNED-PAYLOAD-TRAILER"
GUARD_DENIAL = (403, "unsigned-trailer-denied")

PEER_PROBE = """
import hashlib, http.client, json, socket, sys
from storage import S3CompatibleObjectStorage
import boto3
from botocore.config import Config
p = json.load(sys.stdin)
if p.get("signed_url"):
    from urllib.request import urlopen
    with urlopen(p["signed_url"], timeout=5) as response:
        print(json.dumps(hashlib.sha256(response.read()).hexdigest()))
else:
    for host, port, expected in p["tcp"]:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                connected = True
        except OSError:
            connected = False
        assert connected is expected, (host, port, connected, expected)
    for host, port, path, expected in p["http"]:
        conn = http.client.HTTPConnection(host, port, timeout=3)
        conn.request("PUT", path, headers={
            "x-aMz-CoNtEnT-sHa256": " streaming-unsigned-payload-trailer "
        })
        response = conn.getresponse()
        assert [response.status, response.getheader("X-Prufai-Storage-Guard")] == expected
        conn.close()
    if p.get("adapter"):
        args = p["adapter"]
        store = S3CompatibleObjectStorage(
            endpoint=args["endpoint"], bucket=args["bucket"], access_key=args["key"],
            secret_key=args["secret"], secure=False,
            client=boto3.client("s3", endpoint_url=args["endpoint"], region_name="us-east-1",
                aws_access_key_id=args["key"], aws_secret_access_key=args["secret"],
                aws_session_token=args["token"], config=Config(
                    connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}))
        )
        content = store.get(args["object"])
        assert hashlib.sha256(content).hexdigest() == args["sha256"]
    print(json.dumps({"tcp": len(p["tcp"]), "guard_denials": len(p["http"])}))
"""


def request(port, method, path, *, host="127.0.0.1", raw_headers=(), body=b""):
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.putrequest(method, path)
        conn.putheader("Connection", "close")
        conn.putheader("Content-Length", str(len(body)))
        for name, value in raw_headers:
            conn.putheader(name, value)
        conn.endheaders(body)
        response = conn.getresponse()
        return response.status, response.getheader("X-Prufai-Storage-Guard")
    finally:
        conn.close()


def malformed(port, bucket):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
        conn.settimeout(3)
        conn.sendall(
            f"PUT /{bucket}/malformed HTTP/1.1\r\nHost: localhost\r\n"
            f"X-Amz-Content-Sha256 : {UNSAFE}\r\nContent-Length: 0\r\n\r\n".encode()
        )
        with conn.makefile("rb") as stream:
            assert stream.readline(100).split()[1] == b"400"


def listeners(rig, service):
    data = rig.docker("exec", service, "cat", "/proc/net/tcp", "/proc/net/tcp6")
    sockets = []
    for line in data.decode().splitlines():
        columns = line.split()
        if len(columns) < 4 or columns[3] != "0A":
            continue
        raw, port = columns[1].split(":")
        packed = bytes.fromhex(raw)
        address = ipaddress.ip_address(
            b"".join(packed[i : i + 4][::-1] for i in range(0, len(packed), 4))
        )
        sockets.append((str(address), int(port, 16)))
    for port in BACK:
        addresses = [ipaddress.ip_address(a) for a, p in sockets if p == port]
        assert any(a.version == 4 for a in addresses)
        assert all(a.is_loopback for a in addresses), (port, addresses)
    for port in FRONT:
        assert ("::", port) in sockets, sockets
    return sockets


def addresses(rig, service, network):
    info = rig.inspect("container", service)["NetworkSettings"]["Networks"]
    assert set(info) == {network}
    info = info[network]
    assert info["IPAddress"] and info["GlobalIPv6Address"]
    return info["IPAddress"], info["GlobalIPv6Address"]


def peer(rig, network, *, tcp=(), http=(), adapter=None, signed_url=None):
    report = rig.docker(
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
        network,
        "-i",
        rig.peer_image,
        "python",
        "-c",
        PEER_PROBE,
        data=json.dumps(
            {"tcp": tcp, "http": http, "adapter": adapter, "signed_url": signed_url}
        ).encode(),
    )
    return json.loads(report)


def topology(rig, storage, isolated):
    settings = rig.inspect("container", storage.container)
    assert settings["HostConfig"]["NetworkMode"] == storage.network
    assert settings["Image"] == rig.storage_image
    assert not settings["HostConfig"]["Privileged"]
    bindings = settings["HostConfig"]["PortBindings"]
    assert set(bindings) == {f"{port}/tcp" for port in FRONT}
    assert all(
        {b["HostIp"] for b in entries} == {"127.0.0.1", "::1"}
        for entries in bindings.values()
    )
    ipv4, ipv6 = addresses(rig, storage.container, storage.network)
    sockets = listeners(rig, storage.container)
    direct = [(addr, port, False) for addr in (ipv4, ipv6) for port in BACK]
    guarded = [(addr, port, True) for addr in (ipv4, ipv6) for port in FRONT]
    denied = [
        (addr, port, "/synthetic/o1b2-network-probe", GUARD_DENIAL)
        for addr in (ipv4, ipv6)
        for port in FRONT
    ]
    assert request(storage.port(), "GET", "/minio/health/ready")[0] == 200
    assert request(storage.port(9001), "GET", "/")[0] == 200
    for port in FRONT:
        assert (
            request(
                storage.port(port),
                "PUT",
                "/synthetic/host",
                raw_headers=(("x-amz-content-sha256", UNSAFE),),
            )
            == GUARD_DENIAL
        )
    peer(rig, storage.network, tcp=direct + guarded, http=denied)
    # The Docker engine host can route to the bridge and must still see no backend.
    host = [("::1", storage.port(port, "::1"), True) for port in FRONT]
    peer(
        rig,
        "host",
        tcp=direct + host,
        http=[
            ("::1", storage.port(port, "::1"), "/synthetic/host-v6", GUARD_DENIAL)
            for port in FRONT
        ],
    )
    peer(
        rig,
        isolated,
        tcp=direct + [(addr, port, False) for addr in (ipv4, ipv6) for port in FRONT],
    )
    rig.evidence["topology"] = {
        "container": storage.container,
        "network": storage.network,
        "interfaces": [ipv4, ipv6],
        "listeners": sockets,
        "published": settings["NetworkSettings"]["Ports"],
        "container_id": settings["Id"],
        "host_ipv4_and_ipv6": "PASS",
        "peer_ipv4_and_ipv6": "PASS",
        "separate_bridge": "PASS",
        "guarded_console": "PASS",
    }


def unsafe_requests(storage, record, assert_original):
    # Each request targets either an existing object or a different synthetic key.
    key = storage.scoped("standards/*")[0]
    query = "?" + urlencode(
        {"X-Amz-Credential": f"{key}/20261109/us-east-1/s3/aws4_request"}
    )
    target = f"/{storage.bucket}/{record['key']}"
    multipart = storage.operator().create_multipart_upload(
        Bucket=storage.bucket, Key=record["key"]
    )
    upload_id = multipart["UploadId"]

    cases = {
        "mixed_case_and_whitespace": (
            target,
            (("x-AmZ-cOnTeNt-sHa256", f" \t{UNSAFE.lower()}\t "),),
        ),
        "duplicate_conflicting": (
            target,
            (
                ("X-Amz-Content-Sha256", "UNSIGNED-PAYLOAD"),
                ("x-amz-content-sha256", UNSAFE),
            ),
        ),
        "combined_value": (
            target,
            (("X-Amz-Content-Sha256", f"UNSIGNED-PAYLOAD, {UNSAFE}"),),
        ),
        "encoded_path_and_query_credentials": (
            target.replace("standards", "%73tandards") + query,
            (("X-Amz-Content-Sha256", UNSAFE),),
        ),
        "snowball": (
            target + "?extract",
            (("X-Amz-Content-Sha256", UNSAFE), ("X-Minio-Extract", "true")),
        ),
        "multipart_part": (
            target + f"?partNumber=1&uploadId={quote(upload_id, safe='')}",
            (("X-Amz-Content-Sha256", UNSAFE),),
        ),
    }
    for name, (path, headers) in cases.items():
        assert (
            request(
                storage.port(),
                "PUT",
                path,
                raw_headers=headers,
                body=b"synthetic rejection probe",
            )
            == GUARD_DENIAL
        ), name
        assert_original()
    parts = storage.operator().list_parts(
        Bucket=storage.bucket, Key=record["key"], UploadId=upload_id
    )
    assert parts.get("Parts", []) == []
    storage.operator().abort_multipart_upload(
        Bucket=storage.bucket, Key=record["key"], UploadId=upload_id
    )
    malformed(storage.port(), storage.bucket)
    assert_original()
    return [*cases, "malformed_header_rejected_by_http_parser"]


def rejected(call, *, status):
    try:
        call()
    except ClientError as exc:
        assert exc.response["ResponseMetadata"]["HTTPStatusCode"] == status
        return
    raise AssertionError("unexpected authorized S3 operation")


def faults(rig, storage, record, read_restricted, assert_original):
    results = {}
    rig.owned("container", storage.container)
    child = (
        rig.docker(
            "exec",
            storage.container,
            "sh",
            "-ec",
            """
      for p in /proc/[0-9]*/comm; do
        if read -r name < "$p" && [ "$name" = minio ]; then
          p=${p%/comm}; echo "${p##*/}"
        fi
      done
    """,
        )
        .decode()
        .splitlines()
    )
    assert len(child) == 1 and child[0].isdecimal() and int(child[0]) > 1
    rig.docker("exec", storage.container, "kill", "-STOP", child[0])
    try:
        try:
            assert request(storage.port(), "GET", "/minio/health/ready")[0] != 200
        except TimeoutError:
            pass
        try:
            read_restricted()
        except StorageError:
            pass
        else:
            raise AssertionError("paused backend served a signed request")
        assert (
            request(
                storage.port(),
                "PUT",
                f"/{storage.bucket}/{record['key']}",
                raw_headers=(("X-Amz-Content-Sha256", UNSAFE),),
            )
            == GUARD_DENIAL
        )
        peer(
            rig,
            storage.network,
            tcp=[
                (a, p, False)
                for a in addresses(rig, storage.container, storage.network)
                for p in BACK
            ],
        )
    finally:
        rig.docker("exec", storage.container, "kill", "-CONT", child[0])
    storage.wait()
    assert_original()
    results["backend_pause_health_and_client_failure"] = "PASS"

    # Let the real SDK retry only after its first request experienced a timeout.
    adapter = storage.adapter("standards/*", write=False, retry=True)
    attempts_seen = []
    resumed = False

    def observe_retry(attempts, caught_exception, response, **_):
        nonlocal resumed
        if attempts == 1:
            assert isinstance(caught_exception, ReadTimeoutError)
            rig.docker("exec", storage.container, "kill", "-CONT", child[0])
            resumed = True
            attempts_seen.append("read timeout")
        else:
            assert attempts == 2 and response[0].status_code == 200
            attempts_seen.append("200")

    adapter.client.meta.events.register("needs-retry.s3.GetObject", observe_retry)
    rig.docker("exec", storage.container, "kill", "-STOP", child[0])
    try:
        assert (
            hashlib.sha256(adapter.get(record["key"])).hexdigest() == record["sha256"]
        )
    finally:
        if not resumed:
            rig.docker("exec", storage.container, "kill", "-CONT", child[0])
    assert attempts_seen == ["read timeout", "200"]
    results["bounded_api_adapter_retry"] = attempts_seen

    for event, argv, expected_exit in (
        ("guard_sigkill", ("kill", "--signal", "KILL", storage.container), 137),
        ("guard_sigterm", ("kill", "--signal", "TERM", storage.container), 0),
        ("container_stop", ("stop", "--time", "12", storage.container), 0),
        (
            "backend_sigkill",
            ("exec", storage.container, "kill", "-KILL", "{backend}"),
            1,
        ),
    ):
        before = addresses(rig, storage.container, storage.network)
        ports = [(storage.port(port), storage.port(port, "::1")) for port in FRONT]
        if "{backend}" in argv:
            # The process ID is different on each container start.
            pid = (
                rig.docker(
                    "exec",
                    storage.container,
                    "sh",
                    "-ec",
                    'for p in /proc/[0-9]*/comm; do if read -r n < "$p" && [ "$n" = minio ]; then p=${p%/comm}; echo "${p##*/}"; fi; done',
                )
                .decode()
                .strip()
            )
            assert pid.isdecimal() and int(pid) > 1
            argv = (*argv[:-1], pid)
        rig.docker(*argv)
        assert int(rig.docker("wait", storage.container).strip()) == expected_exit
        closed = [(addr, port, False) for addr in before for port in (*FRONT, *BACK)]
        peer(rig, storage.network, tcp=closed)
        peer(
            rig,
            "host",
            tcp=[
                (address, pair[index], False)
                for pair in ports
                for index, address in enumerate(("127.0.0.1", "::1"))
            ],
        )
        rig.docker("start", storage.container)
        storage.wait()
        assert_original()
        assert (
            request(
                storage.port(),
                "PUT",
                f"/{storage.bucket}/{record['key']}",
                raw_headers=(("X-Amz-Content-Sha256", UNSAFE),),
            )
            == GUARD_DENIAL
        )
        results[event] = {"exit": expected_exit, "recovered": True}

    old = addresses(rig, storage.container, storage.network)
    rig.docker("network", "disconnect", storage.network, storage.container)
    peer(
        rig, storage.network, tcp=[(a, p, False) for a in old for p in (*FRONT, *BACK)]
    )
    rig.docker("network", "rm", storage.network)
    rig.create_network(storage.network, register_cleanup=False)
    rig.docker(
        "network",
        "connect",
        "--alias",
        "guarded-storage",
        storage.network,
        storage.container,
    )
    storage.wait()
    assert_original()
    direct = [
        (a, p, False)
        for a in addresses(rig, storage.container, storage.network)
        for p in BACK
    ]
    peer(rig, storage.network, tcp=direct)
    results["bridge_recreated_and_signed_client_reconnected"] = "PASS"
    return results
