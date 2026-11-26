"""`webpage` — operate web pages in a real browser window.

The model opens a page and gets back a numbered list of what it can
interact with:

    Search results — https://www.youtube.com/results?search_query=lofi
    [1] input[search] "Search" value="lofi"
    [2] button "Search"
    [3] link "lofi hip hop radio 📚 beats to relax/study to" → https://…

then acts by number — click 3, type into 1 — and every action returns the
fresh state, so it can keep going until the task is done.

Runs on Playwright (`pip install 'openbro[browser]'`) and drives the
Chrome or Edge already installed on the computer; Playwright's own
Chromium is the last fallback. The browser has its own profile under the
storage folder, so sites stay logged in between sessions, separate from
the user's everyday browser.

Safety: clicking anything that looks like buy / pay / send / delete /
submit, and typing into password fields, are dangerous-risk calls, so
the permission gate asks the user first.
"""

from __future__ import annotations

import queue
import re
import threading
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path

from openbro.tools.base import BaseTool, RiskLevel

SAFE_ACTIONS = {
    "open",
    "snapshot",
    "read",
    "scroll",
    "screenshot",
    "back",
    "forward",
    "reload",
    "tabs",
    "switch_tab",
    "new_tab",
    "close_tab",
    "wait",
    "close",
}
INTERACT_ACTIONS = {"click", "type", "select", "press"}
RISKY_WORDS = re.compile(
    r"\b(buy|pay|payment|purchase|place order|order now|checkout|check out|confirm|"
    r"delete|remove|send|transfer|submit|book now|subscribe|unsubscribe|post|publish|"
    r"sign out|log ?out|kharid|bhej|bhugtan|mita)\b",
    re.IGNORECASE,
)

SNAPSHOT_JS = r"""
(maxElements) => {
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],' +
              '[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],' +
              '[role=switch],[role=combobox],[role=searchbox],[contenteditable=true]';
  document.querySelectorAll('[data-openbro-ref]')
    .forEach(e => e.removeAttribute('data-openbro-ref'));
  const items = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2) continue;
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.disabled || el.getAttribute('aria-hidden') === 'true') continue;
    if (el.tagName === 'INPUT' && el.type === 'hidden') continue;
    const inView = r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
    items.push({el, r, inView});
  }
  items.sort((a, b) => (b.inView - a.inView) || (a.r.top - b.r.top) || (a.r.left - b.r.left));
  const out = [];
  let n = 0;
  for (const {el, inView} of items.slice(0, maxElements)) {
    n += 1;
    el.setAttribute('data-openbro-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const isButtonInput = tag === 'input' && ['submit', 'button'].includes(el.type);
    let label = el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') ||
                el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name') ||
                (isButtonInput ? el.value : '') || '';
    if (!label && el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) label = lab.innerText;
    }
    let value = '';
    if (tag === 'select') {
      value = [...el.options].map(o => (o.selected ? '*' : '') + o.text.trim())
        .slice(0, 12).join(' | ');
    } else if (tag === 'input' && el.type === 'password') {
      value = el.value ? '••••' : '';
    } else if (tag === 'input' && ['checkbox', 'radio'].includes(el.type)) {
      value = el.checked ? 'checked' : 'unchecked';
    } else if ('value' in el && typeof el.value === 'string') {
      value = el.value.slice(0, 60);
    }
    out.push({
      ref: n, tag, type: (tag === 'input' ? el.type : '') || '',
      role: el.getAttribute('role') || '',
      label: label.trim().replace(/\s+/g, ' ').slice(0, 90),
      href: tag === 'a' ? (el.href || '') : '', value, inView,
    });
  }
  const body = document.body ? document.body.innerText : '';
  return {
    title: document.title, url: location.href, total: items.length, elements: out,
    text: body.replace(/\n{3,}/g, '\n\n').slice(0, 4000),
  };
}
"""


# ─── the Playwright side (runs only on the worker thread) ───────────


class PlaywrightDriver:
    def __init__(
        self,
        headless: bool = False,
        channel: str = "auto",
        profile_dir: Path | None = None,
        timeout_s: float = 30,
        screenshot_dir: Path | None = None,
    ):
        self.headless = headless
        self.channel = channel
        self.profile_dir = Path(profile_dir or Path.home() / ".openbro" / "browser-profile")
        self.timeout_ms = int(timeout_s * 1000)
        self.screenshot_dir = Path(screenshot_dir or self.profile_dir.parent / "screenshots")
        self._pw = None
        self._ctx = None
        self._page = None

    # lifecycle
    def _ensure(self):
        if self._ctx is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "Web page control needs Playwright: pip install 'openbro[browser]'"
            ) from e
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        channels = ["chrome", "msedge", None] if self.channel == "auto" else [self.channel or None]
        errors = []
        for ch in channels:
            kwargs = {"headless": self.headless, "no_viewport": not self.headless}
            if ch:
                kwargs["channel"] = ch
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), **kwargs
                )
                break
            except Exception as e:
                errors.append(f"{ch or 'bundled chromium'}: {str(e).splitlines()[0][:160]}")
        if self._ctx is None:
            self._pw.stop()
            self._pw = None
            raise RuntimeError(
                "Couldn't start a browser. Install Chrome or Edge, or run "
                "`python -m playwright install chromium`.\n" + "\n".join(errors)
            )
        self._ctx.set_default_timeout(self.timeout_ms)
        self._ctx.on("page", self._on_new_page)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def _on_new_page(self, page):
        self._page = page  # links that open a new tab: follow them

    def _current(self):
        self._ensure()
        if self._page is None or self._page.is_closed():
            open_pages = [p for p in self._ctx.pages if not p.is_closed()]
            self._page = open_pages[-1] if open_pages else self._ctx.new_page()
        return self._page

    def _settle(self, page):
        for state, ms in (("domcontentloaded", 8000), ("networkidle", 2500)):
            try:
                page.wait_for_load_state(state, timeout=ms)
            except Exception:
                pass

    def _locator(self, page, ref=None, text=None):
        if ref is not None and str(ref).strip():
            loc = page.locator(f'[data-openbro-ref="{int(ref)}"]')
            if loc.count() == 0:
                raise RuntimeError(f"No element [{ref}] on the page now — take a new snapshot.")
            return loc.first
        if text:
            loc = page.get_by_text(text, exact=False)
            if loc.count() == 0:
                loc = page.get_by_role("button", name=text)
            if loc.count() == 0:
                raise RuntimeError(f"Nothing with the text {text!r} on the page.")
            return loc.first
        raise RuntimeError("Pass ref (a number from the snapshot) or text.")

    # state
    def snapshot(self, max_elements: int = 80):
        page = self._current()
        state = page.evaluate(SNAPSHOT_JS, max_elements)
        state["tabs"] = len(self._ctx.pages)
        return state

    # actions — each returns the fresh snapshot
    def open(self, url: str):
        if not re.match(r"^[a-z][a-z0-9+.-]*:", url or "", re.I):
            url = "https://" + url.strip()
        page = self._current()
        page.goto(url, wait_until="domcontentloaded")
        self._settle(page)
        return self.snapshot()

    def click(self, ref=None, text=None):
        page = self._current()
        self._locator(page, ref, text).click()
        self._settle(self._current())
        return self.snapshot()

    def type(self, ref=None, text: str = "", submit: bool = False, clear: bool = True):
        page = self._current()
        loc = self._locator(page, ref)
        try:
            if clear:
                loc.fill(text)
            else:
                loc.click()
                page.keyboard.type(text)
        except Exception:
            loc.click()
            page.keyboard.type(text)
        if submit:
            loc.press("Enter")
            self._settle(self._current())
        return self.snapshot()

    def select(self, ref=None, value: str = ""):
        page = self._current()
        loc = self._locator(page, ref)
        try:
            loc.select_option(label=value)
        except Exception:
            loc.select_option(value=value)
        self._settle(page)
        return self.snapshot()

    def press(self, key: str = "Enter"):
        page = self._current()
        page.keyboard.press(key)
        self._settle(self._current())
        return self.snapshot()

    def scroll(self, direction: str = "down", amount: int = 1):
        page = self._current()
        dy = 700 * max(1, min(int(amount or 1), 10)) * (-1 if direction == "up" else 1)
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(400)
        return self.snapshot()

    def read(self, max_chars: int = 20000):
        page = self._current()
        text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        return {
            "title": page.title(),
            "url": page.url,
            "text": text[:max_chars],
            "length": len(text),
        }

    def back(self):
        page = self._current()
        page.go_back(wait_until="domcontentloaded")
        self._settle(page)
        return self.snapshot()

    def forward(self):
        page = self._current()
        page.go_forward(wait_until="domcontentloaded")
        self._settle(page)
        return self.snapshot()

    def reload(self):
        page = self._current()
        page.reload(wait_until="domcontentloaded")
        self._settle(page)
        return self.snapshot()

    def screenshot(self, path: str | None = None, full_page: bool = False):
        page = self._current()
        if path:
            target = Path(path).expanduser()
        else:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            target = self.screenshot_dir / f"web_{datetime.now():%Y%m%d_%H%M%S}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=bool(full_page))
        return {"title": page.title(), "url": page.url, "saved": str(target)}

    def tabs(self):
        self._ensure()
        current = self._current()
        return {
            "tabs": [
                {"index": i, "title": p.title(), "url": p.url, "active": p is current}
                for i, p in enumerate(self._ctx.pages)
            ]
        }

    def switch_tab(self, index: int = 0):
        self._ensure()
        pages = self._ctx.pages
        if not 0 <= int(index) < len(pages):
            raise RuntimeError(f"No tab {index}; there are {len(pages)}.")
        self._page = pages[int(index)]
        self._page.bring_to_front()
        return self.snapshot()

    def new_tab(self, url: str = ""):
        self._ensure()
        self._page = self._ctx.new_page()
        return self.open(url) if url else self.snapshot()

    def close_tab(self, index: int | None = None):
        self._ensure()
        pages = self._ctx.pages
        target = (
            pages[int(index)]
            if index is not None and 0 <= int(index) < len(pages)
            else self._current()
        )
        target.close()
        self._page = None
        return (
            self.snapshot()
            if [p for p in self._ctx.pages if not p.is_closed()]
            else {"closed": True}
        )

    def wait(self, seconds: float = 0, text: str = ""):
        page = self._current()
        if text:
            page.get_by_text(text, exact=False).first.wait_for(timeout=self.timeout_ms)
        else:
            page.wait_for_timeout(int(max(0.0, min(float(seconds or 1), 30.0)) * 1000))
        return self.snapshot()

    def close(self):
        try:
            if self._ctx is not None:
                self._ctx.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._pw = self._ctx = self._page = None
        return {"closed": True}


# ─── one thread owns the browser ────────────────────────────────────


class BrowserWorker:
    """Playwright objects must stay on the thread that created them, but
    tool calls arrive from the REPL thread, the voice thread or a
    sub-agent. Every call is queued to one long-lived worker thread."""

    def __init__(self, factory):
        self._factory = factory
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _start(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="webpage")
                self._thread.start()

    def _loop(self):
        driver = None
        while True:
            method, kwargs, fut = self._q.get()
            try:
                if driver is None:
                    driver = self._factory()
                fut.set_result(getattr(driver, method)(**kwargs))
            except Exception as e:
                fut.set_exception(e)

    def call(self, method: str, timeout: float = 120, **kwargs):
        self._start()
        fut: Future = Future()
        self._q.put((method, kwargs, fut))
        return fut.result(timeout=timeout)


def _default_driver() -> PlaywrightDriver:
    from openbro.utils.config import load_config

    cfg = load_config()
    browser = cfg.get("browser", {}) or {}
    base = Path((cfg.get("storage", {}) or {}).get("base_dir") or Path.home() / ".openbro")
    return PlaywrightDriver(
        headless=bool(browser.get("headless", False)),
        channel=str(browser.get("channel", "auto")),
        profile_dir=Path(browser.get("profile_dir") or base / "browser-profile"),
        timeout_s=float(browser.get("timeout_seconds", 30)),
        screenshot_dir=base / "screenshots",
    )


# ─── the tool ───────────────────────────────────────────────────────


def format_state(state: dict, max_elements: int = 60, text_chars: int = 1500) -> str:
    if "tabs" in state and isinstance(state["tabs"], list):
        rows = []
        for t in state["tabs"]:
            mark = "*" if t["active"] else " "
            rows.append(f"{mark} [{t['index']}] {t['title'] or '(untitled)'} — {t['url']}")
        return "\n".join(rows)
    if state.get("closed"):
        return "Browser closed."
    if "saved" in state:
        where = f"{state.get('title', '')} — {state.get('url', '')}"
        return f"Screenshot saved: {state['saved']} ({where})"
    if "length" in state:
        more = (
            f"\n…[{state['length'] - len(state['text']):,} more chars]"
            if state["length"] > len(state["text"])
            else ""
        )
        return f"{state['title']} — {state['url']}\n\n{state['text']}{more}"

    lines = [f"{state.get('title') or '(untitled)'} — {state.get('url', '')}"]
    if state.get("tabs", 1) > 1:
        lines[0] += f"   ({state['tabs']} tabs)"
    elements = state.get("elements", [])
    if elements:
        lines.append("Elements (numbers to use as ref; in view first):")
    for el in elements[:max_elements]:
        kind = el["tag"]
        if el.get("type") and el["type"] not in ("submit", "button"):
            kind += f"[{el['type']}]"
        elif el.get("role"):
            kind += f"[{el['role']}]"
        line = f'[{el["ref"]}] {kind} "{el["label"]}"'
        if el.get("value"):
            line += f' value="{el["value"]}"'
        if el.get("href"):
            line += f" → {el['href'][:120]}"
        if not el.get("inView"):
            line += " (scroll to see)"
        lines.append(line)
    hidden = max(state.get("total", len(elements)) - min(len(elements), max_elements), 0)
    if hidden:
        lines.append(f"(+{hidden} more elements — scroll or snapshot to see others)")
    text = (state.get("text") or "").strip()
    if text:
        cut = text[:text_chars]
        lines.append("\nPage text:\n" + cut + ("…" if len(text) > text_chars else ""))
    return "\n".join(lines)


class WebpageTool(BaseTool):
    name = "webpage"
    description = (
        "OPERATE web pages in a real browser window: open a site, see its "
        "clickable/typeable elements as a numbered list, click, type, choose "
        "options, press keys, scroll, read the page, take screenshots, manage "
        "tabs. Every action returns the fresh page state with new numbers — "
        "always use numbers from the LATEST result.\n"
        "Use it for tasks on a site: search inside it, fill a form, log in, "
        "follow links, read what's behind navigation. To just show a page to "
        "the user, `browser` is enough; to just read an article, `web` fetch "
        "is faster.\n"
        "Logins persist in OpenBro's own browser profile. Never buy, pay, "
        "send, post or delete without the user's clear go-ahead for that "
        "exact action. If a page needs a captcha or OTP, ask the user."
    )
    risk = RiskLevel.MODERATE

    def __init__(self, worker: BrowserWorker | None = None):
        self._worker = worker
        self._elements: dict[int, dict] = {}

    def _w(self) -> BrowserWorker:
        if self._worker is None:
            self._worker = BrowserWorker(_default_driver)
        return self._worker

    def compute_risk(self, args: dict) -> RiskLevel:
        action = str(args.get("action", "")).lower()
        if action in SAFE_ACTIONS:
            return RiskLevel.SAFE
        if action not in INTERACT_ACTIONS:
            return RiskLevel.MODERATE
        el = None
        try:
            if args.get("ref") not in (None, ""):
                el = self._elements.get(int(args["ref"]))
        except (TypeError, ValueError):
            el = None
        label = " ".join(str(x) for x in ((el or {}).get("label", ""), args.get("text", "")) if x)
        if action == "type" and (el or {}).get("type") == "password":
            return RiskLevel.DANGEROUS
        if action in ("click", "press") and RISKY_WORDS.search(label or ""):
            return RiskLevel.DANGEROUS
        if (
            action == "type"
            and args.get("submit")
            and RISKY_WORDS.search((el or {}).get("label", ""))
        ):
            return RiskLevel.DANGEROUS
        return RiskLevel.MODERATE

    def run(
        self,
        action: str,
        url: str = "",
        ref: int | None = None,
        text: str = "",
        submit: bool = False,
        key: str = "",
        value: str = "",
        direction: str = "down",
        amount: int = 1,
        index: int | None = None,
        path: str = "",
        full_page: bool = False,
        seconds: float = 0,
    ) -> str:
        action = (action or "").lower().strip()
        calls = {
            "open": ("open", {"url": url}),
            "snapshot": ("snapshot", {}),
            "click": ("click", {"ref": ref, "text": text or None}),
            "type": ("type", {"ref": ref, "text": text, "submit": bool(submit)}),
            "select": ("select", {"ref": ref, "value": value or text}),
            "press": ("press", {"key": key or text or "Enter"}),
            "scroll": ("scroll", {"direction": direction, "amount": amount}),
            "read": ("read", {}),
            "back": ("back", {}),
            "forward": ("forward", {}),
            "reload": ("reload", {}),
            "screenshot": ("screenshot", {"path": path or None, "full_page": full_page}),
            "tabs": ("tabs", {}),
            "switch_tab": ("switch_tab", {"index": index or 0}),
            "new_tab": ("new_tab", {"url": url}),
            "close_tab": ("close_tab", {"index": index}),
            "wait": ("wait", {"seconds": seconds, "text": text}),
            "close": ("close", {}),
        }
        if action not in calls:
            return f"Unknown action: {action}. Available: {', '.join(calls)}"
        if action == "open" and not url:
            return "open needs 'url'"
        if action in ("type", "select") and ref in (None, ""):
            return f"{action} needs 'ref' (a number from the latest snapshot)"
        method, kwargs = calls[action]
        try:
            state = self._w().call(method, **kwargs)
        except Exception as e:
            return f"webpage {action} failed: {e}"
        if isinstance(state, dict) and "elements" in state:
            self._elements = {el["ref"]: el for el in state["elements"]}
        if action == "close":
            self._elements = {}
        return format_state(state)

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "open",
                            "snapshot",
                            "click",
                            "type",
                            "select",
                            "press",
                            "scroll",
                            "read",
                            "back",
                            "forward",
                            "reload",
                            "screenshot",
                            "tabs",
                            "switch_tab",
                            "new_tab",
                            "close_tab",
                            "wait",
                            "close",
                        ],
                    },
                    "url": {"type": "string", "description": "open / new_tab"},
                    "ref": {
                        "type": "integer",
                        "description": "Element number from the latest result (click/type/select)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type, visible text to click, or text to wait for",
                    },
                    "submit": {"type": "boolean", "description": "Press Enter after typing"},
                    "key": {
                        "type": "string",
                        "description": "press: Enter, Tab, Escape, ArrowDown…",
                    },
                    "value": {"type": "string", "description": "select: option to choose"},
                    "direction": {"type": "string", "enum": ["down", "up"]},
                    "amount": {"type": "integer", "description": "scroll: screens (1-10)"},
                    "index": {"type": "integer", "description": "switch_tab / close_tab"},
                    "path": {"type": "string", "description": "screenshot file (optional)"},
                    "full_page": {"type": "boolean", "description": "screenshot whole page"},
                    "seconds": {"type": "number", "description": "wait time (max 30)"},
                },
                "required": ["action"],
            },
        }
