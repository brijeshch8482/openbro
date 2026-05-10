"""Speech-to-Text - offline via faster-whisper (default).

Voice is offline by design: mic audio never leaves your machine. The
installer auto-installs Python 3.12 if your current version doesn't have
faster-whisper wheels, so this just works.

Cloud STT (Groq Whisper) is available as an opt-in fallback for users
who explicitly want it - set voice.use_cloud_stt = true in config.
"""

from __future__ import annotations

from pathlib import Path

VOICE_DEPS_HINT = (
    "Offline voice STT not installed. Run:\n"
    "  pip install 'openbro[voice]'\n"
    "(Needs Python 3.10-3.13. The installer auto-installs Python 3.12 if "
    "your version is too new.)"
)


def _wants_cloud_stt() -> tuple[bool, str | None]:
    """Return (use_cloud, groq_api_key) per user config preference.

    Default: offline. Cloud only kicks in if user explicitly opts in via
    voice.use_cloud_stt = true in config (and has a Groq key).
    """
    try:
        from openbro.utils.config import load_config

        cfg = load_config()
        voice_cfg = cfg.get("voice", {}) or {}
        if not voice_cfg.get("use_cloud_stt", False):
            return False, None
        key = (cfg.get("providers", {}).get("groq", {}) or {}).get("api_key")
        return True, key
    except Exception:
        return False, None


class SpeechToText:
    """Offline-first STT via faster-whisper. Optional cloud fallback."""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None
        self._cloud = None

    def _ensure_model(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            # If user opted into cloud STT, surface a softer error
            use_cloud, key = _wants_cloud_stt()
            if use_cloud and key:
                # Will be handled by transcribe() falling back to cloud
                return
            raise RuntimeError(VOICE_DEPS_HINT) from e
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )

    def _get_cloud(self):
        if self._cloud is not None:
            return self._cloud
        use_cloud, key = _wants_cloud_stt()
        if not use_cloud or not key:
            return None
        try:
            from openbro.voice.cloud_stt import CloudSTT

            self._cloud = CloudSTT(api_key=key, language=self.language)
            return self._cloud
        except Exception:
            return None

    def transcribe(self, audio_path: str | Path) -> str:
        """Transcribe an audio file (wav/mp3/m4a)."""
        self._ensure_model()
        if self._model is not None:
            segments, _info = self._model.transcribe(
                str(audio_path),
                language=self.language,
                beam_size=1,
                vad_filter=True,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        # Cloud fallback (only if user opted in)
        cloud = self._get_cloud()
        if cloud:
            return cloud.transcribe_file(audio_path)
        raise RuntimeError(VOICE_DEPS_HINT)

    def transcribe_array(self, audio, sample_rate: int = 16000) -> str:
        """Transcribe a numpy float32 mono array."""
        self._ensure_model()
        if self._model is not None:
            segments, _info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=1,
                vad_filter=True,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        cloud = self._get_cloud()
        if cloud:
            return cloud.transcribe_array(audio, sample_rate=sample_rate)
        raise RuntimeError(VOICE_DEPS_HINT)


def transcribe_file(audio_path: str | Path, model_size: str = "base") -> str:
    """One-shot helper: transcribe a single audio file."""
    return SpeechToText(model_size=model_size).transcribe(audio_path)
