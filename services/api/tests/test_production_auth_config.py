"""Synthetic configuration/startup checks; never contact deployment services."""

import io
import json
import logging
import os
import secrets
import subprocess
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings

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
@pytest.mark.parametrize("entry", ["constructor", "json"])
def test_production_rejects_insecure_keys_without_echoing_inputs(key_case, cookie, entry, caplog):
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
        if entry == "json":
            Settings.model_validate_json(json.dumps(values))
        else:
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
@pytest.mark.parametrize("entry", ["constructor", "json"])
def test_production_requires_secure_cookie(cookie, entry):
    values = production()
    if cookie is None:
        values.pop("auth_cookie_secure")
    else:
        values["auth_cookie_secure"] = cookie
    with pytest.raises(ValidationError, match="AUTH_COOKIE_SECURE=true"):
        if entry == "json":
            Settings.model_validate_json(json.dumps(values))
        else:
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


def require_private_diagnostics(error, key):
    # Never pass source JSON or signing material to pytest assertion rendering.
    outputs = [
        str(error),
        repr(error),
        repr(error.errors()),
        error.json(),
        "".join(traceback.format_exception(error)),
    ]
    with io.StringIO() as buffer:
        logger = logging.Logger("private-configuration-negative-test")
        handler = logging.StreamHandler(buffer)
        logger.addHandler(handler)
        try:
            logger.error("%s", error.json())
            outputs.append(buffer.getvalue())
        finally:
            logger.removeHandler(handler)
            handler.close()
        if any(key in output for output in outputs):
            raise AssertionError("configuration diagnostic exposed signing material")


@pytest.mark.parametrize("kind", ["str", "bytes", "bytearray"])
@pytest.mark.parametrize("case", ["truncated", "trailing_comma", "invalid_prefix"])
def test_malformed_settings_json_is_rejected_without_source_or_exception_context(case, kind):
    calls = []

    class ObservedSettings(Settings):
        @model_validator(mode="wrap")
        @classmethod
        def observe(cls, value, handler):
            calls.append(True)
            return handler(value)

    values = production()
    source = json.dumps(values)
    malformed = {
        "truncated": source[:-1],
        "trailing_comma": source[:-1] + ",}",
        "invalid_prefix": "invalid " + source,
    }[case]
    data = malformed if kind == "str" else malformed.encode()
    if kind == "bytearray":
        data = bytearray(data)
    with pytest.raises(ValidationError) as caught:
        ObservedSettings.model_validate_json(data)
    require_private_diagnostics(caught.value, values["auth_jwt_secret"])
    assert calls == []  # Parser rejection precedes every model-wrap validator.
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert caught.value.errors(include_url=False) == [
        {
            "type": "json_invalid",
            "loc": (),
            "msg": "Invalid JSON: Malformed configuration JSON",
            "input": "[redacted]",
            "ctx": {"error": "Malformed configuration JSON"},
        }
    ]


@pytest.mark.parametrize("kind", [bytes, bytearray], ids=["bytes", "bytearray"])
def test_invalid_json_encoding_does_not_expose_configuration(kind):
    values = production()
    data = kind(json.dumps(values).encode() + b"\xff")
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate_json(data)
    require_private_diagnostics(caught.value, values["auth_jwt_secret"])
    assert caught.value.errors()[0]["type"] == "json_invalid"
    assert caught.value.__context__ is None


def test_wrong_json_input_type_is_sanitized():
    values = production()
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate_json(values)
    require_private_diagnostics(caught.value, values["auth_jwt_secret"])
    assert caught.value.errors()[0]["type"] == "json_type"
    assert caught.value.errors()[0]["input"] == "[redacted]"
    assert caught.value.__context__ is None


def test_unsafe_parser_diagnostic_negative_control_is_detected_in_memory():
    values = production()
    # Deliberately bypass the protected public entry point. Nothing is printed,
    # logged globally or written to a file, even when the detector fails.
    with pytest.raises(ValidationError) as caught:
        Settings.__pydantic_validator__.validate_json(json.dumps(values)[:-1])
    with pytest.raises(AssertionError, match="diagnostic exposed signing material"):
        require_private_diagnostics(caught.value, values["auth_jwt_secret"])


@pytest.mark.parametrize("kind", [str, bytes, bytearray], ids=["str", "bytes", "bytearray"])
def test_valid_json_preserves_supplied_key_and_json_validation(kind):
    values = production()
    source = json.dumps(values)
    data = source if kind is str else kind(source.encode())
    settings = Settings.model_validate_json(data)
    if settings.auth_jwt_secret != values["auth_jwt_secret"]:
        raise AssertionError("signing key was replaced")
    assert settings.auth_cookie_secure is True


@pytest.mark.parametrize(
    "override,category",
    [
        ({"auth_access_token_ttl_minutes": 999}, "value_error"),
        ({"auth_cookie_samesite": "invalid"}, "literal_error"),
        ({"auth_cookie_secure": False}, "value_error"),
        ({"auth_jwt_secret": "replace-with-a-random-32-byte-secret"}, "value_error"),
        ({"auth_rate_limit_backend": "memory"}, "value_error"),
        ({"object_storage_backend": "unsupported"}, "literal_error"),
        ({"knowledge_embedding_backend": "unsupported"}, "literal_error"),
        ({"knowledge_reranker_backend": "unsupported"}, "literal_error"),
        ({"writing_test_adapter_enabled": True}, "value_error"),
    ],
)
def test_valid_json_retains_unrelated_validation_and_production_guards(override, category):
    values = production(**override)
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate_json(json.dumps(values))
    require_private_diagnostics(caught.value, values["auth_jwt_secret"])
    assert caught.value.errors()[0]["type"] == category


def test_json_validation_forwards_native_options(monkeypatch):
    expected = Settings(_env_file=None, environment="test")
    seen = []

    def native(cls, data, **options):
        seen.append((cls, data, options))
        return expected

    monkeypatch.setattr(BaseSettings, "model_validate_json", classmethod(native))
    options = dict(
        strict=True, extra="forbid", context={"review": True}, by_alias=True, by_name=True
    )
    assert Settings.model_validate_json("{}", **options) is expected
    assert seen == [(Settings, "{}", options)]


@pytest.mark.parametrize("case", ["missing_key", "missing_secure", "false_secure", "valid"])
def test_isolated_application_startup_without_network(case, tmp_path):
    # Import and lifespan run in a new process with no .env and network forbidden.
    values = production()
    key = values["auth_jwt_secret"]
    if case == "missing_key":
        values.pop("auth_jwt_secret")
    elif case == "missing_secure":
        values.pop("auth_cookie_secure")
    elif case == "false_secure":
        values["auth_cookie_secure"] = False
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        **{k.upper(): str(v) for k, v in values.items()},
    }
    code = """
import asyncio,secrets,socket
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected network'))
secrets.token_urlsafe=lambda *a,**k: (_ for _ in ()).throw(AssertionError('startup key generation'))
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
    assert (result.returncode == 0) is (case == "valid")
    assert "unexpected network" not in result.stderr
    assert "startup key generation" not in result.stderr
    if case == "valid":
        assert "ISOLATED_STARTUP_PASS" in result.stdout
    else:
        field = "AUTH_JWT_SECRET" if case == "missing_key" else "AUTH_COOKIE_SECURE"
        assert field in result.stderr
