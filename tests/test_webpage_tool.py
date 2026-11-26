"""webpage tool — browser automation (open, snapshot, click, type, select…)."""

import threading

import pytest

from openbro.tools.base import RiskLevel
from openbro.tools.webpage_tool import BrowserWorker, PlaywrightDriver, WebpageTool, format_state


def _state(elements, title="Shop", url="https://shop.test/", text="Welcome", total=None):
    return {
        "title": title,
        "url": url,
        "elements": elements,
        "total": total if total is not None else len(elements),
        "text": text,
        "tabs": 1,
    }


def _el(ref, tag="button", label="", **kw):
    base = {
        "ref": ref,
        "tag": tag,
        "type": "",
        "role": "",
        "label": label,
        "href": "",
        "value": "",
        "inView": True,
    }
    base.update(kw)
    return base


class FakeDriver:
    """Stands in for Playwright; records calls and the thread they run on."""

    def __init__(self):
        self.calls = []
        self.threads = set()

    def _rec(self, name, **kw):
        self.calls.append((name, kw))
        self.threads.add(threading.get_ident())

    def open(self, url):
        self._rec("open", url=url)
        return _state(
            [
                _el(1, "input", "Search", type="search"),
                _el(2, "button", "Search"),
                _el(3, "button", "Buy now"),
                _el(4, "input", "Password", type="password"),
                _el(5, "a", "Next page", href="https://shop.test/2", inView=False),
            ],
            total=9,
        )

    def click(self, ref=None, text=None):
        self._rec("click", ref=ref, text=text)
        return _state([_el(1, "button", "Done")], title="Clicked")

    def type(self, ref=None, text="", submit=False, clear=True):
        self._rec("type", ref=ref, text=text, submit=submit)
        return _state([_el(1, "input", "Search", type="search", value=text)])

    def read(self):
        self._rec("read")
        return {"title": "T", "url": "u", "text": "abc", "length": 10}

    def tabs(self):
        return {"tabs": [{"index": 0, "title": "A", "url": "a", "active": True}]}

    def boom(self):
        raise RuntimeError("page crashed")


@pytest.fixture
def tool():
    driver = FakeDriver()
    return WebpageTool(BrowserWorker(lambda: driver)), driver


def test_open_formats_numbered_elements(tool):
    t, driver = tool
    out = t.run(action="open", url="shop.test")
    assert out.splitlines()[0] == "Shop — https://shop.test/"
    assert '[1] input[search] "Search"' in out
    assert '[5] a "Next page" → https://shop.test/2 (scroll to see)' in out
    assert "(+4 more elements" in out
    assert "Page text:\nWelcome" in out
    assert driver.calls[0] == ("open", {"url": "shop.test"})


def test_calls_run_on_one_worker_thread(tool):
    t, driver = tool
    t.run(action="open", url="x")
    t.run(action="click", ref=2)
    other = threading.Thread(target=lambda: t.run(action="type", ref=1, text="hi"))
    other.start()
    other.join()
    assert len(driver.threads) == 1
    assert threading.get_ident() not in driver.threads


def test_risk_uses_latest_snapshot(tool):
    t, _ = tool
    assert t.compute_risk({"action": "open"}) == RiskLevel.SAFE
    assert t.compute_risk({"action": "read"}) == RiskLevel.SAFE
    t.run(action="open", url="x")
    assert t.compute_risk({"action": "click", "ref": 2}) == RiskLevel.MODERATE
    assert t.compute_risk({"action": "click", "ref": 3}) == RiskLevel.DANGEROUS  # Buy now
    assert t.compute_risk({"action": "type", "ref": 4, "text": "x"}) == RiskLevel.DANGEROUS
    assert t.compute_risk({"action": "type", "ref": 1, "text": "x"}) == RiskLevel.MODERATE
    assert t.compute_risk({"action": "click", "text": "Place order"}) == RiskLevel.DANGEROUS
    assert t.compute_risk({"action": "click", "text": "Kharid lo"}) == RiskLevel.DANGEROUS


def test_validation_and_errors(tool):
    t, _ = tool
    assert "open needs 'url'" in t.run(action="open")
    assert "needs 'ref'" in t.run(action="type", text="x")
    assert "Unknown action" in t.run(action="fly")
    t._worker = BrowserWorker(lambda: FakeDriver())
    t._worker._factory = lambda: type("D", (), {"snapshot": lambda self: 1 / 0})()
    assert "webpage snapshot failed" in t.run(action="snapshot")


def test_format_read_tabs_and_screenshot():
    assert format_state({"title": "T", "url": "u", "text": "abc", "length": 10}).endswith(
        "…[7 more chars]"
    )
    assert (
        format_state({"tabs": [{"index": 0, "title": "A", "url": "a", "active": True}]})
        == "* [0] A — a"
    )
    assert format_state({"saved": "x.png", "title": "T", "url": "u"}).startswith(
        "Screenshot saved: x.png"
    )
    assert format_state({"closed": True}) == "Browser closed."


def test_missing_playwright_message(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="openbro\\[browser\\]"):
        PlaywrightDriver(profile_dir=tmp_path / "p").snapshot()


def test_registered_as_builtin():
    from openbro.tools.registry import ToolRegistry

    reg = ToolRegistry()
    assert "webpage" in reg.list_tools()
    assert reg.get_risk("webpage", {"action": "snapshot"}) == "safe"


# ─── real browser (runs only where Playwright + a browser are available) ─


def _browser_available():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            for ch in ("chrome", "msedge", None):
                try:
                    kw = {"headless": True}
                    if ch:
                        kw["channel"] = ch
                    p.chromium.launch(**kw).close()
                    return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


PAGE = """<!doctype html><html><head><title>Fee form</title></head><body>
<h1>Fee payment</h1>
<label for="name">Student name</label><input id="name" type="text">
<select id="term"><option>Term 1</option><option>Term 2</option></select>
<button id="go" onclick="show()">Show</button>
<p id="out"></p>
<script>
function show() {
  const name = document.getElementById('name').value;
  const term = document.getElementById('term').value;
  document.getElementById('out').innerText = 'Hello ' + name + ' / ' + term;
}
</script>
<a href="second.html">Next page</a>
</body></html>"""


@pytest.mark.skipif(not _browser_available(), reason="needs Playwright and a browser")
def test_real_browser_form_flow(tmp_path):
    (tmp_path / "form.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "second.html").write_text(
        "<html><head><title>Second</title></head><body>Page two</body></html>", encoding="utf-8"
    )
    driver = PlaywrightDriver(headless=True, profile_dir=tmp_path / "profile", timeout_s=20)
    tool = WebpageTool(BrowserWorker(lambda: driver))
    try:
        out = tool.run(action="open", url=(tmp_path / "form.html").as_uri())
        assert out.startswith("Fee form — file:")
        refs = {el["label"]: el["ref"] for el in tool._elements.values()}
        assert {"Student name", "Show", "Next page"} <= set(refs)

        tool.run(action="type", ref=refs["Student name"], text="Asha")
        select_ref = next(r for r, el in tool._elements.items() if el["tag"] == "select")
        tool.run(action="select", ref=select_ref, value="Term 2")
        out = tool.run(action="click", ref=refs["Show"])
        assert "Hello Asha / Term 2" in out

        assert "Hello Asha" in tool.run(action="read")
        shot = tool.run(action="screenshot", path=str(tmp_path / "shot.png"))
        assert (tmp_path / "shot.png").exists() and "Screenshot saved" in shot

        out = tool.run(action="click", text="Next page")
        assert out.startswith("Second — file:")
        assert tool.run(action="back").startswith("Fee form")
    finally:
        tool.run(action="close")
