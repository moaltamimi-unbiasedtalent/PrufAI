"""Synthetic selector-policy checks; no datastore or provider is contacted."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.knowledge.storage import S3CompatibleObjectStorage, StorageError, build_object_storage
from tests.test_production_auth_config import production, require_private_diagnostics

CASES = [
    ("valid", {}, (), None),
    ("memory", {"object_storage_backend": "memory"}, (), "object_storage_backend=s3"),
    ("trial", {"object_storage_backend": "aws-s3-trial"}, (), "object_storage_backend=s3"),
    (
        "approved_trial",
        {"object_storage_backend": "aws-s3-trial", "object_storage_aws_trial_approved": True},
        (),
        "object_storage_backend=s3",
    ),
    ("embedding", {"knowledge_embedding_backend": "deterministic"}, (), "production embeddings"),
    ("reranker", {"knowledge_reranker_backend": "deterministic"}, (), "production reranking"),
    (
        "combined",
        {
            "object_storage_backend": "memory",
            "knowledge_embedding_backend": "deterministic",
            "knowledge_reranker_backend": "deterministic",
        },
        (),
        "object_storage_backend=s3",
    ),
    ("omitted_storage", {}, ("object_storage_backend",), "object_storage_backend=s3"),
    ("omitted_embedding", {}, ("knowledge_embedding_backend",), "production embeddings"),
    ("omitted_reranker", {}, ("knowledge_reranker_backend",), "production reranking"),
    (
        "omitted_all",
        {},
        ("object_storage_backend", "knowledge_embedding_backend", "knowledge_reranker_backend"),
        "object_storage_backend=s3",
    ),
]


@pytest.fixture(autouse=True)
def isolated_configuration():
    # JSON validation uses the same BaseSettings constructor internally, but
    # _env_file is not a public model_validate_json keyword argument.
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(Settings.model_config, {"env_file": None}),
    ):
        yield


def load(values, entry):
    return (
        Settings(_env_file=None, **values)
        if entry == "constructor"
        else Settings.model_validate_json(json.dumps(values))
    )


@pytest.mark.parametrize("entry", ["constructor", "json"])
@pytest.mark.parametrize("environment", ["development", "test", "production"])
@pytest.mark.parametrize("case,overrides,omissions,reason", CASES, ids=[c[0] for c in CASES])
def test_backend_selector_policy(entry, environment, case, overrides, omissions, reason):
    del case
    values = production(environment=environment, **overrides)
    for field in omissions:
        values.pop(field)
    if environment == "production" and reason:
        with pytest.raises(ValidationError) as caught:
            load(values, entry)
        require_private_diagnostics(caught.value, values["auth_jwt_secret"])
        errors = caught.value.errors(include_input=False, include_context=False)
        assert errors[0]["type"] == "value_error"
        assert reason in errors[0]["msg"]  # The backend, not an unrelated auth rule, rejected it.
        return
    settings = load(values, entry)
    assert settings.environment == environment
    for field in (
        "object_storage_backend",
        "knowledge_embedding_backend",
        "knowledge_reranker_backend",
    ):
        expected = values.get(field, Settings.model_fields[field].default)
        assert getattr(settings, field) == expected
    if settings.auth_jwt_secret != values["auth_jwt_secret"]:
        raise AssertionError("supplied signing key changed")
    assert settings.auth_cookie_secure is True
    assert settings.auth_rate_limit_backend == "redis"


@pytest.mark.parametrize("entry", ["constructor", "json"])
@pytest.mark.parametrize("environment_first", [True, False])
@pytest.mark.parametrize(
    "overrides,reason", [(c[1], c[3]) for c in CASES if c[0] in {"memory", "embedding", "reranker"}]
)
def test_production_policy_ignores_input_order(entry, environment_first, overrides, reason):
    values = production(**overrides)
    environment = values.pop("environment")
    values = (
        {"environment": environment, **values}
        if environment_first
        else {**values, "environment": environment}
    )
    with pytest.raises(ValidationError) as caught:
        load(values, entry)
    require_private_diagnostics(caught.value, values["auth_jwt_secret"])
    assert reason in caught.value.errors(include_input=False)[0]["msg"]


@pytest.mark.parametrize("environment", ["development", "test", "production"])
@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("entry", ["constructor", "json"])
def test_trial_factory_approval_boundary_unchanged(environment, approved, entry, monkeypatch):
    calls = []
    client = object()

    def sdk_client(*_args, **_kwargs):
        calls.append(True)
        return client

    monkeypatch.setattr("boto3.client", sdk_client)
    values = production(
        environment=environment,
        object_storage_backend="aws-s3-trial",
        object_storage_aws_trial_approved=approved,
        object_storage_endpoint="https://s3.eu-central-1.amazonaws.com",
        object_storage_region="eu-central-1",
        object_storage_secure=True,
        object_storage_bucket="synthetic-trial-review",
        object_storage_access_key="synthetic-access",
        object_storage_secret_key="synthetic-secret",
        object_storage_session_token="synthetic-token",
    )
    if environment == "production":
        with pytest.raises(ValidationError) as caught:
            load(values, entry)
        require_private_diagnostics(caught.value, values["auth_jwt_secret"])
        assert "object_storage_backend=s3" in str(caught.value)
    elif environment != "test" or not approved:
        with pytest.raises(StorageError, match="approved synthetic"):
            build_object_storage(load(values, entry))
    else:
        storage = build_object_storage(load(values, entry))
        assert isinstance(storage, S3CompatibleObjectStorage)
        assert storage.client is client
    assert len(calls) == int(environment == "test" and approved)


@pytest.mark.parametrize(
    "case,overrides,omissions,reason",
    [
        c
        for c in CASES
        if c[0] in {"valid", "memory", "approved_trial", "embedding", "reranker", "omitted_all"}
    ],
    ids=[
        c[0]
        for c in CASES
        if c[0] in {"valid", "memory", "approved_trial", "embedding", "reranker", "omitted_all"}
    ],
)
def test_backend_policy_applies_before_startup(case, overrides, omissions, reason, tmp_path):
    values = production(**overrides)
    for field in omissions:
        values.pop(field)
    key = values["auth_jwt_secret"]
    environment = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        **{k.upper(): str(v) for k, v in values.items()},
    }
    code = """
import asyncio,secrets,socket
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
secrets.token_urlsafe=lambda *a,**k: (_ for _ in ()).throw(AssertionError('KEYGEN_FORBIDDEN'))
from app.main import app
async def check():
 async with app.router.lifespan_context(app): print('BACKEND_STARTUP_READY')
asyncio.run(check())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    if key in output:
        raise AssertionError("startup diagnostic exposed signing material")
    assert "NETWORK_FORBIDDEN" not in output
    assert "KEYGEN_FORBIDDEN" not in output
    assert (result.returncode == 0) is (case == "valid")
    if reason:
        assert reason in output
    else:
        assert "BACKEND_STARTUP_READY" in output
