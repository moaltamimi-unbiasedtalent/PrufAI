"""Explicit execution adapters. External transfer is closed until deployment approval.

No provider is selected from the current mutable registry during recovery. A job's
immutable snapshot is the complete input to this boundary. Never log request bodies,
provider errors, credential values or response bodies.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import socket
import ssl
import threading
import time
import uuid
from typing import Any

from app.config import Settings
from app.speech_transcription.provider import (
    EmptyProviderResult,
    InvalidProviderResult,
    ProviderTimeout,
    ProviderUnavailable,
    SpeechTranscriptionProvider,
    TranscriptionResult,
)

ENDPOINT = "https://eu.api.openai.com/v1/audio/transcriptions"
CREDENTIAL_REFERENCE = "PRUFAI_SPEECH_OPENAI_API_KEY"
MAX_AUDIO_BYTES = 25_000_000
MAX_RESPONSE_BYTES = 2_000_000
TEST_TEXT = "TEST ONLY — technical execution fixture; no speech recognition was performed."
PROCESSING_REFERENCES = (
    "legal_basis_reference",
    "consent_policy_reference",
    "data_processing_agreement_reference",
    "residency_approval_reference",
    "retention_policy_reference",
    "logging_training_controls_reference",
    "audio_transfer_authorization_reference",
    "data_minimization_reference",
)
_SAFE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}")
_LANGUAGE = re.compile(r"[a-z]{2}")


def _reference(value: Any) -> bool:
    return isinstance(value, str) and _SAFE_REFERENCE.fullmatch(value) is not None


def validate_profile_configuration(snapshot: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Safe availability only. No network I/O, secret values or compliance assertion."""
    test_only = snapshot.get("provider") == "DETERMINISTIC_TEST"

    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "availability": "UNCONFIGURED",
            "unavailable_reason": reason,
            "test_only": test_only,
        }

    config = snapshot.get("configuration")
    if not isinstance(config, dict) or config.get("schema_version") != "1":
        return unavailable("UNSUPPORTED_CONFIGURATION")
    if snapshot.get("adapter_version") != "1":
        return unavailable("UNSUPPORTED_ADAPTER_VERSION")
    languages = config.get("languages")
    if (
        not isinstance(languages, list)
        or not 1 <= len(languages) <= 20
        or any(not isinstance(v, str) or not _LANGUAGE.fullmatch(v) for v in languages)
        or (snapshot.get("language") is not None and snapshot["language"] not in languages)
    ):
        return unavailable("UNSUPPORTED_LANGUAGE")
    if test_only:
        technical_test = settings.environment == "test" or (
            settings.environment == "development"
            and os.getenv("PRUFAI_E2E_ALLOW_DEMO") == "1"
            and os.getenv("PRUFAI_DEMO_ENABLED") == "1"
        )
        if not technical_test or not settings.speech_transcription_test_adapter_enabled:
            return unavailable("TEST_ADAPTER_DISABLED")
        if snapshot.get("model_name") != "fixture-transcriber":
            return unavailable("UNSUPPORTED_MODEL")
        if set(config) - {
            "schema_version",
            "adapter_version",
            "languages",
            "scenario",
            "delay_seconds",
        }:
            return unavailable("UNSUPPORTED_CONFIGURATION")
        delay = config.get("delay_seconds", 0)
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(delay)
            or not 0 <= delay <= 30
            or not isinstance(config.get("scenario", "success"), str)
            or config.get("scenario", "success")
            not in {"success", "timeout", "unavailable", "malformed", "empty"}
        ):
            return unavailable("UNSUPPORTED_CONFIGURATION")
        return {"availability": "TEST_ONLY", "unavailable_reason": None, "test_only": True}
    if snapshot.get("provider") != "OPENAI" or snapshot.get("model_name") != "gpt-transcribe":
        return unavailable("UNSUPPORTED_PROVIDER_OR_MODEL")
    source = snapshot.get("source", {})
    if isinstance(source, dict) and source:
        size = source.get("byte_size")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_AUDIO_BYTES:
            return unavailable("UNSUPPORTED_AUDIO_SIZE")
    allowed = {
        "schema_version",
        "adapter_version",
        "languages",
        "endpoint",
        "region",
        "deployment_id",
        "credential_reference",
        "model_configuration_version",
        "response_format",
        "processing_approval_reference",
        "processing",
    }
    if set(config) - allowed or config.get("response_format") != "json":
        return unavailable("UNSUPPORTED_CONFIGURATION")
    if config.get("endpoint") != ENDPOINT or config.get("region") != "EU":
        return unavailable("UNAPPROVED_ENDPOINT_OR_REGION")
    if not all(
        _reference(config.get(key))
        for key in ("deployment_id", "model_configuration_version", "processing_approval_reference")
    ):
        return unavailable("PROCESSING_APPROVAL_REQUIRED")
    processing = config.get("processing")
    if (
        not isinstance(processing, dict)
        or set(processing) != set(PROCESSING_REFERENCES)
        or not all(_reference(processing.get(key)) for key in PROCESSING_REFERENCES)
    ):
        return unavailable("PROCESSING_APPROVAL_REQUIRED")
    if config.get("credential_reference") != CREDENTIAL_REFERENCE:
        return unavailable("UNAPPROVED_CREDENTIAL_REFERENCE")
    if (
        snapshot.get("profile_version")
        not in settings.speech_transcription_approved_profile_fingerprints
    ):
        return unavailable("DEPLOYMENT_APPROVAL_REQUIRED")
    secret = os.getenv(CREDENTIAL_REFERENCE, "")
    if not secret or any(ord(c) < 33 or ord(c) > 126 for c in secret):
        return unavailable("CREDENTIAL_UNAVAILABLE")
    return {"availability": "CONFIGURED", "unavailable_reason": None, "test_only": False}


def _identity(configuration: dict[str, Any], language: str, *, test_only: bool) -> dict[str, Any]:
    return {
        "execution_id": configuration.get("execution_id"),
        "audio_sha256": configuration.get("audio_sha256"),
        "language": language,
        "test_only": test_only,
    }


class TestOnlyExecutionProvider:
    """Automated technical evidence only; never recognizes or manufactures speech."""

    provider = "DETERMINISTIC_TEST"
    __test__ = False
    model_name = "fixture-transcriber"
    adapter_version = "1"

    def transcribe(
        self, audio: bytes, *, language: str, configuration: dict[str, Any], limits: dict[str, int]
    ) -> TranscriptionResult:
        del limits
        if hashlib.sha256(audio).hexdigest() != configuration.get("audio_sha256"):
            raise InvalidProviderResult("SOURCE_CHECKSUM_MISMATCH")
        time.sleep(float(configuration.get("delay_seconds", 0)))
        scenario = configuration.get("scenario", "success")
        if scenario == "timeout":
            raise ProviderTimeout("TEST_ONLY_TIMEOUT")
        if scenario == "unavailable":
            raise ProviderUnavailable("TEST_ONLY_UNAVAILABLE")
        return TranscriptionResult(
            transcript="" if scenario in {"malformed", "empty"} else TEST_TEXT,
            provider=self.provider,
            model_name=self.model_name,
            adapter_version=self.adapter_version,
            metadata=_identity(configuration, language, test_only=True),
        )


def _multipart(audio: bytes, *, language: str, content_type: str) -> tuple[bytes, str]:
    # The recorded asset retains its exact MIME/provenance. Only the outbound
    # multipart metadata uses a canonical allowlisted type, without parameters.
    content_type = content_type.split(";", 1)[0].strip().lower()
    extension = {
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
    }.get(content_type)
    if extension is None:
        raise InvalidProviderResult("UNSUPPORTED_AUDIO_FORMAT")
    boundary = "prufai-" + uuid.uuid4().hex
    fields = {"model": "gpt-transcribe", "language": language, "response_format": "json"}
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    ]
    parts.extend(
        [
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="audio.{extension}"'
                f"\r\nContent-Type: {content_type}\r\n\r\n"
            ).encode(),
            audio,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(parts), boundary


class OpenAITranscriptionProvider:
    """Bounded HTTPS adapter for an explicitly approved EU OpenAI project.

    Uses the documented gpt-transcribe JSON contract. The provider supplies no
    timestamps in this contract, so this adapter never invents segments/confidence.
    A profile pins the model identifier and configuration, not undocumented weights.
    """

    provider = "OPENAI"
    model_name = "gpt-transcribe"
    adapter_version = "1"

    def __init__(self, snapshot: dict[str, Any], settings: Settings) -> None:
        self.snapshot = snapshot
        self.settings = settings

    def transcribe(
        self, audio: bytes, *, language: str, configuration: dict[str, Any], limits: dict[str, int]
    ) -> TranscriptionResult:
        del limits
        if (
            validate_profile_configuration(self.snapshot, self.settings)["availability"]
            != "CONFIGURED"
        ):
            raise ProviderUnavailable("PROVIDER_UNCONFIGURED")
        if (
            not audio
            or len(audio) > MAX_AUDIO_BYTES
            or hashlib.sha256(audio).hexdigest() != configuration.get("audio_sha256")
            or language != self.snapshot.get("language")
        ):
            raise InvalidProviderResult("INVALID_AUDIO_INPUT")
        config = self.snapshot["configuration"]
        body, boundary = _multipart(
            audio, language=language, content_type=configuration.get("source_content_type", "")
        )
        timeout = self.settings.speech_transcription_provider_timeout_seconds
        started = time.monotonic()
        connection = http.client.HTTPSConnection(
            "eu.api.openai.com", timeout=timeout, context=ssl.create_default_context()
        )
        expired = threading.Event()
        io_socket: socket.socket | None = None
        response: http.client.HTTPResponse | None = None

        def stop_io() -> None:
            expired.set()
            active_socket = io_socket if io_socket is not None else connection.sock
            if active_socket is not None:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        deadline = threading.Timer(timeout, stop_io)
        deadline.daemon = True
        deadline.start()
        try:
            # Direct TLS, no environment proxy, arbitrary URL or redirect following.
            connection.request(
                "POST",
                "/v1/audio/transcriptions",
                body=body,
                headers={
                    "Authorization": "Bearer " + os.environ[CREDENTIAL_REFERENCE],
                    "OpenAI-Project": config["deployment_id"],
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            )
            # Capture before getresponse(): a closing response can detach it
            # from HTTPConnection while still reading the provider body.
            io_socket = connection.sock
            response = connection.getresponse()
            if response.status != 200:
                # Never include provider response/error bodies in safe failure records.
                raise ProviderUnavailable("PROVIDER_REQUEST_REJECTED")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise InvalidProviderResult("PROVIDER_RESPONSE_TOO_LARGE")
            chunks: list[bytes] = []
            size = 0
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise ProviderTimeout("PROVIDER_TIMEOUT")
                if io_socket is not None:
                    io_socket.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if expired.is_set():
                    raise ProviderTimeout("PROVIDER_TIMEOUT")
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise InvalidProviderResult("PROVIDER_RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            try:
                payload = json.loads(b"".join(chunks))
            except (ValueError, UnicodeDecodeError) as exc:
                raise InvalidProviderResult("INVALID_PROVIDER_JSON") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
                raise InvalidProviderResult("INVALID_PROVIDER_SCHEMA")
            if not payload["text"].strip():
                raise EmptyProviderResult("EMPTY_RESULT")
            languages = payload.get("languages")
            if (
                not isinstance(languages, list)
                or len(languages) != 1
                or not isinstance(languages[0], dict)
                or languages[0].get("code") != language
            ):
                raise InvalidProviderResult("UNSUPPORTED_OUTPUT_LANGUAGE")
            # Do not copy raw response metadata into persistence or operational logs.
            return TranscriptionResult(
                transcript=payload["text"],
                provider=self.provider,
                model_name=self.model_name,
                adapter_version=self.adapter_version,
                metadata=_identity(configuration, language, test_only=False),
            )
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderTimeout("PROVIDER_TIMEOUT") from exc
        except (OSError, http.client.HTTPException) as exc:
            if expired.is_set():
                raise ProviderTimeout("PROVIDER_TIMEOUT") from exc
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE") from exc
        finally:
            deadline.cancel()
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass
            try:
                connection.close()
            except OSError:
                pass


def build_transcription_provider(
    snapshot: dict[str, Any], settings: Settings
) -> SpeechTranscriptionProvider:
    availability = validate_profile_configuration(snapshot, settings)
    if availability["availability"] == "TEST_ONLY":
        return TestOnlyExecutionProvider()
    if availability["availability"] == "CONFIGURED":
        return OpenAITranscriptionProvider(snapshot, settings)
    raise ProviderUnavailable("PROVIDER_UNCONFIGURED")
