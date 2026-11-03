"""Original technical PCM tone: decodable media, never speech-recognition evidence."""

import io
import math
import struct
import wave


def technical_tone_wav(*, duration_seconds: float = 1.0, frequency_hz: int = 440) -> bytes:
    sample_rate = 16_000
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(sample_rate)
        recording.writeframes(
            b"".join(
                struct.pack(
                    "<h", int(4_000 * math.sin(2 * math.pi * frequency_hz * sample / sample_rate))
                )
                for sample in range(int(sample_rate * duration_seconds))
            )
        )
    return output.getvalue()
