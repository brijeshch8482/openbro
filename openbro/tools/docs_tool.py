"""`docs` tool — search the user's indexed documents (harness piece 5).

Raw data only: ranked passages with file, page and heading. If the first
search misses, the model searches again with other words or reads the
whole document.
"""

from __future__ import annotations

from openbro.tools.base import BaseTool, RiskLevel


class DocsTool(BaseTool):
    name = "docs"
    description = (
        "Search the user's OWN documents that were added to OpenBro's index "
        "(PDFs, Word, Excel, notes, scans). Hybrid search: exact words "
        "(names, invoice numbers, Hinglish) + meaning. Returns passages with "
        "file path, page and heading — cite them in the answer.\n"
        "Actions: search (query, k?, path_filter?, type_filter? e.g. 'pdf,docx'), "
        "read (doc = id or path, page?) for the full text, add (path) to index "
        "a file or folder in place, list (what is indexed), remove (path).\n"
        "If results don't answer the question, search again with different "
        "words before saying it isn't there. For a single file the user names "
        "that isn't indexed, use the `document` tool instead."
    )
    risk = RiskLevel.SAFE

    def __init__(self, service=None):
        self._service = service

    def _svc(self):
        if self._service is None:
            from openbro.rag import get_docs_service

            self._service = get_docs_service()
        return self._service

    def compute_risk(self, args: dict) -> RiskLevel:
        if str(args.get("action", "")).lower() in ("add", "remove"):
            return RiskLevel.MODERATE
        return RiskLevel.SAFE

    def run(
        self,
        action: str,
        query: str = "",
        k: int = 0,
        path_filter: str = "",
        type_filter: str = "",
        doc: str = "",
        page: int | None = None,
        path: str = "",
    ) -> str:
        action = (action or "").lower().strip()
        svc = self._svc()

        if action == "search":
            if not query.strip():
                return "search needs 'query'"
            hits = svc.search(
                query, k=int(k or 0) or None, path_filter=path_filter, type_filter=type_filter
            )
            if not hits:
                indexed = svc.status()["documents"]
                if not indexed:
                    return "Nothing is indexed yet. Ask the user which folder to add (docs add)."
                return f"No passages matched {query!r} in {indexed} indexed documents."
            lines = [f"{len(hits)} passages for {query!r} ({svc.mode()}):"]
            for i, h in enumerate(hits, start=1):
                where = h.path + (f", page {h.page}" if h.page else "")
                head = f" — {h.heading}" if h.heading else ""
                lines.append(f"\n[{i}] {where}{head}  (doc={h.doc_id}, score {h.score})")
                lines.append(h.text.strip())
            return "\n".join(lines)

        if action == "read":
            ref = doc or path
            if not ref:
                return "read needs 'doc' (id from search results) or 'path'"
            text = svc.read(ref, page=int(page) if page else None)
            return text if len(text) <= 30_000 else text[:30_000] + "\n…[truncated — read by page]"

        if action == "add":
            if not path:
                return "add needs 'path' (file or folder)"
            try:
                report = svc.add(path)
            except FileNotFoundError as e:
                return str(e)
            extra = ""
            if report.skipped:
                extra += "\nSkipped: " + "; ".join(report.skipped[:5])
            if report.errors:
                extra += "\nFailed: " + "; ".join(report.errors[:5])
            return f"Indexed {report.summary()}{extra}"

        if action == "list":
            sources = svc.sources()
            st = svc.status()
            lines = [
                f"{st['documents']} documents, {st['chunks']} passages — {st['mode']}",
            ]
            for s in sources:
                n = len(svc.store.list_documents(s.id))
                label = "uploads library" if s.kind == "library" else s.kind
                lines.append(f"- {s.path} ({label}, {n} docs, last scan {s.last_scan or 'never'})")
            return "\n".join(lines)

        if action == "remove":
            if not path:
                return "remove needs 'path'"
            return svc.remove(path)

        return f"Unknown action: {action}. Available: search, read, add, list, remove"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "read", "add", "list", "remove"],
                    },
                    "query": {"type": "string", "description": "What to look for (search)"},
                    "k": {"type": "integer", "description": "How many passages (default 5)"},
                    "path_filter": {
                        "type": "string",
                        "description": "Only paths containing this text (search)",
                    },
                    "type_filter": {
                        "type": "string",
                        "description": "File types, e.g. 'pdf,xlsx' (search)",
                    },
                    "doc": {"type": "string", "description": "Document id or path (read)"},
                    "page": {"type": "integer", "description": "Page number (read)"},
                    "path": {"type": "string", "description": "File or folder (add/remove)"},
                },
                "required": ["action"],
            },
        }
