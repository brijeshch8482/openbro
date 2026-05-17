"""Voice mode entry point - mic in, TTS out, agent in middle.

Voice mode enables Boss mode permissions by default, so every tool the
agent wants to call gets a voice prompt ("Bhai, X tool chalau? Haan ya nahi?").
"""

from rich.console import Console

from openbro.core.agent import Agent
from openbro.core.permissions import PermissionGate
from openbro.utils.config import load_config
from openbro.utils.language import voice_for

console = Console()


def run_voice_mode():
    config = load_config()
    voice_cfg = config.get("voice", {}) or {}
    if not voice_cfg.get("enabled", True):
        console.print("[yellow]Voice disabled in config.[/yellow]")
        return

    try:
        voice_listener_cls = globals().get("VoiceListener")
        text_to_speech_cls = globals().get("TextToSpeech")
        if voice_listener_cls is None:
            from openbro.voice.listener import VoiceListener as voice_listener_cls
        if text_to_speech_cls is None:
            from openbro.voice.tts import TextToSpeech as text_to_speech_cls
    except Exception as e:
        console.print(f"[red]Voice deps not available: {e}[/red]")
        console.print(
            "[dim]Install: pip install openbro[voice] "
            "(faster-whisper, edge-tts, sounddevice, numpy)[/dim]"
        )
        return

    tts = text_to_speech_cls(voice=voice_cfg.get("tts_voice", "en-IN-NeerjaNeural"))

    # Build listener first (without callback) so we can pass it to the gate
    try:
        listener = voice_listener_cls(
            wake_words=voice_cfg.get("wake_words"),
            stt_model=voice_cfg.get("stt_model", "small"),
            stt_language=voice_cfg.get("stt_language"),
            stt_device=voice_cfg.get("stt_device", "cpu"),
            stt_compute_type=voice_cfg.get("stt_compute_type", "int8"),
            stt_beam_size=int(voice_cfg.get("stt_beam_size", 5)),
            stt_vad_filter=bool(voice_cfg.get("stt_vad_filter", True)),
            chunk_seconds=float(voice_cfg.get("chunk_seconds", 8.0)),
            silence_threshold=float(voice_cfg.get("silence_threshold", 0.003)),
            silence_seconds=float(voice_cfg.get("silence_seconds", 0.8)),
            speak_replies=voice_cfg.get("speak_replies", True),
            on_transcript=None,
            assistant_name="OpenBro",
            ack_phrases=voice_cfg.get("ack_phrases"),
        )
    except Exception as e:
        console.print(f"[red]Voice listener init failed: {e}[/red]")
        return

    # Voice mode → Boss mode by default (every tool needs voice approval)
    perm_mode = config.get("safety", {}).get("permission_mode", "boss")
    gate = PermissionGate(
        mode=perm_mode,
        channel="voice",
        voice_listener=listener,
        tts=tts,
    )

    agent = Agent(permission_gate=gate)
    console.print("[bold cyan]Voice mode active.[/bold cyan]")
    console.print(
        "[dim]Wake words: hey openbro, hi openbro, ok openbro. "
        f"Permission mode: {gate.mode}. Ctrl+C to exit.[/dim]\n"
    )

    def handle(text: str) -> str:
        try:
            reply = agent.chat(text)
            # Switch TTS voice based on detected reply language
            tts.voice = voice_for(agent.last_language)
            return reply
        except Exception as e:
            return f"Error: {e}"

    listener.on_transcript = handle
    listener.tts = tts  # share the same TTS so language switching applies

    try:
        listener.run()
    except KeyboardInterrupt:
        console.print("\n[bold cyan]Voice mode bandh.[/bold cyan]")
