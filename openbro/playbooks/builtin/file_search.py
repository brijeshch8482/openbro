"""FileSearchPlaybook — 'kitne X documents D drive me' and similar.

Captured failure: agent counted only .docx for 'documents', missed PDFs;
on a follow-up filtered with startswith('fee') which missed
'College_Fee_Receipt.pdf'. This playbook does the right thing the FIRST
time: multi-extension match + substring filter + table output with real
filenames.
"""

from __future__ import annotations

import os
import re
import time

from openbro.playbooks.base import Playbook, PlaybookContext, render_table

# Extension groups for "documents" / "images" / "spreadsheets" / "videos" /
# "music" / "code". Single source of truth so a typo in one place doesn't
# silently miss half the files.
_EXT_GROUPS = {
    "documents": {".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".pages"},
    "docs": {".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".pages"},
    "files": {".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".pages"},
    "papers": {".pdf", ".docx", ".doc"},
    "images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".svg", ".heic"},
    "photos": {".jpg", ".jpeg", ".png", ".heic"},
    "pictures": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"},
    "spreadsheets": {".xlsx", ".xls", ".csv", ".ods"},
    "sheets": {".xlsx", ".xls", ".csv"},
    "videos": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"},
    "music": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"},
    "code": {".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp", ".rb", ".php"},
    "scripts": {".sh", ".ps1", ".bat", ".py"},
    "pdfs": {".pdf"},
    "pdf": {".pdf"},
    "word documents": {".docx", ".doc"},
    "excel": {".xlsx", ".xls", ".csv"},
}


def _expand_extensions(group_or_ext: str) -> set[str]:
    """Map 'documents' -> {.pdf, .docx, ...}; '.pdf' -> {.pdf}."""
    key = group_or_ext.lower().strip().rstrip("s")  # singular form
    if key in _EXT_GROUPS:
        return _EXT_GROUPS[key]
    plural = key + "s"
    if plural in _EXT_GROUPS:
        return _EXT_GROUPS[plural]
    if group_or_ext.startswith("."):
        return {group_or_ext.lower()}
    return set()


# Roots commonly referenced by name in queries. 'D drive' / 'desktop' /
# 'documents' resolve via this map before falling through to the user
# folder shortcuts.
def _resolve_root(name: str) -> str | None:
    n = name.lower().strip()
    if re.match(r"^[a-z]\s*drive$", n):
        return n[0].upper() + ":\\"
    if re.match(r"^[a-z]:\\?$", n):
        return n[0].upper() + ":\\"
    home = os.path.expanduser("~")
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or ""
    folder_map = {
        "desktop": [
            os.path.join(onedrive, "Desktop") if onedrive else "",
            os.path.join(home, "Desktop"),
        ],
        "documents": [
            os.path.join(onedrive, "Documents") if onedrive else "",
            os.path.join(home, "Documents"),
        ],
        "downloads": [
            os.path.join(onedrive, "Downloads") if onedrive else "",
            os.path.join(home, "Downloads"),
        ],
        "pictures": [
            os.path.join(onedrive, "Pictures") if onedrive else "",
            os.path.join(home, "Pictures"),
        ],
    }
    if n in folder_map:
        for cand in folder_map[n]:
            if cand and os.path.isdir(cand):
                return cand
    return None


class FileSearchPlaybook(Playbook):
    name = "file_search"
    description = "Count / list files matching name + extension across a folder."
    triggers = [
        # 'kitne <kind> <root> me hain' / 'D drive me kitne pdf'
        (
            re.compile(
                r"\bkitne\s+(?P<keyword>[\w \-]+?\s+)?"
                r"(?P<kind>documents?|docs?|files?|pdfs?|images?|photos?|"
                r"pictures?|videos?|music|audio|spreadsheets?|sheets?|code|scripts?)\b"
                r"\s+(?P<root>[\w \-:\\]+?)?\s*(me|main|in|hain|hai)?$",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'find X files in Y' / 'list X documents'
        (
            re.compile(
                r"\b(find|list|show|search)\s+(?P<keyword>[\w \-]+?\s+)?"
                r"(?P<kind>documents?|docs?|files?|pdfs?|images?|photos?|"
                r"videos?|music|audio|spreadsheets?|code|scripts?)\b"
                r"(?:\s+(in|on|under|inside)\s+(?P<root>[\w \-:\\]+))?",
                re.IGNORECASE,
            ),
            1.0,
        ),
        # 'how many X in Y'
        (
            re.compile(
                r"\bhow\s+many\s+(?P<keyword>[\w \-]+?\s+)?"
                r"(?P<kind>documents?|docs?|files?|pdfs?|images?|photos?|"
                r"videos?)\b\s*(in|on|under)?\s*(?P<root>[\w \-:\\]+)?",
                re.IGNORECASE,
            ),
            1.0,
        ),
    ]
    keywords: list[str] = []

    # Hard caps to keep the playbook snappy. Beyond these we surface a
    # "too many — narrow your search" message.
    _MAX_DEPTH = 6
    _MAX_FILES = 5000
    _MAX_SECONDS = 8.0
    # Folder names we skip outright — huge, noisy, never what the user means.
    _SKIP_DIRS = {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "windows",
        "$recycle.bin",
        "system volume information",
        "programdata",
        "program files",
        "program files (x86)",
        ".cache",
        ".npm",
    }

    def execute(self, context: PlaybookContext) -> str:
        kind = (context.captures.get("kind") or "").strip().lower()
        root_name = (context.captures.get("root") or "").strip()
        keyword = (context.captures.get("keyword") or "").strip().lower()

        exts = _expand_extensions(kind)
        if not exts:
            return f"_Don't know what `{kind}` means — try 'pdfs', 'documents', 'images', etc._"

        # Resolve the search root. If user didn't name one, walk Desktop +
        # Documents + Downloads (high-signal folders). Drive root searches
        # are explicit per query.
        roots: list[str] = []
        if root_name:
            resolved = _resolve_root(root_name)
            if resolved:
                roots.append(resolved)
            elif os.path.isdir(root_name):
                roots.append(os.path.abspath(root_name))
            else:
                return f"_Couldn't resolve `{root_name}` to a folder._"
        else:
            for default in ("desktop", "documents", "downloads"):
                r = _resolve_root(default)
                if r:
                    roots.append(r)

        if not roots:
            return "_No search roots available._"

        # Walk + filter. Keyword (if any) becomes a case-insensitive
        # substring on the filename stem.
        matches = self._walk(roots, exts, keyword)
        if not matches:
            kw_part = f" containing `{keyword}`" if keyword else ""
            return (
                f"**0 {kind}{kw_part}** found in {', '.join(roots)} "
                f"(searched {self._MAX_DEPTH} levels deep, {self._MAX_SECONDS:.0f}s cap)."
            )

        # Build the table. Top 15 by name; rest collapsed into a "+N more"
        # row so the response stays scannable even on huge result sets.
        rows = [
            {
                "Name": os.path.basename(p),
                "Size": self._fmt_size(self._safe_size(p)),
                "Folder": self._shrink_path(os.path.dirname(p)),
            }
            for p in matches[:15]
        ]
        kw_part = f" containing `{keyword}`" if keyword else ""
        header = f"**{len(matches)} {kind}{kw_part}** in {', '.join(roots)}:"
        body = render_table(rows)
        more = ""
        if len(matches) > 15:
            more = f"\n\n_… (+{len(matches) - 15} more)_"
        return f"{header}\n\n{body}{more}"

    def _walk(self, roots: list[str], exts: set[str], keyword: str) -> list[str]:
        out: list[str] = []
        deadline = time.monotonic() + self._MAX_SECONDS

        def _walk_one(root: str) -> None:
            for current, dirs, files in os.walk(root):
                if time.monotonic() >= deadline:
                    return
                if len(out) >= self._MAX_FILES:
                    return
                # Depth cap relative to this root
                rel = os.path.relpath(current, root)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if depth > self._MAX_DEPTH:
                    dirs[:] = []
                    continue
                # Skip noisy dirs
                dirs[:] = [d for d in dirs if d.lower() not in self._SKIP_DIRS]
                for fname in files:
                    name_l = fname.lower()
                    if not any(name_l.endswith(e) for e in exts):
                        continue
                    if keyword and keyword not in os.path.splitext(name_l)[0]:
                        continue
                    out.append(os.path.join(current, fname))
                    if len(out) >= self._MAX_FILES:
                        return

        for r in roots:
            try:
                _walk_one(r)
            except (PermissionError, OSError):
                continue
            if time.monotonic() >= deadline:
                break

        out.sort(key=lambda p: os.path.basename(p).lower())
        return out

    @staticmethod
    def _safe_size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    @staticmethod
    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n /= 1024
        return f"{n:.0f} TB"

    @staticmethod
    def _shrink_path(p: str, max_len: int = 40) -> str:
        if len(p) <= max_len:
            return p
        return "…" + p[-(max_len - 1) :]

    # Suppress matches that look like 'open X' / 'close X' — those are
    # handled by other playbooks, and our regex might accidentally
    # capture the verb. The agent picks the highest-confidence playbook,
    # but for defensive depth we override match().
    def match(self, query):
        # Bail if the query is clearly a different intent.
        q_lower = query.lower()
        for verb in ("close ", "kill ", "open ", "launch ", "khol ", "band kar"):
            if q_lower.startswith(verb):
                return None
        # Avoid double-firing with open_app on 'open X.pdf' — that
        # one's a file open, not a count/list query.
        return super().match(query)
