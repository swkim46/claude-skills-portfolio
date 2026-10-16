#!/usr/bin/env python3
"""Knowledge-map scanner (macOS port of scan_folders.ps1).

Reports the CURRENT deliverable state of every project folder under a root so the
map (_지식지도_KNOWLEDGE_MAP.md) can be reconciled by DELTA — without guessing and
without regenerating the curated narrative. READ ONLY; never edits the map.

Shared engine: both knowledge-map variants (life / consulting) call this by
absolute path. Do not fork it — extend it.

Usage:
  python3 .claude/skills/_shared/scan_folders.py --root <path> [--json]

Per folder it prints: newest deliverable (by version token, then mtime),
deliverable count, whether CHANGELOG.md exists, and any HANDOFF files.
Claude then opens the relevant CHANGELOG / HANDOFF / master .md to refresh the
"핵심"·"이어받기"·status cells (curated, not derivable from a file list).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

DELIVERABLE_EXT = {".md", ".xlsx", ".docx", ".pptx", ".pdf", ".html"}
SKIP_EXACT = {".claude", ".git", "node_modules", "__pycache__", "_archive"}
VERSION_RE = re.compile(r"v(\d+)(?:[_.](\d+))?")
HANDOFF_RE = re.compile(r"handoff|핸드오프", re.IGNORECASE)
CATEGORY_RE = re.compile(r"^\d+_")


def version_key(name: str) -> int:
    """Last v<major>[_.<minor>] token -> major*1000+minor; -1 if none."""
    matches = VERSION_RE.findall(name)
    if not matches:
        return -1
    major, minor = matches[-1]
    return int(major) * 1000 + (int(minor) if minor else 0)


def is_skipped(name: str) -> bool:
    return name.startswith("_") or name.startswith(".") or name in SKIP_EXACT


def scan_workstream(folder: Path) -> dict:
    """Collect deliverable state for one workstream folder."""
    deliverables, handoffs = [], []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        # _raw_sources and archives hold inputs/scratch, not deliverables.
        if any(part.startswith("_") or part.startswith(".") for part in path.relative_to(folder).parts[:-1]):
            continue
        if HANDOFF_RE.search(path.name):
            handoffs.append(path.name)
        if path.suffix.lower() in DELIVERABLE_EXT and path.name.upper() != "CHANGELOG.MD":
            deliverables.append(path)

    newest = None
    if deliverables:
        newest = max(deliverables, key=lambda p: (version_key(p.name), p.stat().st_mtime))

    return {
        "folder": folder.name,
        "newest": newest.name if newest else None,
        "newest_version": version_key(newest.name) if newest else -1,
        "count": len(deliverables),
        "changelog": (folder / "CHANGELOG.md").exists(),
        "handoffs": sorted(set(handoffs)),
    }


def collect(root: Path) -> list[dict]:
    """Walk top-level dirs. A dir named like 'N_...' is a CATEGORY parent whose
    children are the workstreams (2-level); otherwise it is a workstream itself."""
    rows = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or is_skipped(entry.name):
            continue
        if CATEGORY_RE.match(entry.name):
            for child in sorted(entry.iterdir()):
                if child.is_dir() and not is_skipped(child.name):
                    row = scan_workstream(child)
                    row["category"] = entry.name
                    rows.append(row)
        else:
            row = scan_workstream(entry)
            row["category"] = None
            rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Knowledge-map folder scanner (read-only).")
    ap.add_argument("--root", required=True, help="Repo/workspace root to scan.")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"ROOT NOT FOUND: {root}", file=sys.stderr)
        return 1

    rows = collect(root)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"SCAN ROOT: {root}   ({len(rows)} workstreams)")
    print("-" * 96)
    for r in rows:
        head = f"[{r['category']}] {r['folder']}" if r["category"] else r["folder"]
        print(f"{head}")
        print(f"    newest      : {r['newest'] or '(no deliverable — 착수 전)'}")
        print(f"    deliverables: {r['count']}    CHANGELOG: {'Y' if r['changelog'] else 'N'}")
        if r["handoffs"]:
            print(f"    handoff     : {', '.join(r['handoffs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
