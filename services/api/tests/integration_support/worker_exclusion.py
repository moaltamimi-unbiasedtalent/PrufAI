"""Regression exercise shared by the real durable worker integration suites."""

import os
import subprocess
import sys

from app.config import Settings, get_settings
from app.pilot import initialize_worker_exclusion


def assert_pilot_database_excludes_worker(proof, module, queued, *, expire):
    assert queued.status == "QUEUED" and queued.attempt_count == 0
    # A real pre-activation claim is a positive control and supplies a valid token.
    claimed = proof.claim()
    assert claimed.id == queued.id and claimed.claim_token is not None
    initialize_worker_exclusion(
        Settings(
            _env_file=None,
            environment="test",
            database_url=proof.database_url,
            v1_pilot_profile="BERLIN_LID_LOCKED",
        )
    )
    assert get_settings().v1_pilot_profile == proof.settings.v1_pilot_profile == "DISABLED"

    def unexpected_provider(*_args):
        raise AssertionError("excluded worker reached a provider")

    assert proof.execute(claimed, provider_factory=unexpected_provider) is None
    expire(claimed)
    # The row is reclaimable by lease timing, but the database blocks both direct
    # service calls and a distinct worker process with a non-pilot environment.
    assert proof.claim() is None
    expected = proof.job(claimed.id)
    result = subprocess.run(
        [sys.executable, "-m", f"app.{module}.worker", "--once"],
        env={
            **os.environ,
            **proof.environment(),
            "V1_PILOT_PROFILE": "DISABLED",
        },
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0 and result.stdout == b""
    after = proof.job(claimed.id)
    assert after.status == expected.status == "RUNNING"
    assert after.attempt_count == expected.attempt_count == 1
    assert after.claim_token == expected.claim_token == claimed.claim_token
    assert after.lease_expires_at == expected.lease_expires_at
