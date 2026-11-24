"""Synthetic configuration/startup checks; never contact deployment services."""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config import Settings


def production(**overrides):
    values = dict(
        environment="production",
        auth_jwt_secret=secrets.token_urlsafe(48),
        auth_cookie_secure=True,
        auth_rate_limit_backend="redis",
        object_storage_backend="s3",
        knowledge_embedding_backend="openai",
        knowledge_reranker_backend="openai",
    )
    values.update(overrides)
    return values


@pytest.fixture(autouse=True)
def isolated_environment():
    with patch.dict(os.environ, {}, clear=True):
        yield


@pytest.mark.parametrize(
    "key_case",
    [
        "omitted",
        "development",
        "placeholder",
        "change-me",
        "empty",
        "short",
        "spaces",
        "padded",
        "uniform",
    ],
)
@pytest.mark.parametrize("cookie", [None, False, True])
def test_production_rejects_insecure_keys_without_echoing_inputs(key_case, cookie, caplog):
    keys = {
        "development": Settings.model_fields["auth_jwt_secret"].default,
        "placeholder": "replace-with-a-random-32-byte-secret",
        "change-me": "change-me-" + "x" * 48,
        "empty": "",
        "short": "synthetic-short-key",
        "spaces": " " * 48,
        "padded": " " + secrets.token_urlsafe(48),
        "uniform": "x" * 48,
    }
    values = production()
    if key_case == "omitted":
        values.pop("auth_jwt_secret")
    else:
        values["auth_jwt_secret"] = keys[key_case]
    if cookie is None:
        values.pop("auth_cookie_secure")
    else:
        values["auth_cookie_secure"] = cookie
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    # Assertions operate on a boolean so pytest cannot echo signing material.
    key = values.get("auth_jwt_secret")
    if key and key.strip():
        outputs = [
            str(caught.value),
            repr(caught.value),
            caught.value.json(),
            repr(caught.value.errors()),
            caplog.text,
        ]
        if any(key in output for output in outputs):
            raise AssertionError("configuration diagnostic exposed signing material")


@pytest.mark.parametrize("cookie", [None, False, "false", "0"])
def test_production_requires_secure_cookie(cookie):
    values = production()
    if cookie is None:
        values.pop("auth_cookie_secure")
    else:
        values["auth_cookie_secure"] = cookie
    with pytest.raises(ValidationError, match="AUTH_COOKIE_SECURE=true"):
        Settings(_env_file=None, **values)


def test_generated_key_is_preserved_and_not_serialized(caplog):
    values = production()
    key = values["auth_jwt_secret"]
    settings = Settings(_env_file=None, **values)
    if settings.auth_jwt_secret != key:
        raise AssertionError("signing key was replaced")
    for output in [
        repr(settings),
        str(settings),
        settings.model_dump_json(),
        repr(settings.model_dump()),
        caplog.text,
    ]:
        if key in output:
            raise AssertionError("settings exposed signing material")
    assert settings.auth_cookie_secure is True
    assert settings.auth_jwt_issuer == "prufai-api"
    assert settings.auth_jwt_audience == "prufai-web"
    # Environment and JSON inputs use the same centralized model validation.
    assert Settings.model_validate_json(json.dumps(values)).auth_cookie_secure
    os.environ.update({name.upper(): str(value) for name, value in values.items()})
    assert Settings(_env_file=None).auth_cookie_secure


@pytest.mark.parametrize("environment", ["development", "test"])
def test_nonproduction_defaults_unchanged(environment):
    settings = Settings(_env_file=None, environment=environment)
    assert settings.auth_cookie_secure is False
    assert settings.auth_rate_limit_backend == "memory"


@pytest.mark.parametrize(
    "override,match",
    [
        ({"auth_rate_limit_backend": "memory"}, "must use Redis"),
        ({"speech_transcription_test_adapter_enabled": True}, "TEST ONLY"),
        ({"writing_test_adapter_enabled": True}, "TEST ONLY"),
        ({"speaking_test_adapter_enabled": True}, "TEST ONLY"),
        ({"nonproduction_prompt_validation_enabled": True}, "forbidden"),
        ({"auth_enrollment_delivery_mode": "TEST_ONLY"}, "forbidden"),
        ({"v1_pilot_profile": "BERLIN_LID_SYNTHETIC"}, "forbidden"),
    ],
)
def test_existing_deployment_guards_preserved(override, match):
    with pytest.raises(ValidationError, match=match):
        Settings(_env_file=None, **production(**override))


def test_other_validation_errors_do_not_echo_valid_key():
    values = production(auth_access_token_ttl_minutes=999)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    if values["auth_jwt_secret"] in caught.value.json():
        raise AssertionError("unrelated validation exposed signing material")


@pytest.mark.parametrize("valid", [False, True])
def test_isolated_application_startup_without_network(valid, tmp_path):
    # Import and lifespan run in a new process with no .env and network forbidden.
    values = production()
    key = values["auth_jwt_secret"]
    if not valid:
        values.pop("auth_jwt_secret")
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        **{k.upper(): str(v) for k, v in values.items()},
    }
    code = """
import asyncio,socket
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected network'))
from app.main import app
async def check():
 async with app.router.lifespan_context(app): pass
asyncio.run(check())
print('ISOLATED_STARTUP_PASS')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if key in result.stdout + result.stderr:
        raise AssertionError("startup diagnostic exposed signing material")
    assert (result.returncode == 0) is valid
    if valid:
        assert "ISOLATED_STARTUP_PASS" in result.stdout
    else:
        assert "AUTH_JWT_SECRET" in result.stderr
