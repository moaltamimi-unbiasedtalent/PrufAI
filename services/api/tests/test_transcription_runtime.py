"""Adapter contract/unit tests; mocked HTTP is not real provider acceptance."""

import hashlib
import io
import json
import socket
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.speech_transcription.provider import (
    EmptyProviderResult,
    InvalidProviderResult,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.speech_transcription.runtime import (
    CREDENTIAL_REFERENCE,
    ENDPOINT,
    MAX_RESPONSE_BYTES,
    PROCESSING_REFERENCES,
    TEST_TEXT,
    OpenAITranscriptionProvider,
    _multipart,
    build_transcription_provider,
    validate_profile_configuration,
)


def real_snapshot():
    return {
        "provider": "OPENAI",
        "model_name": "gpt-transcribe",
        "adapter_version": "1",
        "profile_version": "a" * 64,
        "language": "de",
        "configuration": {
            "schema_version": "1",
            "adapter_version": "1",
            "languages": ["de"],
            "endpoint": ENDPOINT,
            "region": "EU",
            "deployment_id": "proj_approved-test",
            "credential_reference": CREDENTIAL_REFERENCE,
            "model_configuration_version": "approved-v1",
            "response_format": "json",
            "processing_approval_reference": "approval:technical-test",
            "processing": {key: "policy:technical-test" for key in PROCESSING_REFERENCES},
        },
    }


def fixture_snapshot():
    return {
        "provider": "DETERMINISTIC_TEST",
        "model_name": "fixture-transcriber",
        "adapter_version": "1",
        "language": "de",
        "profile_version": "b" * 64,
        "configuration": {"schema_version": "1", "adapter_version": "1", "languages": ["de"]},
    }


def settings(**kwargs):
    return Settings(_env_file=None, environment="test", **kwargs)


def approved_settings(monkeypatch):
    # Explicitly test credentials only; this does not approve a deployed provider.
    monkeypatch.setenv(CREDENTIAL_REFERENCE, "unit-test-credential-never-sent")
    return settings(speech_transcription_approved_profile_fingerprints=["a" * 64])


def test_production_rejects_test_adapter_even_if_enabled_by_environment(monkeypatch):
    monkeypatch.setenv("SPEECH_TRANSCRIPTION_TEST_ADAPTER_ENABLED", "true")
    with pytest.raises(ValidationError, match="TEST ONLY"):
        Settings(_env_file=None, environment="production", auth_rate_limit_backend="redis")


def test_development_test_provider_requires_explicit_automated_demo_context(monkeypatch):
    monkeypatch.delenv("PRUFAI_E2E_ALLOW_DEMO", raising=False)
    monkeypatch.delenv("PRUFAI_DEMO_ENABLED", raising=False)
    runtime = Settings(
        _env_file=None, environment="development", speech_transcription_test_adapter_enabled=True
    )
    assert (
        validate_profile_configuration(fixture_snapshot(), runtime)["availability"]
        == "UNCONFIGURED"
    )
    monkeypatch.setenv("PRUFAI_E2E_ALLOW_DEMO", "1")
    monkeypatch.setenv("PRUFAI_DEMO_ENABLED", "1")
    assert (
        validate_profile_configuration(fixture_snapshot(), runtime)["availability"] == "TEST_ONLY"
    )


def test_incompatible_original_size_is_rejected_before_queueing_or_external_io(monkeypatch):
    snapshot = real_snapshot()
    snapshot["source"] = {"byte_size": 25_000_001}
    runtime = approved_settings(monkeypatch)
    assert (
        validate_profile_configuration(snapshot, runtime)["unavailable_reason"]
        == "UNSUPPORTED_AUDIO_SIZE"
    )


def test_no_implicit_test_fallback_or_external_processing_approval(monkeypatch):
    snapshot = real_snapshot()
    monkeypatch.delenv(CREDENTIAL_REFERENCE, raising=False)
    assert validate_profile_configuration(snapshot, settings())["availability"] == "UNCONFIGURED"
    with pytest.raises(ProviderUnavailable, match="UNCONFIGURED"):
        build_transcription_provider(snapshot, settings())
    assert (
        validate_profile_configuration(fixture_snapshot(), settings())["availability"]
        == "UNCONFIGURED"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("endpoint", "http://127.0.0.1/secrets"),
        ("endpoint", "https://api.openai.com/v1/audio/transcriptions"),
        ("region", "US"),
        ("credential_reference", "AUTH_JWT_SECRET"),
        ("processing", {}),
        ("deployment_id", "invalid\r\nHeader:value"),
        ("api_key", "never-allowed"),
        ("response_format", "text"),
        ("languages", ["en"]),
    ],
)
def test_unapproved_provider_options_fail_closed(monkeypatch, key, value):
    snapshot = real_snapshot()
    snapshot["configuration"][key] = value
    result = validate_profile_configuration(snapshot, approved_settings(monkeypatch))
    assert result["availability"] == "UNCONFIGURED"
    assert "never-allowed" not in json.dumps(result)


def test_missing_credential_or_nonmatching_approved_version_is_unconfigured(monkeypatch):
    runtime = approved_settings(monkeypatch)
    snapshot = real_snapshot()
    snapshot["profile_version"] = "c" * 64
    assert validate_profile_configuration(snapshot, runtime)["availability"] == "UNCONFIGURED"
    monkeypatch.delenv(CREDENTIAL_REFERENCE)
    assert (
        validate_profile_configuration(real_snapshot(), runtime)["availability"] == "UNCONFIGURED"
    )


@pytest.mark.parametrize(
    "delay,scenario",
    [
        (True, "success"),
        (-1, "success"),
        (31, "success"),
        (float("nan"), "success"),
        (0, []),
        (0, "unknown"),
    ],
)
def test_test_only_configuration_is_bounded_and_typed(delay, scenario):
    snapshot = fixture_snapshot()
    snapshot["configuration"].update(delay_seconds=delay, scenario=scenario)
    assert (
        validate_profile_configuration(
            snapshot, settings(speech_transcription_test_adapter_enabled=True)
        )["availability"]
        == "UNCONFIGURED"
    )


def test_test_execution_is_explicit_and_carries_exact_identity():
    snapshot = fixture_snapshot()
    provider = build_transcription_provider(
        snapshot, settings(speech_transcription_test_adapter_enabled=True)
    )
    audio = b"technical-input-not-recognized"
    config = {
        **snapshot["configuration"],
        "execution_id": "execution-unit-test",
        "audio_sha256": hashlib.sha256(audio).hexdigest(),
    }
    result = provider.transcribe(audio, language="de", configuration=config, limits={})
    assert result.transcript == TEST_TEXT
    assert result.segments == ()
    assert result.metadata == {
        "execution_id": "execution-unit-test",
        "audio_sha256": config["audio_sha256"],
        "language": "de",
        "test_only": True,
    }


class Response(io.BytesIO):
    status = 200

    def getheader(self, name):
        return None


class Connection:
    sock = None

    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def invoke(monkeypatch, response):
    runtime = approved_settings(monkeypatch)
    connection = Connection(response)
    connections = []

    def create(host, **kwargs):
        connections.append((host, kwargs))
        return connection

    monkeypatch.setattr("app.speech_transcription.runtime.http.client.HTTPSConnection", create)
    audio = b"verified-audio-unit-contract"
    result = OpenAITranscriptionProvider(deepcopy(real_snapshot()), runtime).transcribe(
        audio,
        language="de",
        configuration={
            "execution_id": "run-unit",
            "source_content_type": "audio/webm",
            "audio_sha256": hashlib.sha256(audio).hexdigest(),
        },
        limits={},
    )
    return result, connection, connections


def test_genuine_adapter_http_contract_with_mocked_transport_is_not_provider_acceptance(
    monkeypatch,
):
    result, connection, connections = invoke(
        monkeypatch,
        Response(json.dumps({"text": "Guten Tag.", "languages": [{"code": "de"}]}).encode()),
    )
    assert connections[0][0] == "eu.api.openai.com"
    assert connections[0][1]["timeout"] == 45
    args, kwargs = connection.requests[0]
    assert args == ("POST", "/v1/audio/transcriptions")
    assert kwargs["headers"]["OpenAI-Project"] == "proj_approved-test"
    assert b"verified-audio-unit-contract" in kwargs["body"]
    assert b'filename="audio.webm"' in kwargs["body"]
    assert result.transcript == "Guten Tag." and result.segments == ()
    assert result.metadata["test_only"] is False
    assert connection.closed


@pytest.mark.parametrize(
    "payload,exception",
    [
        ({"text": "", "languages": [{"code": "de"}]}, EmptyProviderResult),
        ({"text": 1}, InvalidProviderResult),
        ({"text": "Hallo", "languages": []}, InvalidProviderResult),
        ({"text": "Hello", "languages": [{"code": "en"}]}, InvalidProviderResult),
        ([], InvalidProviderResult),
    ],
)
def test_genuine_adapter_rejects_empty_malformed_or_wrong_language(monkeypatch, payload, exception):
    with pytest.raises(exception):
        invoke(monkeypatch, Response(json.dumps(payload).encode()))


def test_provider_redirect_is_never_followed_or_error_body_exposed(monkeypatch):
    response = Response(b"PRIVATE_PROVIDER_ERROR_AND_SPEECH")
    response.status = 302
    with pytest.raises(ProviderUnavailable) as exc:
        invoke(monkeypatch, response)
    assert str(exc.value) == "PROVIDER_REQUEST_REJECTED"
    assert response.tell() == 0


def test_provider_response_size_is_bounded_even_without_length_header(monkeypatch):
    with pytest.raises(InvalidProviderResult, match="TOO_LARGE"):
        invoke(monkeypatch, Response(b"x" * (MAX_RESPONSE_BYTES + 1)))


def test_provider_timeout_is_safe(monkeypatch):
    class TimeoutResponse(Response):
        def read1(self, _size):
            raise socket.timeout("PRIVATE_PROVIDER_DETAILS")

    with pytest.raises(ProviderTimeout) as exc:
        invoke(monkeypatch, TimeoutResponse())
    assert str(exc.value) == "PROVIDER_TIMEOUT"


@pytest.mark.parametrize(
    "mime,extension",
    [
        ("audio/webm;codecs=opus", "webm"),
        ("audio/ogg;codecs=opus", "ogg"),
        ("audio/mp4; codecs=mp4a.40.2", "m4a"),
    ],
)
def test_provider_accepts_verified_browser_mime_parameters_without_relaying_them(mime, extension):
    body, _ = _multipart(b"original-verified-input", language="de", content_type=mime)
    assert f'filename="audio.{extension}"'.encode() in body
    assert mime.split(";", 1)[0].encode() in body
    assert b"codecs=" not in body
