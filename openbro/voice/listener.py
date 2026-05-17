"""Voice loop: capture mic audio, detect wake word, transcribe, dispatch to agent.

Uses sounddevice for mic capture, simple energy-based VAD for utterance boundaries,
and substring match for wake-word detection (e.g. 'hey openbro' / 'hi openbro').

For a production-grade wake word use Porcupine; this stays dependency-light.
"""

from __future__ import annotations

import queue
import random
import sys
import time
from collections.abc import Callable

from openbro.voice.stt import VOICE_DEPS_HINT, SpeechToText
from openbro.voice.tts import TextToSpeech

# Whisper hears 'hey openbro' as 'Hebron', 'hebro', 'ai bro', or even
# random Japanese ('レブロー') depending on the audio. We expand the
# default wake list to include common mishearings so a user saying
# 'hey openbro' isn't silently ignored just because STT spelt it
# differently. All substring-matched lowercase.
DEFAULT_WAKE_WORDS = [
    "hey openbro",
    "hi openbro",
    "ok openbro",
    "hello openbro",
    # Common Whisper mishearings of 'openbro'
    "hebron",
    "hebro",
    "ai bro",
    "open bro",
    "openborough",
    "openborg",
    # Generic fallbacks the model picks up cleanly
    "hey bro",
    "ok bro",
    "hi bro",
    "hello bro",
    "openbro suno",
]


class VoiceListener:
    """Continuous voice loop with wake-word activation."""

    def __init__(
        self,
        wake_words: list[str] | None = None,
        sample_rate: int = 16000,
        chunk_seconds: float = 8.0,
        silence_threshold: float = 0.003,
        silence_seconds: float = 0.8,
        stt_model: str = "small",
        stt_language: str | None = None,
        stt_device: str = "cpu",
        stt_compute_type: str = "int8",
        stt_beam_size: int = 5,
        stt_vad_filter: bool = True,
        on_transcript: Callable[[str], str] | None = None,
        on_heard: Callable[[str, bool], None] | None = None,
        speak_replies: bool = True,
        assistant_name: str = "OpenBro",
        ack_phrases: list[str] | None = None,
    ):
        self.wake_words = [w.lower() for w in (wake_words or DEFAULT_WAKE_WORDS)]
        self.sample_rate = sample_rate
        self.chunk_seconds = chunk_seconds
        self.silence_threshold = silence_threshold
        self.silence_seconds = silence_seconds
        self.on_transcript = on_transcript
        # Fires for EVERY non-empty transcript, with a flag indicating whether
        # the wake word was present. Used by terminal/activity debug visibility — so
        # the user can see "I heard: ..." even when the wake word didn't match
        # (which is the #1 voice complaint: "voice kaam nahi kar rha" usually
        # means the wake word wasn't detected, not that the mic failed).
        self.on_heard = on_heard
        self.speak_replies = speak_replies
        self.assistant_name = assistant_name
        self.ack_phrases = ack_phrases or [
            "Yes bro, bolo.",
            "Yes boss, boliye.",
            "Ji sir, main sun raha hoon.",
        ]
        self.stt = SpeechToText(
            model_size=stt_model,
            device=stt_device,
            compute_type=stt_compute_type,
            language=stt_language,
            beam_size=stt_beam_size,
            vad_filter=stt_vad_filter,
        )
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
                has_wake = self.is_wake_word(text)
                # Tell the terminal/activity surface what we heard, regardless of wake word.
                # This lets the user debug ("I said X but it heard Y") and keeps
                # non-wake-word speech from vanishing silently.
                if self.on_heard:
                    try:
                        self.on_heard(text, has_wake)
                    except Exception:
                        pass
                if not has_wake:
                    continue
                command = self.strip_wake_word(text, self.wake_words)
                if not command:
                    if self.tts:
                        self.tts.speak(random.choice(self.ack_phrases))
                    command = self.listen_once()
                if not command:
                    continue
                print(f"You (voice): {command}")
                if self.on_transcript:
                    reply = self.on_transcript(command)
                    if reply:
                        print(f"{self.assistant_name}: {reply}")
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

        # Background mode should stop promptly. One-shot tests call listen_once()
        # while _running is False, so only honor stop checks if run() owns us.
        respect_stop = self._running

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
            speech_started = False
            last_voice_at = None
            while collected_frames < frames:
                if respect_stop and not self._running:
                    # Caller asked us to stop mid-recording. Discard buffer
                    # and exit so PortAudio releases the mic immediately.
                    return None
                try:
                    data = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                collected.append(data)
                collected_frames += len(data)
                block = data.flatten().astype("float32")
                block_rms = float((block**2).mean() ** 0.5) if len(block) else 0.0
                now = time.monotonic()
                if block_rms >= self.silence_threshold:
                    speech_started = True
                    last_voice_at = now
                recorded_seconds = collected_frames / self.sample_rate
                if (
                    speech_started
                    and last_voice_at is not None
                    and recorded_seconds >= 0.6
                    and now - last_voice_at >= self.silence_seconds
                ):
                    break
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
        return self._trim_silence(audio)

    def _trim_silence(self, audio):
        """Trim obvious leading/trailing silence so STT sees mostly speech."""
        try:
            import numpy as np
        except ImportError:
            return audio

        if audio is None or len(audio) == 0:
            return audio
        threshold = max(self.silence_threshold * 0.6, 0.001)
        active = np.flatnonzero(np.abs(audio) > threshold)
        if len(active) == 0:
            return audio
        pad = int(0.2 * self.sample_rate)
        start = max(int(active[0]) - pad, 0)
        end = min(int(active[-1]) + pad, len(audio))
        return audio[start:end]
