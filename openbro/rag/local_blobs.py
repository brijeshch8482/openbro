"""Local BlobStore: uploaded copies live in `<base_dir>/docs/library/`."""

from __future__ import annotations

import shutil
from pathlib import Path


class LibraryBlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, src: Path, name: str) -> Path:
        """Copy `src` into the library; never overwrites — adds ' (2)' etc."""
        target = self.root / Path(name).name
        stem, suffix, n = target.stem, target.suffix, 2
        while target.exists():
            target = self.root / f"{stem} ({n}){suffix}"
            n += 1
        shutil.copy2(src, target)
        return target

    def open(self, ref: Path) -> Path:
        return Path(ref)

    def delete(self, ref: Path) -> None:
        p = Path(ref)
        if p.is_file() and self.root.resolve() in p.resolve().parents:
            p.unlink()
