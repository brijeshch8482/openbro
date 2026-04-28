"""Download tool - download files from URLs to user-specified folders."""

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from openbro.tools.base import BaseTool, RiskLevel


class DownloadTool(BaseTool):
    name = "download"
    description = (
        "Download a file from a URL to a specified folder. "
        "Supports custom destination paths (e.g. 'D:/Downloads', '~/Music')."
    )
    risk = RiskLevel.MODERATE

    def run(self, url: str, dest_folder: str = "", filename: str = "") -> str:
        if not url:
            return "URL required"

        if not url.startswith(("http://", "https://")):
            return f"Invalid URL: {url}. Must start with http:// or https://"

        # Resolve destination folder
        if dest_folder:
            dest_path = Path(dest_folder).expanduser().resolve()
        else:
            # Default to user's Downloads folder
            dest_path = Path.home() / "Downloads"

        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return f"Cannot create folder '{dest_path}': {e}"

        # Determine filename
        if not filename:
            filename = self._guess_filename(url)

        filename = self._sanitize_filename(filename)
        target = dest_path / filename

        # Avoid overwriting - add suffix if exists
        target = self._unique_path(target)

        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=300) as resp:
                resp.raise_for_status()

                # Try to get filename from Content-Disposition header
                if not filename or filename == "download":
                    cd = resp.headers.get("content-disposition", "")
                    match = re.search(r'filename="?([^"]+)"?', cd)
                    if match:
                        new_name = self._sanitize_filename(match.group(1))
                        target = self._unique_path(dest_path / new_name)

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0

                with open(target, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)

            size_mb = downloaded / (1024 * 1024)
            return f"Downloaded: {target.name}\n  Path: {target}\n  Size: {size_mb:.2f} MB" + (
                f" / {total / (1024 * 1024):.2f} MB" if total else ""
            )

        except httpx.HTTPStatusError as e:
            return f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
        except Exception as e:
            # Clean up partial file
            if target.exists():
                try:
                    target.unlink()
                except Exception:
                    pass
            return f"Download failed: {e}"

    def _guess_filename(self, url: str) -> str:
        parsed = urlparse(url)
        name = unquote(os.path.basename(parsed.path))
        if not name or "." not in name:
            return "download"
        return name

    def _sanitize_filename(self, name: str) -> str:
        # Remove characters illegal on Windows + Linux
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:255]

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        parent = path.parent

        for i in range(1, 1000):
            candidate = parent / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
        return path

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the file to download",
                    },
                    "dest_folder": {
                        "type": "string",
                        "description": (
                            "Destination folder path (e.g. 'D:/Downloads', "
                            "'~/Music', 'C:/Users/me/Documents'). "
                            "Defaults to user's Downloads folder."
                        ),
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "Optional custom filename. "
                            "If not given, derived from URL or Content-Disposition header."
                        ),
                    },
                },
                "required": ["url"],
            },
        }
