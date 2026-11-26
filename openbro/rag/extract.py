"""Text extraction for indexing — page-aware where the format has pages.

PDF and Word are read here directly (pages / heading styles kept); every
other format goes through `DocumentTool`, which already covers
spreadsheets, CSV, HTML, JSON/YAML, images (OCR) and plain text/code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_EXTS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".ini",
    ".toml",
    ".cfg",
    ".xml",
    ".sql",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".ps1",
    ".bat",
    ".swift",
    ".dart",
}
TABLE_EXTS = {".xlsx", ".xls", ".csv", ".tsv"}
DOC_EXTS = {".pdf", ".docx", ".html", ".htm", ".json", ".yaml", ".yml"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def _ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return False
    return True


def indexable_extensions() -> set[str]:
    exts = TEXT_EXTS | TABLE_EXTS | DOC_EXTS
    if _ocr_available():
        exts |= IMAGE_EXTS
    return exts


@dataclass
class Extracted:
    pages: list[tuple[int | None, str]]  # (page number or None, text)
    kind: str = "text"  # 'text' | 'table'
    title: str = ""

    @property
    def total_chars(self) -> int:
        return sum(len(t) for _, t in self.pages)


def _pdf(path: Path) -> Extracted | None:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return None
    reader = PdfReader(str(path))
    pages = []
    for n, page in enumerate(reader.pages, start=1):
        try:
            pages.append((n, page.extract_text() or ""))
        except Exception:
            pages.append((n, ""))
    if not any(t.strip() for _, t in pages):
        return None  # scanned — let DocumentTool try OCR
    return Extracted(pages=pages)


def _docx(path: Path) -> Extracted | None:
    try:
        import docx  # type: ignore[import-not-found]
    except ImportError:
        return None
    d = docx.Document(str(path))
    lines = []
    for p in d.paragraphs:
        text = p.text.strip()
        if not text:
            lines.append("")
            continue
        style = (p.style.name if p.style is not None else "") or ""
        if style.lower().startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            lines.append(f"{'#' * min(int(level), 6)} {text}")
        else:
            lines.append(text)
    return Extracted(pages=[(None, "\n".join(lines))])


def extract(path: Path) -> Extracted:
    """Raise ValueError with a readable reason when nothing can be read."""
    ext = path.suffix.lower()
    result: Extracted | None = None
    if ext == ".pdf":
        try:
            import pypdf  # noqa: F401  # type: ignore[import-not-found]
        except ImportError as e:
            raise ValueError("pypdf not installed (pip install 'openbro[rag]')") from e
        result = _pdf(path)
        if result is None:  # no text layer — scanned
            from openbro.tools.document_tool import DocumentTool

            ocr = DocumentTool()._ocr_pdf(path)
            if not ocr or not ocr.strip() or ocr.startswith("PDF→image conversion failed"):
                raise ValueError("scanned PDF with no text layer, and OCR is not available")
            result = Extracted(pages=[(None, ocr)])
    elif ext == ".docx":
        result = _docx(path)
    elif ext in TEXT_EXTS:
        result = Extracted(pages=[(None, path.read_text(encoding="utf-8", errors="replace"))])
    if result is None:
        from openbro.tools.document_tool import DocumentTool

        text = DocumentTool().run(action="read", file=str(path), limit=10**9)
        head = text[:300].lower()
        bad = (
            text.startswith("(no text extracted")
            or text.startswith("Read failed")
            or text.startswith("File not found")
            or "pip install" in head
            or "returned nothing" in head
        )
        if bad:
            raise ValueError(text.strip().splitlines()[0][:200])
        result = Extracted(pages=[(None, text)], kind="table" if ext in TABLE_EXTS else "text")
    if not result.total_chars or not any(t.strip() for _, t in result.pages):
        raise ValueError("no text found")
    result.title = path.stem
    return result
