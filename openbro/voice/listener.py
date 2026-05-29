"""Voice loop: capture mic audio, transcribe, dispatch to agent.

Two modes:

- **continuous** (default, new): every transcribed utterance is treated as a
  command. No wake word. Mic auto-pauses while the agent's TTS reply plays so
  the agent doesn't hear itself. User exits with `voice off` typed in REPL,
  Ctrl+C, or by speaking a stop phrase like "stop listening" / "bye bro".
- **wake_word** (legacy): keeps the original "hey openbro" gating. Whisper
  mishearings are bundled in DEFAULT_WAKE_WORDS so common variants still match.

Uses sounddevice for mic capture, simple energy-based VAD for utterance
boundaries, and faster-whisper for STT.
"""

from __future__ import annotations

import queue
import random
import sys
import threading
import time
from collections.abc import Callable

from openbro.voice.stt import VOICE_DEPS_HINT, SpeechToText
from openbro.voice.tts import TextToSpeech

# Whisper hears 'hey openbro' as 'Hebron', 'hebro', 'ai bro', or even
# random Japanese ('レブロー') depending on the audio. The wake_word mode
# uses this expanded list to keep mishearings working.
DEFAULT_WAKE_WORDS = [
    "hey openbro",
    "hi openbro",
    "ok openbro",
    "hello openbro",
    "hebron",
    "hebro",
    "ai bro",
    "open bro",
    "openborough",
    "openborg",
    "hey bro",
    "ok bro",
    "hi bro",
    "hello bro",
    "openbro suno",
]

# Phrases that end a continuous voice session. Lowercase, substring match.
# Kept short and distinctive so a normal command can't accidentally trip them.
DEFAULT_STOP_PHRASES = [
    "voice off",
    "stop listening",
    "band karo voice",
    "bye bro",
    "bye openbro",
    "good night bro",
    "good night openbro",
]


class VoiceListener:
    """Continuous voice loop.

    `mode='continuous'` (default): treat every transcript as a command. Mic
    pauses while TTS plays back the reply (prevents the agent hearing itself
    and looping).

    `mode='wake_word'`: legacy gate; only act when a wake word is in the
    transcript.
    """

    def __init__(
        self,
        mode: str = "continuous",
        wake_words: list[str] | None = None,
        stop_phrases: list[str] | None = None,
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
        self.mode = mode if mode in ("continuous", "wake_word") else "continuous"
        self.wake_words = [w.lower() for w in (wake_words or DEFAULT_WAKE_WORDS)]
        self.stop_phrases = [p.lower() for p in (stop_phrases or DEFAULT_STOP_PHRASES)]
        self.sample_rate = sample_rate
        self.chunk_seconds = chunk_seconds
        self.silence_threshold = silence_threshold
        self.silence_seconds = silence_seconds
        self.on_transcript = on_transcript
        # on_heard fires for EVERY non-empty transcript with a flag indicating
        # whether it would be acted on (wake word matched in wake_word mode;
        # always True in continuous mode). Lets the terminal show the user
        # what the mic actually heard.
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
        # `_paused` is consulted both inside the recording loop (to short-
        # circuit mic capture while the agent is speaking) and at the top
        # of `run()`. Plain bool — single-writer, single-reader pattern, no
        # lock needed.
        self._paused = False

    def is_wake_word(self, text: str) -> bool:
        t = text.lower()
        return any(w in t for w in self.wake_words)

    def is_stop_phrase(self, text: str) -> bool:
        t = text.lower()
        return any(p in t for p in self.stop_phrases)

    @staticmethod
    def strip_wake_word(text: str, wake_words: list[str]) -> str:
        t = text.strip()
        low = t.lower()
        for w in wake_words:
            idx = low.find(w)
            if idx >= 0:
                return (t[:idx] + t[idx + len(w) :]).strip(" ,.?!")
        return t

    def pause(self) -> None:
        """Pause mic capture (used during TTS playback to avoid echo loop)."""
        self._paused = True

    def resume(self) -> None:
        """Resume mic capture after a pause."""
        self._paused = False

    def listen_once(self) -> str:
        """Record one chunk and return transcript."""
        audio = self._record_chunk(self.chunk_seconds)
        if audio is None:
            return ""
        return self.stt.transcribe_array(audio, sample_rate=self.sample_rate)

    def run(self) -> None:
        """Main loop: listen, optionally gate on wake-word, dispatch to handler."""
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
                if self._paused:
                    # Agent is currently speaking. Don't open the mic at all
                    # — we'd record our own TTS and feed it back as a command.
                    time.sleep(0.1)
                    continue

                text = self.listen_once()
                if not text:
                    continue

                # Continuous mode acts on every utterance; wake_word mode
                # requires the gate. on_heard's second arg ("would act?")
                # reflects that.
                if self.mode == "continuous":
                    has_wake = True
                    command = text.strip()
                else:
                    has_wake = self.is_wake_word(text)
                    command = self.strip_wake_word(text, self.wake_words) if has_wake else text

                if self.on_heard:
                    try:
                        self.on_heard(text, has_wake)
                    except Exception:
                        pass

                # Stop phrase: works in BOTH modes so a user in wake_word
                # mode can still say "bye bro" to exit without typing.
                if self.is_stop_phrase(text):
                    if self.tts and self.speak_replies:
                        self._speak_with_mic_paused("Voice band kar raha hoon. Bye bro.")
                    self._running = False
                    break

                if self.mode == "wake_word" and not has_wake:
                    continue

                if not command:
                    # Wake word with no payload — ask user to speak the actual
                    # command. Only meaningful in wake_word mode (continuous
                    # mode would never produce empty `command` for non-empty
                    # `text`).
                    if self.tts:
                        self._speak_with_mic_paused(random.choice(self.ack_phrases))
                    command = self.listen_once()
                if not command:
                    continue

                print(f"You (voice): {command}")
                if self.on_transcript:
                    reply = self.on_transcript(command)
                    if reply:
                        print(f"{self.assistant_name}: {reply}")
                        if self.tts:
                            self._speak_with_mic_paused(reply)
            except KeyboardInterrupt:
                self._running = False
                break
            except Exception as e:
                print(f"[voice loop error] {e}", file=sys.stderr)
                time.sleep(0.5)

    def stop(self) -> None:
        self._running = False

    def _speak_with_mic_paused(self, text: str) -> None:
        """Pause mic, speak, then resume — prevents the agent's own audio
        from being captured and processed as the next command (the classic
        always-on assistant echo loop).
        """
        if not self.tts:
            return
        was_paused = self._paused
        self._paused = True
        try:
            self.tts.speak(text)
        except Exception as e:
            print(f"[tts error] {e}", file=sys.stderr)
        finally:
            # Brief tail so any residual speaker output settles before the
            # mic reopens. 250ms is enough on Windows MCI; longer feels laggy.
            time.sleep(0.25)
            if not was_paused:
                self._paused = False

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
                    return None
                if respect_stop and self._paused:
                    # Mid-recording the listener got paused (TTS started).
                    # Discard whatever we have, release the mic, let the
                    # outer loop handle the pause sleep.
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


# threading is imported at module top for future hooks; keep an explicit
# reference so static checkers don't flag it as unused.
_ = threading
