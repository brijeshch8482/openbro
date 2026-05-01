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
        """Play audio file using a system player. Cross-platform."""
        if sys.platform == "win32":
            # Use Windows Media Player command line
            try:
                import winsound

                # winsound only handles WAV; for MP3 use ffplay/start
                if path.lower().endswith(".wav"):
                    winsound.PlaySound(path, winsound.SND_FILENAME)
                    return
            except Exception:
                pass
            # Fallback: 'start' command opens default player (non-blocking)
            ffplay = shutil.which("ffplay")
            if ffplay:
                subprocess.run(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                    check=False,
                )
            else:
                # Last resort: PowerShell SoundPlayer (WAV only) or start
                subprocess.run(["cmd", "/c", "start", "/wait", "", path], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["afplay", path], check=False)
        else:
            for player in ("paplay", "aplay", "ffplay"):
                p = shutil.which(player)
                if p:
                    args = [p, path]
                    if player == "ffplay":
                        args = [p, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
                    subprocess.run(args, check=False)
                    return

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
