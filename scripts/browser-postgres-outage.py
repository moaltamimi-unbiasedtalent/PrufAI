"""Stop or restart only the explicitly designated disposable browser database."""

import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

from app.evaluation.disposable import assert_disposable_database

IMAGE = "pgvector/pgvector:pg16@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f"
LOCAL_LABEL = "org.prufai.browser-disposable"


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"verify", "stop", "start"}:
        raise SystemExit("Expected verify, stop or start.")
    url = os.environ["DATABASE_URL"]
    name = assert_disposable_database(url, report=False)
    container_id = os.environ.get("PRUFAI_E2E_POSTGRES_CONTAINER_ID", "")
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise SystemExit("The full disposable PostgreSQL container ID is required.")
    raw = subprocess.check_output(
        ["docker", "container", "inspect", container_id], timeout=8
    )
    container = json.loads(raw)[0]
    config = container["Config"]
    published = container["HostConfig"]["PortBindings"].get("5432/tcp", [])
    github_service = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("CI") == "true"
        and name == "prufai_browser"
    )
    local_service = config["Labels"].get(LOCAL_LABEL) == name
    if not (
        container["Id"] == container_id
        and config["Image"] == IMAGE
        and f"POSTGRES_DB={name}" in config["Env"]
        and published
        and all(int(binding["HostPort"]) == (urlsplit(url).port or 5432) for binding in published)
        and all(mount["Type"] == "volume" for mount in container["Mounts"])
        and (github_service or local_service)
    ):
        raise SystemExit("Refusing to control an unverified PostgreSQL container.")
    if sys.argv[1] == "stop":
        subprocess.run(["docker", "stop", "-t", "4", container_id], check=True, timeout=15)
    elif sys.argv[1] == "start":
        subprocess.run(["docker", "start", container_id], check=True, timeout=15)


if __name__ == "__main__":
    main()
