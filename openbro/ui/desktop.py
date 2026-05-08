"""OpenBro native desktop UI — customtkinter, system-level, dark theme.

Single window, three regions:
    +----------------------------------------------------------+
    |  [◆ OpenBro]   [model: llama3.1:8b]  [● online]  [⚙]   |
    +-----------------+----------------------------------------+
    |   Activity      |   Chat                                 |
    |   ───────       |   You: ...                             |
    |   thinking      |   Bro: ...                             |
    |   tool: app     |                                        |
    |   ...           |                                        |
    |                 |                                        |
    +-----------------+----------------------------------------+
    |  [🎤]  [Type or speak...                ]  [→]            |
    +----------------------------------------------------------+

Voice: system-level (sounddevice + faster-whisper + edge-tts), not browser.
Hybrid: pipeline detects online/offline and routes accordingly.
"""

from __future__ import annotations

import threading
import time

UI_DEPS_HINT = (
    "Desktop UI deps not installed. Run: pip install 'openbro[gui]' (installs customtkinter)"
)


def run_desktop():
    """Launch the OpenBro desktop window. Blocks until user closes it."""
    try:
        import customtkinter as ctk
    except ImportError:
        print(UI_DEPS_HINT)
        return

    from openbro.brain import Brain
    from openbro.brain.memory import SemanticMemory
    from openbro.brain.skills import SkillRegistry
    from openbro.core.activity import get_bus
    from openbro.core.agent import Agent
    from openbro.core.reasoning import ReasoningPipeline
    from openbro.utils.config import load_config

    # ─── Brain wiring (memory + skills attached to brain instance) ──
    brain = Brain.load()
    brain.memory = SemanticMemory(brain.storage.memory_db_path)
    brain.skills = SkillRegistry(brain.storage.skills_dir)

    agent = Agent()
    pipeline = ReasoningPipeline(brain, agent)
    bus = get_bus()
    cfg = load_config()

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    app = OpenBroApp(brain, agent, pipeline, bus, cfg)
    app.mainloop()


class OpenBroApp:
    """Customtkinter desktop window — composed of header, activity, chat, input."""

    def __init__(self, brain, agent, pipeline, bus, cfg):
        import customtkinter as ctk

        self.brain = brain
        self.agent = agent
        self.pipeline = pipeline
        self.bus = bus
        self.cfg = cfg
        self.voice_listener = None
        self.voice_running = False
        self._unsub_bus = None

        self.root = ctk.CTk()
        self.root.title("OpenBro - Tera Apna AI Bro")
        self.root.geometry("1100x720")
        self.root.minsize(800, 500)

        self._build_header(ctk)
        self._build_body(ctk)
        self._build_footer(ctk)

        # Subscribe to activity bus to stream events into the sidebar
        self._unsub_bus = bus.subscribe(self._on_activity)

        # Cleanup on close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Welcome message
        self._add_message("bro", f"Bol bhai, kya kaam hai? (LLM: {agent.provider.name()})")

    # ─── header ────────────────────────────────────────────────────

    def _build_header(self, ctk):
        header = ctk.CTkFrame(self.root, height=50, corner_radius=0)
        header.pack(side="top", fill="x")

        title = ctk.CTkLabel(
            header,
            text="◆  OpenBro",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(side="left", padx=16, pady=10)

        self.model_label = ctk.CTkLabel(
            header,
            text=self.agent.provider.name(),
            font=ctk.CTkFont(size=12),
            text_color="#8b949e",
        )
        self.model_label.pack(side="left", padx=16)

        self.status_label = ctk.CTkLabel(
            header,
            text="● online",
            font=ctk.CTkFont(size=12),
            text_color="#3fb950",
        )
        self.status_label.pack(side="right", padx=16)

        self._update_online_status()

    # ─── body (activity sidebar + chat) ────────────────────────────

    def _build_body(self, ctk):
        body = ctk.CTkFrame(self.root, corner_radius=0)
        body.pack(side="top", fill="both", expand=True)

        # Sidebar
        self.sidebar = ctk.CTkFrame(body, width=260, corner_radius=0)
        self.sidebar.pack(side="left", fill="y", padx=0, pady=0)
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(
            self.sidebar,
            text="ACTIVITY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#8b949e",
        ).pack(anchor="w", padx=12, pady=(12, 4))

        self.activity_frame = ctk.CTkScrollableFrame(self.sidebar, corner_radius=0)
        self.activity_frame.pack(fill="both", expand=True, padx=8, pady=4)

        # Chat area
        chat_container = ctk.CTkFrame(body, corner_radius=0)
        chat_container.pack(side="right", fill="both", expand=True)

        self.chat_frame = ctk.CTkScrollableFrame(chat_container, corner_radius=0)
        self.chat_frame.pack(fill="both", expand=True, padx=12, pady=12)

    # ─── footer (input + mic) ──────────────────────────────────────

    def _build_footer(self, ctk):
        footer = ctk.CTkFrame(self.root, height=64, corner_radius=0)
        footer.pack(side="bottom", fill="x")

        self.mic_btn = ctk.CTkButton(
            footer,
            text="🎤",
            width=44,
            height=44,
            font=ctk.CTkFont(size=18),
            command=self._toggle_voice,
        )
        self.mic_btn.pack(side="left", padx=(12, 6), pady=10)

        self.input = ctk.CTkEntry(
            footer,
            placeholder_text="Type karo ya 🎤 dabao...",
            font=ctk.CTkFont(size=14),
            height=44,
        )
        self.input.pack(side="left", fill="x", expand=True, padx=6, pady=10)
        self.input.bind("<Return>", lambda e: self._send())
        self.input.focus()

        self.send_btn = ctk.CTkButton(
            footer,
            text="→",
            width=44,
            height=44,
            font=ctk.CTkFont(size=20, weight="bold"),
            command=self._send,
        )
        self.send_btn.pack(side="right", padx=(6, 12), pady=10)

    # ─── event handlers ────────────────────────────────────────────

    def _send(self):
        text = self.input.get().strip()
        if not text:
            return
        self.input.delete(0, "end")
        self._add_message("user", text)
        self._set_thinking(True)
        # Run pipeline in background thread so UI doesn't freeze
        threading.Thread(target=self._handle_prompt, args=(text,), daemon=True).start()

    def _handle_prompt(self, prompt: str):
        try:
            result = self.pipeline.handle(prompt)
            reply = result.reply
            meta = ""
            if result.used_skill:
                meta = f"  [skill: {result.used_skill}]"
            elif result.memory_hits:
                meta = f"  [{result.memory_hits} memories used]"
            self.root.after(0, lambda: self._add_message("bro", reply + meta))
        except Exception as exc:
            err = f"Error: {exc}"
            self.root.after(0, lambda: self._add_message("bro", err))
        finally:
            self.root.after(0, lambda: self._set_thinking(False))

    def _toggle_voice(self):
        if self.voice_running:
            self._stop_voice()
        else:
            self._start_voice()

    def _start_voice(self):
        try:
            from openbro.voice.listener import VoiceListener
            from openbro.voice.tts import TextToSpeech
        except Exception as e:
            self._add_message(
                "bro",
                f"Voice deps missing: {e}\nInstall: pip install 'openbro[voice]'",
            )
            return

        voice_cfg = self.cfg.get("voice", {}) or {}
        tts = TextToSpeech(voice=voice_cfg.get("tts_voice", "en-IN-NeerjaNeural"))
        try:
            self.voice_listener = VoiceListener(
                wake_words=voice_cfg.get("wake_words"),
                stt_model=voice_cfg.get("stt_model", "base"),
                speak_replies=voice_cfg.get("speak_replies", True),
            )
        except Exception as e:
            self._add_message("bro", f"Voice listener init failed: {e}")
            return

        self.voice_listener.tts = tts

        def on_text(text: str) -> str:
            # Voice transcript flows through the same pipeline as typed input
            self.root.after(0, lambda: self._add_message("user", f"🎤 {text}"))
            try:
                result = self.pipeline.handle(text)
                self.root.after(0, lambda: self._add_message("bro", result.reply))
                # Voice mode replies aloud too
                tts.speak(result.reply)
                return result.reply
            except Exception as e:
                err = f"Error: {e}"
                self.root.after(0, lambda: self._add_message("bro", err))
                return err

        self.voice_listener.on_transcript = on_text
        threading.Thread(target=self.voice_listener.run, daemon=True).start()
        self.voice_running = True
        self.mic_btn.configure(text="●", fg_color="#f85149")
        self._add_message("bro", "🎤 Voice on. Bol 'hey bro' wake word ke saath.")

    def _stop_voice(self):
        if self.voice_listener:
            try:
                self.voice_listener.stop()
            except Exception:
                pass
        self.voice_listener = None
        self.voice_running = False
        self.mic_btn.configure(text="🎤", fg_color=("#3a7ebf", "#1f538d"))

    def _on_close(self):
        try:
            self._stop_voice()
        except Exception:
            pass
        try:
            if self._unsub_bus:
                self._unsub_bus()
        except Exception:
            pass
        self.root.destroy()

    # ─── UI helpers ────────────────────────────────────────────────

    def _add_message(self, who: str, text: str):
        import customtkinter as ctk

        bubble = ctk.CTkFrame(
            self.chat_frame,
            fg_color=("#2f81f7", "#1f538d") if who == "user" else ("#21262d", "#161b22"),
            corner_radius=12,
        )
        bubble.pack(
            anchor="e" if who == "user" else "w",
            padx=8,
            pady=4,
            fill=None,
        )
        label = ctk.CTkLabel(
            bubble,
            text=text,
            font=ctk.CTkFont(size=13),
            justify="left",
            wraplength=600,
            text_color="white" if who == "user" else "#e6edf3",
        )
        label.pack(padx=14, pady=10)

        # Auto-scroll
        self.root.update_idletasks()
        try:
            self.chat_frame._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _on_activity(self, ev):
        # Called from worker threads via bus.subscribe
        self.root.after(0, lambda: self._append_activity(ev.kind, ev.text))

    def _append_activity(self, kind: str, text: str):
        import customtkinter as ctk

        ts = time.strftime("%H:%M:%S")
        line = ctk.CTkLabel(
            self.activity_frame,
            text=f"{ts}  {kind:11} {text[:60]}",
            font=ctk.CTkFont(size=10, family="Consolas"),
            text_color="#8b949e",
            anchor="w",
            justify="left",
        )
        line.pack(anchor="w", padx=2, pady=1, fill="x")
        # Cap at 200 entries so we don't leak
        kids = self.activity_frame.winfo_children()
        if len(kids) > 200:
            kids[0].destroy()

    def _set_thinking(self, thinking: bool):
        if thinking:
            self.status_label.configure(text="● thinking...", text_color="#d29922")
        else:
            self._update_online_status()

    def _update_online_status(self):
        # Quick non-blocking check — use cached state for now; full hybrid
        # detection lands in the pipeline (online vs offline routing).
        try:
            import socket

            socket.setdefaulttimeout(0.5)
            socket.gethostbyname("github.com")
            online = True
        except Exception:
            online = False
        if online:
            self.status_label.configure(text="● online", text_color="#3fb950")
        else:
            self.status_label.configure(text="● offline", text_color="#d29922")

    def mainloop(self):
        self.root.mainloop()
