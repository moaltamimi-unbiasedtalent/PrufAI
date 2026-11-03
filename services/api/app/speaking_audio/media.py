"""Bounded inspection of original audio; this module never transcodes evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings

MAX_INSPECTION_OUTPUT = 256 * 1024
FORMATS: dict[str, tuple[str, str]] = {
    "audio/webm;codecs=opus": ("webm", "opus"),
    "audio/ogg;codecs=opus": ("ogg", "opus"),
    "audio/mp4;codecs=mp4a.40.2": ("mp4", "aac"),
    "audio/wav": ("wav", "pcm_s16le"),
}


class AudioMediaError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedAudio:
    duration_ms: int
    sha256: str
    byte_size: int
    validation_provenance: dict[str, Any]


class _FrameSummary:
    """Fold bounded metadata lines without retaining frames or decoded PCM."""

    def __init__(self, maximum_seconds: int) -> None:
        self.pending = bytearray()
        self.maximum = Fraction(maximum_seconds)
        self.frames = 0
        self.decoded = Fraction(0)
        self.first_pts: Fraction | None = None
        self.last_pts: Fraction | None = None
        self.last_end: Fraction | None = None

    def add(self, chunk: bytes) -> None:
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, remainder = self.pending.partition(b"\n")
            self.pending = bytearray(remainder)
            if len(line) > 4096:
                raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")
            self.line(bytes(line))
        if len(self.pending) > 4096:
            raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")

    def line(self, line: bytes) -> None:
        raise NotImplementedError

    def frame(self, pts: Fraction, duration: Fraction, resolution: Fraction) -> None:
        if duration <= 0 or pts < -resolution:
            raise AudioMediaError("AUDIO.INVALID_PRESENTATION_TIMELINE")
        if self.last_pts is not None and (
            pts <= self.last_pts or (self.last_end is not None and pts < self.last_end - resolution)
        ):
            raise AudioMediaError("AUDIO.NON_MONOTONIC_PRESENTATION_TIMELINE")
        if self.first_pts is None:
            self.first_pts = pts
        self.last_pts, self.last_end = pts, pts + duration
        self.decoded += duration
        self.frames += 1
        if self.frames > 2_000_000:
            raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")
        if self.decoded > self.maximum:
            raise AudioMediaError("AUDIO.DURATION_OUTSIDE_POLICY")
        # Include a positive initial timestamp. Subtracting it would hide an
        # initial presentation delay; tiny negative codec/clock rounding remains
        # visible in the span rather than silently being discarded.
        origin = min(Fraction(0), self.first_pts)
        if self.last_end - origin > self.maximum + resolution:
            raise AudioMediaError("AUDIO.PRESENTATION_TIMELINE_OUTSIDE_POLICY")

    @property
    def span(self) -> Fraction:
        if self.first_pts is None or self.last_end is None:
            raise AudioMediaError("AUDIO.DECODED_DURATION_UNAVAILABLE")
        return self.last_end - min(Fraction(0), self.first_pts)

    def result(self) -> bytes:
        if self.pending or not self.frames:
            raise AudioMediaError("AUDIO.DECODED_DURATION_UNAVAILABLE")
        return json.dumps(
            {
                "decoded_seconds": str(self.decoded),
                "presentation_seconds": str(self.span),
                "first_pts_seconds": str(self.first_pts),
                "last_end_seconds": str(self.last_end),
                "frames": self.frames,
            }
        ).encode()


class _FrameCounter(_FrameSummary):
    """Independent full FFmpeg decode with original timestamps retained."""

    def __init__(self, maximum_seconds: int) -> None:
        super().__init__(maximum_seconds)
        self.timebase: Fraction | None = None

    def line(self, line: bytes) -> None:
        timebase = re.fullmatch(rb"#tb 0: (\d+)/(\d+)", line)
        if timebase:
            numerator, denominator = map(int, timebase.groups())
            if not 0 < numerator <= denominator <= 1_000_000_000 or self.timebase is not None:
                raise AudioMediaError("AUDIO.INVALID_STREAM_METADATA")
            self.timebase = Fraction(numerator, denominator)
        elif line and not line.startswith(b"#"):
            fields = line.split(b",")
            try:
                if len(fields) != 6 or int(fields[0]) != 0 or self.timebase is None:
                    raise ValueError("invalid frame")
                pts = int(fields[2]) * self.timebase
                duration = int(fields[3]) * self.timebase
            except ValueError as exc:
                raise AudioMediaError("AUDIO.DECODED_DURATION_UNAVAILABLE") from exc
            # PCM sample timestamps are exact here. No rounding tolerance is
            # added to the authoritative decoded/presentation policy limit.
            self.frame(pts, duration, Fraction(0))


class _ProbeFrames(_FrameSummary):
    """Inspect every original decoded frame PTS, including container gaps."""

    def __init__(self, maximum_seconds: int, sample_rate: int, resolution: Fraction) -> None:
        super().__init__(maximum_seconds)
        self.sample_rate = sample_rate
        self.resolution = resolution

    def line(self, line: bytes) -> None:
        if not line:
            return
        try:
            fields = dict(part.split("=", 1) for part in line.decode("ascii").split("|"))
            if fields["media_type"] != "audio" or fields["stream_index"] != "0":
                raise ValueError("invalid frame")
            samples_text = fields["nb_samples"]
            if not samples_text.isascii() or not samples_text.isdigit():
                raise ValueError("invalid samples")
            duration = Fraction(int(samples_text), self.sample_rate)
            # Explicit original PTS is required; a synthetic best-effort clock
            # cannot establish that timestamp discontinuities were inspected.
            pts = Fraction(fields["pts_time"])
        except (ValueError, KeyError, UnicodeDecodeError, ZeroDivisionError) as exc:
            raise AudioMediaError("AUDIO.INVALID_PRESENTATION_TIMELINE") from exc
        self.frame(pts, duration, self.resolution)


def _run_bounded(
    command: list[str],
    timeout: int,
    source: Path | None = None,
    counter: _FrameSummary | None = None,
    strict_diagnostics: bool = False,
) -> bytes:
    """Drain both pipes under one wall clock and output limit, without logging them."""
    input_file = source.open("rb") if source else None
    try:
        process = subprocess.Popen(
            command,
            stdin=input_file if input_file else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        if input_file:
            input_file.close()
        raise AudioMediaError("AUDIO.MEDIA_VALIDATOR_UNAVAILABLE") from exc
    output = bytearray()
    total = 0
    diagnostic = False
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, True)
    selector.register(process.stderr, selectors.EVENT_READ, False)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AudioMediaError("AUDIO.MEDIA_VALIDATION_TIMEOUT")
            for key, _mask in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data and counter:
                    counter.add(chunk)
                    continue
                if not key.data:
                    diagnostic = True
                total += len(chunk)
                if total > MAX_INSPECTION_OUTPUT:
                    raise AudioMediaError("AUDIO.MEDIA_VALIDATION_OUTPUT_LIMIT")
                if key.data:
                    output.extend(chunk)
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if process.returncode != 0:
            if command[0] == "docker" and process.returncode in {125, 126, 127}:
                raise AudioMediaError("AUDIO.MEDIA_VALIDATOR_UNAVAILABLE")
            raise AudioMediaError("AUDIO.MEDIA_UNDECODABLE_OR_VALIDATOR_UNAVAILABLE")
        if strict_diagnostics and diagnostic:
            raise AudioMediaError("AUDIO.MEDIA_DECODER_DIAGNOSTIC")
        return counter.result() if counter else bytes(output)
    except subprocess.TimeoutExpired as exc:
        raise AudioMediaError("AUDIO.MEDIA_VALIDATION_TIMEOUT") from exc
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        process.stdout.close()
        process.stderr.close()
        if input_file:
            input_file.close()


def _inspect_command(
    settings: Settings,
    tool: str,
    arguments: list[str],
    source: Path,
    counter: _FrameSummary | None = None,
) -> bytes:
    container_name = "prufai-media-" + uuid.uuid4().hex
    if settings.audio_media_runtime == "docker":
        command = [
            "docker",
            "run",
            "--interactive",
            "--rm",
            "--pull=never",
            "--name",
            container_name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--cpus=1",
            f"--memory={settings.audio_probe_memory_mb}m",
            "--user=65534:65534",
            "--tmpfs=/tmp:size=8m,mode=1777",
            "--tmpfs=/input:size=26m,mode=1777",
            "--entrypoint",
            "/bin/sh",
            settings.audio_media_image,
            "-c",
            'cat > /input/audio && exec "$@"',
            "audio-inspection",
            f"/usr/bin/{tool}",
            *["/input/audio" if argument == str(source) else argument for argument in arguments],
        ]
    else:
        executable = (
            settings.audio_ffprobe_executable
            if tool == "ffprobe"
            else settings.audio_ffmpeg_executable
        )
        if shutil.which(executable) is None:
            raise AudioMediaError("AUDIO.MEDIA_VALIDATOR_UNAVAILABLE")
        # Limits are set in a separate Python child before exec, avoiding
        # preexec_fn in the API's multithreaded process.
        limits = (
            "import os,resource,sys; "
            "m=int(sys.argv[1]); t=int(sys.argv[2]); "
            "resource.setrlimit(resource.RLIMIT_AS,(m,m)); "
            "resource.setrlimit(resource.RLIMIT_CPU,(t,t+1)); "
            "resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
            "os.execvp(sys.argv[3],sys.argv[3:])"
        )
        command = [
            sys.executable,
            "-c",
            limits,
            str(settings.audio_probe_memory_mb * 1024 * 1024),
            str(settings.audio_probe_timeout_seconds),
            executable,
            *arguments,
        ]
    try:
        return _run_bounded(
            command,
            settings.audio_probe_timeout_seconds,
            source if settings.audio_media_runtime == "docker" else None,
            counter=counter,
            strict_diagnostics=arguments != ["-version"],
        )
    finally:
        if settings.audio_media_runtime == "docker":
            # A killed Docker client must not leave its bounded inspection
            # container alive. This name belongs only to the current invocation.
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass


def validate_recorded_media(
    content: bytes,
    declared_mime: str,
    policy: dict[str, Any],
    settings: Settings | None = None,
) -> ValidatedAudio:
    configuration = settings or get_settings()
    maximum_bytes = int(policy["max_upload_bytes"])
    maximum_seconds = int(policy["max_recording_seconds"])
    if not content or len(content) > min(maximum_bytes, 25 * 1024 * 1024):
        raise AudioMediaError("AUDIO.EMPTY_OR_OVERSIZED")
    declared_base = declared_mime.split(";", 1)[0].strip().lower()
    declared_codecs = [
        parameter.split("=", 1)[1].strip().strip('"').lower()
        for parameter in declared_mime.split(";")[1:]
        if parameter.strip().lower().startswith("codecs=")
    ]
    permitted = [
        (mime, FORMATS[mime])
        for mime in policy["supported_mime_types"]
        if mime in FORMATS
        and mime.split(";", 1)[0] == declared_base
        and (not declared_codecs or declared_codecs == [mime.split(";codecs=", 1)[-1]])
    ]
    if not permitted:
        raise AudioMediaError("AUDIO.FORMAT_NOT_PERMITTED")
    with tempfile.TemporaryDirectory(prefix="prufai-audio-inspection-") as temporary:
        source = Path(temporary) / "original.audio"
        source.write_bytes(content)
        source.chmod(0o644)
        try:
            raw = _inspect_command(
                configuration,
                "ffprobe",
                [
                    "-v",
                    "warning",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_entries",
                    "format=format_name:stream=codec_type,codec_name,sample_rate,channels,time_base",
                    "-of",
                    "json",
                    str(source),
                ],
                source,
            )
            probe = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AudioMediaError("AUDIO.MEDIA_PROBE_INVALID") from exc
        streams = probe.get("streams", [])
        if len(streams) != 1 or streams[0].get("codec_type") != "audio":
            raise AudioMediaError("AUDIO.EXACTLY_ONE_AUDIO_STREAM_REQUIRED")
        stream = streams[0]
        codec = str(stream.get("codec_name", ""))
        containers = str(probe.get("format", {}).get("format_name", "")).split(",")
        matched = next(
            (
                (mime, container)
                for mime, (container, allowed_codec) in permitted
                if container in containers and codec == allowed_codec
            ),
            None,
        )
        if matched is None:
            raise AudioMediaError("AUDIO.CONTAINER_CODEC_MIME_MISMATCH")
        try:
            sample_rate = int(stream["sample_rate"])
            channels = int(stream["channels"])
            timestamp_resolution = Fraction(stream["time_base"])
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
            raise AudioMediaError("AUDIO.INVALID_STREAM_METADATA") from exc
        if not 8000 <= sample_rate <= 48000 or channels not in {1, 2}:
            raise AudioMediaError("AUDIO.UNSUPPORTED_STREAM_PARAMETERS")
        if not Fraction(0) < timestamp_resolution <= Fraction(1, 1000):
            raise AudioMediaError("AUDIO.UNSUPPORTED_TIMESTAMP_PRECISION")
        original_frames = _inspect_command(
            configuration,
            "ffprobe",
            [
                "-v",
                "warning",
                "-err_detect",
                "explode",
                "-protocol_whitelist",
                "file,pipe",
                "-show_frames",
                "-show_entries",
                "frame=media_type,stream_index,pts_time,nb_samples",
                "-of",
                "compact=p=0:nk=0",
                str(source),
            ],
            source,
            _ProbeFrames(maximum_seconds, sample_rate, timestamp_resolution),
        )
        decoded_frames = _inspect_command(
            configuration,
            "ffmpeg",
            [
                "-v",
                "warning",
                "-xerror",
                "-err_detect",
                "explode",
                "-nostdin",
                "-threads",
                "1",
                "-guess_layout_max",
                "0",
                "-copyts",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                "pcm_s16le",
                "-f",
                "framecrc",
                "-",
            ],
            source,
            _FrameCounter(maximum_seconds),
        )
        # Both processes must drain the complete original and exit successfully.
        # A callback cap, timeout, diagnostic or decoder failure raises instead
        # of returning any prefix summary. FFprobe retains original frame PTS;
        # FFmpeg independently decodes every sample with copyts and no -t cutoff.
        try:
            counts = json.loads(decoded_frames)
            original = json.loads(original_frames)
            decoded_duration = Fraction(counts["decoded_seconds"])
            decoded_timeline = Fraction(counts["presentation_seconds"])
            original_duration = Fraction(original["decoded_seconds"])
            original_timeline = Fraction(original["presentation_seconds"])
            duration_ms = math.ceil(decoded_duration * 1000)
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
            raise AudioMediaError("AUDIO.DECODED_DURATION_UNAVAILABLE") from exc
        if (
            decoded_duration <= 0
            or decoded_duration > maximum_seconds
            or original_duration != decoded_duration
            or decoded_timeline > maximum_seconds
            or abs(original_timeline - decoded_timeline) > timestamp_resolution
            or original_timeline > maximum_seconds + timestamp_resolution
        ):
            raise AudioMediaError("AUDIO.DURATION_OUTSIDE_POLICY")
        version = _inspect_command(configuration, "ffprobe", ["-version"], source)
        version_line = version.decode("utf-8", errors="replace").splitlines()[0][:160]
    digest = hashlib.sha256(content).hexdigest()
    return ValidatedAudio(
        duration_ms=duration_ms,
        sha256=digest,
        byte_size=len(content),
        validation_provenance={
            "validator": "ffprobe_and_complete_ffmpeg_audio_decode",
            "validator_version": version_line,
            "runtime": configuration.audio_media_runtime,
            "container": matched[1],
            "codec": codec,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_source": "decoded_pcm_frame_samples",
            "duration_ms": duration_ms,
            "complete_input": True,
            "decoded_frame_count": counts["frames"],
            "inspected_frame_count": original["frames"],
            "decoded_presentation_span_ms": math.ceil(decoded_timeline * 1000),
            "original_presentation_span_ms": math.ceil(original_timeline * 1000),
            "original_first_pts_seconds": original["first_pts_seconds"],
            "original_last_end_seconds": original["last_end_seconds"],
            "timestamp_resolution_seconds": str(timestamp_resolution),
            "timestamp_rounding_tolerance": "at_most_one_original_timebase_tick",
            "source_sha256": digest,
            "source_byte_size": len(content),
        },
    )
