"""Text-to-Speech: edge-tts (online, natural) with pyttsx3 fallback (offline).

edge-tts uses Microsoft Edge's free streaming TTS service. No API key needed.
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_VOICE = "en-IN-NeerjaNeural"  # Indian English, female; great for Hinglish


def _play_mp3_mci(path: str) -> None:
    """Play an MP3 in-process via Windows MCI. No external app launched.

    Uses an open->play->close cycle with a unique alias so concurrent
    speak() calls don't collide. Each MCI command is checked; any
    non-zero return raises so the speak() caller can fall back to
    pyttsx3 (SAPI5) instead of silently dropping audio.
    """
    import ctypes
    import os
    import threading

    winmm = ctypes.windll.winmm
    # Alias must be unique per thread + time so threaded callers don't
    # stomp on each other's playback handles.
    alias = f"openbro_tts_{os.getpid()}_{threading.get_ident()}"
    # MCI prefers POSIX-style paths quoted; backslashes also work but
    # quoting is essential for paths with spaces (Windows temp dir).
    open_cmd = f'open "{path}" type mpegvideo alias {alias}'
    rc = winmm.mciSendStringW(open_cmd, None, 0, 0)
    if rc != 0:
        raise RuntimeError(f"MCI open failed (rc={rc}) for {path}")
    try:
        rc = winmm.mciSendStringW(f"play {alias} wait", None, 0, 0)
        if rc != 0:
            raise RuntimeError(f"MCI play failed (rc={rc})")
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, 0)


class TextToSpeech:
    """Speak text aloud or save to a file. Auto-falls back to pyttsx3 offline."""

    def __init__(self, voice: str = DEFAULT_VOICE, rate: str = "+0%"):
        self.voice = voice
        self.rate = rate

    def speak(self, text: str) -> None:
        """Synthesize and play audio (blocking)."""
        if not text or not text.strip():
            return
        # Try edge-tts first
        try:
            self._speak_edge(text)
            return
        except Exception:
            pass
        # Fallback: pyttsx3 (offline)
        try:
            self._speak_pyttsx3(text)
        except Exception as e:
            print(f"[tts error] {e}", file=sys.stderr)

    def save(self, text: str, output_path: str | Path) -> None:
        """Synthesize to an mp3/wav file (uses edge-tts)."""
        try:
            asyncio.run(self._edge_save(text, str(output_path)))
        except RuntimeError:
            # Already in an event loop (e.g. inside async context)
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._edge_save(text, str(output_path)))
            finally:
                loop.close()

    def _speak_edge(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name
        try:
            self.save(text, tmp_path)
            self._play_audio(tmp_path)
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    async def _edge_save(self, text: str, path: str) -> None:
        try:
            import edge_tts
        except ImportError as e:
            raise RuntimeError("edge-tts not installed. Run: pip install openbro[voice]") from e
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
        await communicate.save(path)

    @staticmethod
    def _play_audio(path: str) -> None:
        """Play audio file in-process. NEVER invoke the system shell-open path.

        Earlier this used `start /wait <mp3>` as a fallback which on
        Windows opens the user's default music app (Groove / Spotify /
        Media Player) every time the agent speaks — extremely intrusive
        and the app stayed open. The fix: route MP3 through Windows MCI
        (Media Control Interface, a built-in winmm API) which plays the
        file in-process without spawning any UI app. Raises on failure
        so the speak() caller can fall through to pyttsx3 / SAPI.
        """
        if sys.platform == "win32":
            # WAV: winsound is in stdlib and instant.
            if path.lower().endswith(".wav"):
                import winsound

                winsound.PlaySound(path, winsound.SND_FILENAME)
                return
            # MP3 (edge-tts output): MCI via winmm.dll. No external
            # process, no music app, no shell-open side effects.
            _play_mp3_mci(path)
            return
        if sys.platform == "darwin":
            subprocess.run(["afplay", path], check=False)
            return
        # Linux
        for player in ("paplay", "aplay", "ffplay"):
            p = shutil.which(player)
            if p:
                args = [p, path]
                if player == "ffplay":
                    args = [p, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
                subprocess.run(args, check=False)
                return
        raise RuntimeError("No supported audio player found (paplay/aplay/ffplay missing).")

    def _speak_pyttsx3(self, text: str) -> None:
        try:
            import pyttsx3
        except ImportError as e:
            raise RuntimeError("pyttsx3 fallback not installed. Run: pip install pyttsx3") from e
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()


def speak(text: str, voice: str = DEFAULT_VOICE) -> None:
    """One-shot helper: speak text aloud."""
    TextToSpeech(voice=voice).speak(text)
