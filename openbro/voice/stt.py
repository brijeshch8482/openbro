"""Speech-to-Text using faster-whisper (offline, GPU-friendly).

Optional dependency: install with `pip install openbro[voice]`.
"""

from pathlib import Path

VOICE_DEPS_HINT = (
    "Voice deps not installed. Run: pip install openbro[voice] "
    "(installs faster-whisper, edge-tts, sounddevice, numpy)"
)


class SpeechToText:
    """Wraps faster-whisper. Lazy-loads the model on first use."""

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

    def _ensure_model(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(VOICE_DEPS_HINT) from e
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )

    def transcribe(self, audio_path: str | Path) -> str:
        """Transcribe an audio file (wav/mp3/m4a) and return text."""
        self._ensure_model()
        segments, _info = self._model.transcribe(
            str(audio_path),
            language=self.language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def transcribe_array(self, audio: "object", sample_rate: int = 16000) -> str:
        """Transcribe a numpy float32 mono array at given sample rate."""
        self._ensure_model()
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_file(audio_path: str | Path, model_size: str = "base") -> str:
    """One-shot helper: transcribe a single audio file."""
    return SpeechToText(model_size=model_size).transcribe(audio_path)
