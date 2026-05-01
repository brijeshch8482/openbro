"""Voice mode entry point - mic in, TTS out, agent in middle."""

from rich.console import Console

from openbro.core.agent import Agent
from openbro.utils.config import load_config

console = Console()


def run_voice_mode():
    config = load_config()
    voice_cfg = config.get("voice", {}) or {}
    if not voice_cfg.get("enabled", True):
        console.print("[yellow]Voice disabled in config.[/yellow]")
        return

    try:
        from openbro.voice.listener import VoiceListener
    except Exception as e:
        console.print(f"[red]Voice deps not available: {e}[/red]")
        console.print(
            "[dim]Install: pip install openbro[voice] "
            "(faster-whisper, edge-tts, sounddevice, numpy)[/dim]"
        )
        return

    agent = Agent()
    console.print("[bold cyan]🎙️  Voice mode active.[/bold cyan]")
    console.print("[dim]Wake words: hey bro, hi bro, ok bro. Ctrl+C to exit.[/dim]\n")

    def handle(text: str) -> str:
        try:
            return agent.chat(text)
        except Exception as e:
            return f"Error: {e}"

    try:
        listener = VoiceListener(
            wake_words=voice_cfg.get("wake_words"),
            stt_model=voice_cfg.get("stt_model", "base"),
            speak_replies=voice_cfg.get("speak_replies", True),
            on_transcript=handle,
        )
    except Exception as e:
        console.print(f"[red]Voice listener init failed: {e}[/red]")
        return
    try:
        listener.run()
    except KeyboardInterrupt:
        console.print("\n[bold cyan]Voice mode bandh.[/bold cyan]")
