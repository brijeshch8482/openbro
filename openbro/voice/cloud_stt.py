"""Cloud Speech-to-Text via Groq's free Whisper-large-v3 endpoint.

Why this exists: faster-whisper / ctranslate2 lack pre-built wheels for
very-recent Python versions (e.g. 3.14). Source builds segfault and
take 30+ minutes. Cloud Whisper sidesteps the problem entirely:
just POST audio bytes, get transcript back. Free tier is generous.

Endpoint: https://api.groq.com/openai/v1/audio/transcriptions
Auth: provider's groq.api_key from openbro config (user already set this
during the LLM step).

Public API:
    stt = CloudSTT(api_key="gsk_...")
    text = stt.transcribe_array(audio_np_array, sample_rate=16000)
    text = stt.transcribe_file("recording.wav")
"""

from __future__ import annotations

import io
from pathlib import Path

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-large-v3-turbo"  # free tier, very fast


class CloudSTT:
    """Groq Whisper STT — wire-compatible with the SpeechToText interface."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        language: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.language = language

    @staticmethod
    def _audio_array_to_wav_bytes(audio, sample_rate: int) -> bytes:
        """Convert a float32 numpy array (-1.0 to 1.0, mono) to a WAV byte stream."""
        try:
            import numpy as np
        except ImportError as e:
            raise RuntimeError("numpy required for audio encoding") from e

        # Convert float32 [-1, 1] to int16 PCM
        if hasattr(audio, "dtype") and audio.dtype != np.int16:
            audio = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)

        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    def transcribe_file(self, audio_path: str | Path) -> str:
        """Transcribe a WAV/MP3/M4A file via Groq's Whisper API."""
        import httpx

        path = Path(audio_path)
        if not path.exists():
            return ""
        try:
            files = {"file": (path.name, path.read_bytes(), "audio/wav")}
            data = {"model": self.model, "response_format": "text"}
            if self.language:
                data["language"] = self.language
            r = httpx.post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files,
                data=data,
                timeout=30,
            )
            if r.status_code != 200:
                return ""
            return r.text.strip()
        except Exception:
            return ""

    def transcribe_array(self, audio, sample_rate: int = 16000) -> str:
        """Transcribe a numpy float32 mono array."""
        try:
            wav_bytes = self._audio_array_to_wav_bytes(audio, sample_rate)
        except Exception:
            return ""

        import httpx

        try:
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            data = {"model": self.model, "response_format": "text"}
            if self.language:
                data["language"] = self.language
            r = httpx.post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files,
                data=data,
                timeout=30,
            )
            if r.status_code != 200:
                return ""
            return r.text.strip()
        except Exception:
            return ""
