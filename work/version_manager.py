#!/usr/bin/env python3
"""
Save / restore / compare named versions of the signal stack.

Usage:
  python version_manager.py save best
  python version_manager.py restore best
  python version_manager.py list
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parent
VERSIONS_DIR = WORK_DIR / "versions"

TRACKED = (
    "signal_engine.py",
    "news_sentiment.py",
    "trade_learning.py",
    "signal_hybrid.py",
    "backtest_yesterday.py",
    "trade_cycle.py",
)


def _version_dir(name: str) -> Path:
    return VERSIONS_DIR / name


def save(name: str, note: str = "") -> None:
    dest = _version_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"saved_at": datetime.now(timezone.utc).isoformat(), "note": note, "files": []}
    for rel in TRACKED:
        src = WORK_DIR / rel
        if not src.exists():
            continue
        shutil.copy2(src, dest / rel)
        manifest["files"].append(rel)
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Saved version '{name}' ({len(manifest['files'])} files) → {dest}")


def restore(name: str) -> None:
    src = _version_dir(name)
    if not src.exists():
        raise SystemExit(f"Version '{name}' not found at {src}")
    for rel in TRACKED:
        f = src / rel
        if f.exists():
            shutil.copy2(f, WORK_DIR / rel)
            print(f"  restored {rel}")
    print(f"Restored version '{name}'")


def list_versions() -> None:
    if not VERSIONS_DIR.exists():
        print("No versions saved.")
        return
    for p in sorted(VERSIONS_DIR.iterdir()):
        if p.is_dir():
            m = p / "manifest.json"
            note = ""
            if m.exists():
                note = json.loads(m.read_text()).get("note", "")
            print(f"  {p.name}  {note}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "save":
        save(sys.argv[2] if len(sys.argv) > 2 else "best", " ".join(sys.argv[3:]))
    elif cmd == "restore":
        restore(sys.argv[2] if len(sys.argv) > 2 else "best")
    elif cmd == "list":
        list_versions()
    else:
        print(f"Unknown command: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
