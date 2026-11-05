"""Fail closed before evaluation acceptance can mutate a database.

This guard deliberately recognizes only the owned local acceptance server and
GitHub Actions' ephemeral service. It has no app Settings/default-URL fallback.
"""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import unquote, urlsplit


def assert_disposable_database(url: str, expected_name: str | None = None) -> str:
    expected = expected_name or os.environ.get("PRUFAI_DISPOSABLE_DATABASE", "")
    target = urlsplit(url)
    try:
        port = target.port or 5432
    except ValueError as exc:
        raise ValueError("DISPOSABLE_DATABASE_REQUIRED") from exc
    name = unquote(target.path.removeprefix("/"))
    if (
        target.scheme != "postgresql+psycopg"
        or target.hostname not in {"127.0.0.1", "localhost"}
        or target.username != "prufai"
        or target.query
        or target.fragment
        or not expected
        or name != expected
        or "/" in name
    ):
        raise ValueError("DISPOSABLE_DATABASE_REQUIRED")
    local = port == 55438 and bool(re.fullmatch(r"prufai_eval2_[a-z]+_[a-f0-9]{32}", name))
    ci = (
        sys.platform == "linux"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("CI") == "true"
        and port == 5432
        and (
            name in {"prufai", "prufai_browser"}
            or bool(re.fullmatch(r"prufai_eval2_[a-z]+_[a-f0-9]{32}", name))
        )
    )
    if not (local or ci):
        raise ValueError("DISPOSABLE_DATABASE_REQUIRED")
    environment = "github-actions-ephemeral" if ci else "local-isolated"
    print(
        f"Verified disposable PostgreSQL target: host={target.hostname} "
        f"port={port} database={name} environment={environment}",
        flush=True,
    )
    return name
