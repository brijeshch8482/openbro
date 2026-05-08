"""Voice loop: capture mic audio, detect wake word, transcribe, dispatch to agent.

Uses sounddevice for mic capture, simple energy-based VAD for utterance boundaries,
and substring match for wake-word detection (e.g. 'hey bro' / 'hi bro' / 'bro').

For a production-grade wake word use Porcupine; this stays dependency-light.
"""

from __future__ import annotations

import queue
import sys
import time
from collections.abc import Callable

from openbro.voice.stt import VOICE_DEPS_HINT, SpeechToText
from openbro.voice.tts import TextToSpeech

DEFAULT_WAKE_WORDS = ["hey bro", "hi bro", "bro suno", "ok bro"]


class VoiceListener:
    """Continuous voice loop with wake-word activation."""

    def __init__(
        self,
        wake_words: list[str] | None = None,
        sample_rate: int = 16000,
        chunk_seconds: float = 4.0,
        silence_threshold: float = 0.005,
        stt_model: str = "base",
        on_transcript: Callable[[str], str] | None = None,
        speak_replies: bool = True,
    ):
        self.wake_words = [w.lower() for w in (wake_words or DEFAULT_WAKE_WORDS)]
        self.sample_rate = sample_rate
        self.chunk_seconds = chunk_seconds
        self.silence_threshold = silence_threshold
        self.on_transcript = on_transcript
        self.speak_replies = speak_replies
        self.stt = SpeechToText(model_size=stt_model)
        self.tts = TextToSpeech() if speak_replies else None
        self._running = False

    def is_wake_word(self, text: str) -> bool:
        t = text.lower()
        return any(w in t for w in self.wake_words)

    @staticmethod
    def strip_wake_word(text: str, wake_words: list[str]) -> str:
        t = text.strip()
        low = t.lower()
        for w in wake_words:
            idx = low.find(w)
            if idx >= 0:
                return (t[:idx] + t[idx + len(w) :]).strip(" ,.?!")
        return t

    def listen_once(self) -> str:
        """Record one chunk and return transcript."""
        audio = self._record_chunk(self.chunk_seconds)
        if audio is None:
            return ""
        return self.stt.transcribe_array(audio, sample_rate=self.sample_rate)

    def run(self) -> None:
        """Main loop: listen, detect wake word, transcribe command, call handler."""
        try:
            import numpy as np  # noqa: F401
            import sounddevice as sd  # noqa: F401
        except ImportError:
            print(VOICE_DEPS_HINT, file=sys.stderr)
            return

        self._running = True
        # NOTE: don't print here — when this listener runs in background of the
        # REPL (voice.auto_start=True), prompt_toolkit owns the terminal and
        # any stray print() collides with the input line. The REPL's
        # _start_voice() already shows a status message before spawning us.
        while self._running:
            try:
                text = self.listen_once()
                if not text:
                    continue
                if not self.is_wake_word(text):
                    continue
                command = self.strip_wake_word(text, self.wake_words)
                if not command:
                    if self.tts:
                        self.tts.speak("Haan bro, bolo.")
                    command = self.listen_once()
                if not command:
                    continue
                print(f"You (voice): {command}")
                if self.on_transcript:
                    reply = self.on_transcript(command)
                    if reply:
                        print(f"Bro: {reply}")
                        if self.tts:
                            self.tts.speak(reply)
            except KeyboardInterrupt:
                self._running = False
                break
            except Exception as e:
                print(f"[voice loop error] {e}", file=sys.stderr)
                time.sleep(0.5)

    def stop(self) -> None:
        self._running = False

    def _record_chunk(self, seconds: float):
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            print(VOICE_DEPS_HINT, file=sys.stderr)
            return None

        frames = int(seconds * self.sample_rate)
        q: queue.Queue = queue.Queue()

        def _cb(indata, _frames, _time, status):
            if status:
                pass
            q.put(indata.copy())

        # Bail early if stop was requested between chunks - don't open the
        # mic stream just to immediately tear it down.
        if not self._running and self._running is not False:
            return None

        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=_cb,
        )
        try:
            stream.start()
            collected = []
            collected_frames = 0
            while collected_frames < frames:
                if not self._running:
                    # Caller asked us to stop mid-recording. Discard buffer
                    # and exit so PortAudio releases the mic immediately.
                    return None
                try:
                    data = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                collected.append(data)
                collected_frames += len(data)
        finally:
            # Hard cleanup: stop + close the stream even if an exception or
            # KeyboardInterrupt fires. This is what prevents the "mic still
            # held by python.exe after exit" zombie state.
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        if not collected:
            return None
        audio = np.concatenate(collected, axis=0).flatten().astype("float32")
        # Drop near-silent chunks early
        rms = float((audio**2).mean() ** 0.5)
        if rms < self.silence_threshold:
            return None
        return audio
