"""System tray app — runs in the background, summon the desktop window
with a global hotkey or right-click menu.

Default hotkey: Ctrl+Shift+B (toggles the OpenBro window).

Stack:
    pystray   — cross-platform tray icon + menu
    pynput    — global hotkey (cross-platform)
    PIL       — for the tray icon image
"""

from __future__ import annotations

import threading

TRAY_DEPS_HINT = (
    "Tray deps not installed. Run: pip install 'openbro[tray]' (installs pystray, pynput, pillow)"
)


def _make_icon():
    """Generate a small in-memory PNG so we don't ship a binary asset."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Simple geometric "OB" mark on a dark blue background
    draw.rounded_rectangle((4, 4, 60, 60), radius=10, fill=(31, 83, 141, 255))
    draw.text((16, 14), "OB", fill=(255, 255, 255, 255))
    return img


class TrayApp:
    def __init__(self):
        self._desktop_thread: threading.Thread | None = None
        self._icon = None
        self._desktop_app = None
        self._hotkey_listener = None

    # ─── desktop lifecycle ───────────────────────────────────────

    def _launch_desktop(self):
        """Start the desktop window in a worker thread; idempotent."""
        if self._desktop_thread and self._desktop_thread.is_alive():
            return

        def _run():
            try:
                from openbro.ui.desktop import run_desktop

                run_desktop()
            except Exception as e:
                print(f"[tray] desktop launch failed: {e}")

        self._desktop_thread = threading.Thread(target=_run, daemon=True)
        self._desktop_thread.start()

    # ─── tray menu actions ───────────────────────────────────────

    def on_show(self, icon=None, item=None):
        self._launch_desktop()

    def on_setup(self, icon=None, item=None):
        from openbro.cli.wizard import run_wizard

        threading.Thread(target=run_wizard, daemon=True).start()

    def on_quit(self, icon=None, item=None):
        if self._icon:
            self._icon.stop()
        if self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass

    # ─── global hotkey ───────────────────────────────────────────

    def _start_hotkey(self):
        try:
            from pynput import keyboard
        except ImportError:
            return

        def _toggle():
            self._launch_desktop()

        try:
            self._hotkey_listener = keyboard.GlobalHotKeys({"<ctrl>+<shift>+b": _toggle})
            self._hotkey_listener.start()
        except Exception as e:
            print(f"[tray] hotkey unavailable: {e}")

    # ─── main loop ───────────────────────────────────────────────

    def run(self) -> None:
        try:
            import pystray
            from pystray import Menu, MenuItem
        except ImportError:
            print(TRAY_DEPS_HINT)
            return

        icon_image = _make_icon()
        if icon_image is None:
            print(TRAY_DEPS_HINT)
            return

        self._start_hotkey()

        menu = Menu(
            MenuItem("Open OpenBro", self.on_show, default=True),
            MenuItem("Re-run Setup", self.on_setup),
            Menu.SEPARATOR,
            MenuItem("Quit", self.on_quit),
        )
        self._icon = pystray.Icon("openbro", icon_image, "OpenBro", menu)
        # Auto-launch the window on startup so the user sees something
        threading.Timer(0.5, self._launch_desktop).start()
        self._icon.run()


def run_tray() -> None:
    """CLI entry point — `openbro --tray`."""
    TrayApp().run()
