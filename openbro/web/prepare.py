"""Build the demo's document index ahead of time (used by the Dockerfile).

    python -m openbro.web.prepare --data-dir DIR PATH [PATH ...]

Downloads the embedding model (when `openbro[rag]` is installed) and
indexes the given files/folders into DIR, so `openbro web` with the same
`--data-dir` starts without re-indexing (unchanged files are skipped).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openbro.web.prepare")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    from openbro.web.demo import build_demo_docs_service

    missing = [p for p in args.paths if not p.exists()]
    if missing:
        parser.error(f"not found: {', '.join(map(str, missing))}")
    svc = build_demo_docs_service(args.data_dir, args.paths)
    status = svc.status()
    print(f"Indexed {status['documents']} documents — {svc.mode()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
