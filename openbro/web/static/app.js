"use strict";

// ── state ────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const thread = $("thread");
const input = $("input");
const send = $("send");
const scroller = $("scroll");
let sessionId = sessionStorage.getItem("openbro.session") || null;
let busy = false;
let maxChars = 1500;

// ── safe Markdown → HTML (escape first, then a small subset) ─────────
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function inline(s) {
  // s is already escaped. Code spans first so their content stays literal.
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, t, u) => `<a href="${u}" target="_blank" rel="noopener nofollow">${t}</a>`);
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    (_, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener nofollow">${u}</a>`);
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
}

function isTableSep(line) { return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line); }
function cells(line) { return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())); }

// Some models add citation markers like 【2†doc=…】; they mean nothing to readers.
function stripMarkers(s) { return (s || "").replace(/【[^】]*】/g, ""); }

function renderMarkdown(src) {
  const lines = esc(stripMarkers(src)).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```/);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(`<pre><code>${body.join("\n")}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { const n = Math.min(4, h[1].length + 1); out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push("<table><thead><tr>" + head.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
      continue;
    }
    const li = line.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
    if (li) {
      const ordered = /\d/.test(li[1]);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
        if (!m) break;
        items.push(`<li>${inline(m[2])}</li>`);
        i++;
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,4}\s|[-*•]\s|\d+[.)]\s)/.test(lines[i])) {
      para.push(inline(lines[i++]));
    }
    if (para.length) out.push(`<p>${para.join("<br>")}</p>`);
    else { out.push(`<p>${inline(line)}</p>`); i++; }
  }
  return out.join("");
}

// ── DOM helpers ──────────────────────────────────────────────────────
function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
}
function nearBottom() { return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120; }
function toBottom(force) { if (force || nearBottom()) scroller.scrollTop = scroller.scrollHeight; }

function addUser(text) {
  const li = el("li", "msg user");
  const b = el("div", "bubble");
  b.textContent = text;
  li.appendChild(b);
  thread.appendChild(li);
}

function addBot() {
  const li = el("li", "msg bot");
  li.appendChild(el("div", "avatar", "◆"));
  const body = el("div", "body");
  const steps = el("div", "steps");
  steps.hidden = true;
  const thinking = el("div", "thinking", '<span class="dots"><i></i><i></i><i></i></span><span class="label">thinking…</span>');
  const answer = el("div", "md");
  body.append(steps, thinking, answer);
  li.appendChild(body);
  thread.appendChild(li);
  return { steps, thinking, answer, running: {} };
}

function setThinking(view, text) {
  view.thinking.hidden = false;
  view.thinking.querySelector(".label").textContent = text;
}

function onEvent(view, ev) {
  if (ev.type === "session") {
    sessionId = ev.id;
    sessionStorage.setItem("openbro.session", ev.id);
  } else if (ev.type === "thinking") {
    setThinking(view, ev.step > 1 ? `thinking… step ${ev.step}` : "thinking…");
  } else if (ev.type === "tool" && ev.phase === "start") {
    view.steps.hidden = false;
    const row = el("div", "step");
    row.innerHTML = `<span class="i">›</span><span class="t">${esc(ev.tool)}</span><span class="d">${esc(ev.detail || "")}</span><span class="s">…</span>`;
    view.steps.appendChild(row);
    (view.running[ev.tool] = view.running[ev.tool] || []).push(row);
    setThinking(view, `running ${ev.tool}…`);
  } else if (ev.type === "tool" && ev.phase === "end") {
    const row = (view.running[ev.tool] || []).shift();
    if (row) {
      row.querySelector(".i").outerHTML = ev.ok ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>';
      row.querySelector(".s").textContent = `${ev.elapsed}s`;
      if (ev.preview) row.title = ev.preview;
    }
    setThinking(view, "thinking…");
  } else if (ev.type === "answer") {
    view.thinking.hidden = true;
    view.answer.innerHTML = renderMarkdown(ev.text || "(no answer)");
  } else if (ev.type === "error") {
    view.thinking.hidden = true;
    view.answer.innerHTML = `<p class="error">${esc(ev.message)}</p>`;
  }
  toBottom();
}

// ── sending ──────────────────────────────────────────────────────────
async function ask(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true;
  send.disabled = true;
  $("hero").hidden = true;
  addUser(text);
  const view = addBot();
  toBottom(true);
  input.value = "";
  autosize();
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    if (!res.ok || !res.body) {
      let msg = `Request failed (${res.status}).`;
      try { msg = (await res.json()).error || msg; } catch (_) { /* not JSON */ }
      onEvent(view, { type: "error", message: msg });
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let cut;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          try { onEvent(view, JSON.parse(line.slice(6))); } catch (_) { /* skip bad frame */ }
        }
      }
    }
    if (!view.thinking.hidden && !view.answer.innerHTML) {
      onEvent(view, { type: "error", message: "The connection closed before an answer arrived." });
    }
  } catch (e) {
    onEvent(view, { type: "error", message: "Network error — check your connection and try again." });
  } finally {
    busy = false;
    send.disabled = false;
    input.focus();
  }
}

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
  const n = input.value.length;
  $("limit").textContent = n > maxChars * 0.8 ? `${n} / ${maxChars}` : "";
}

$("form").addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(input.value); }
});
input.addEventListener("input", autosize);

$("new-chat").addEventListener("click", async () => {
  if (busy) return;
  if (sessionId) {
    fetch("/api/reset", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).catch(() => {});
  }
  sessionId = null;
  sessionStorage.removeItem("openbro.session");
  thread.innerHTML = "";
  $("hero").hidden = false;
  input.focus();
});

// ── boot ─────────────────────────────────────────────────────────────
fetch("/api/info").then((r) => r.json()).then((info) => {
  $("ver").textContent = `v${info.version}`;
  $("model").textContent = info.model || "model";
  maxChars = info.limits?.max_message_chars || maxChars;
  input.maxLength = maxChars;
  const chips = $("chips");
  for (const q of info.suggestions || []) {
    const b = el("button", "chip");
    b.type = "button";
    b.textContent = q;
    b.addEventListener("click", () => ask(q));
    chips.appendChild(b);
  }
}).catch(() => { $("model").textContent = "offline"; });

// A visitor returning to the tab keeps their session, but the page starts
// fresh (the server may have expired it); the next message continues it.
input.focus();
