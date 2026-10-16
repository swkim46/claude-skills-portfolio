#!/usr/bin/env python3
"""Skill integrity checker — deterministic guard for skill edits.

Catches the failure modes that prose rules and a RANDOMIZED inline gate miss.
Every check is mechanical (no model judgement), so it cannot be rationalized past.

WHY THIS EXISTS (each check = a real incident, 2026-08-21):
  R-NAME  folder name != frontmatter `name`  -> skill silently fails to load
  R-DUP   two skills share a name            -> ambiguous invocation
  R-RUBRIC a SKILL.md calls `SG form/judge <name> <cp>` but rubrics/<name>.json
           lacks that cp                     -> gate dies on rename (rubrics are
           keyed by SKILL NAME, so renaming orphans them)
  R-ORPHAN a rubric exists for no skill      -> leftover from a rename
  R-PATH  an absolute path cited in SKILL.md does not exist -> dead helper
           reference (e.g. after moving skills out of ~/.claude/skills)
  R-YAML  frontmatter is not valid YAML       -> skill vanishes from the tool list
           with NO error shown anywhere. Chief cause (2026-09-09, 5 skills at once):
           an UNQUOTED description containing ": " — YAML reads colon+space as a
           nested mapping and the whole block fails. gate-audit and fact-check had
           been silently unloadable; earlier attempts fixed their LOCATION, which
           was never the problem. Fix: use a block scalar (`description: >-`).

SEALED-RUBRIC SAFETY: this script only reports whether a checkpoint KEY exists.
It never prints observations, criteria, or attest values, so running it does NOT
count as reading the sealed answer key.

Usage:
  python3 .claude/skills/_shared/check_skill_integrity.py [--root <repo>] [--quiet]
Exit code 0 = clean, 1 = violations found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
RUBRIC_DIR = ".claude/skills/_stepgate/rubrics"
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
# `SG form <skill> <cp>` / `SG judge <skill> <cp>` / `stepgate.py form <skill> <cp>`
GATE_RE = re.compile(r"(?:SG|stepgate\.py)\s+(?:form|judge)\s+([a-z0-9][a-z0-9-]*)\s+([a-z0-9_-]+)")
ABSPATH_RE = re.compile(r"/Users/[A-Za-z0-9_.-]+/[^\s`'\"()<>,;]*")
# Placeholder-bearing paths are templates, not real references.
PLACEHOLDER = re.compile(r"[<>{}*]|\.\.\.|…")
# Frontmatter must open at byte 0 and close before the body.
FM_RE = re.compile(r"\A---\n(.*?)\n---\s*?\n", re.S)
# The two silent killers inside an unquoted YAML scalar.
PLAIN_COLON = re.compile(r":[ \t]")
PLAIN_HASH = re.compile(r"[ \t]#")
YAML_INDICATORS = tuple("-?,[]{}&*!%@`")


def find_skills(root: Path) -> list[tuple[str, Path]]:
    """Every SKILL.md under any .claude/skills/ in the repo (archives excluded)."""
    out = []
    for skill_md in root.glob("**/.claude/skills/*/SKILL.md"):
        if "_archive" in skill_md.parts:
            continue
        out.append((skill_md.parent.name, skill_md))
    return sorted(out)


def frontmatter_name(text: str) -> str | None:
    m = NAME_RE.search(text)
    return m.group(1).strip().strip("\r") if m else None


def frontmatter_problems(text: str) -> list[str]:
    """Report why the frontmatter would fail to parse as YAML.

    No YAML library is used on purpose: this repo's python has none, and the failure
    modes worth catching are narrow and mechanical. We check the two ways a *plain*
    (unquoted) scalar silently swallows a skill:
      · it contains ": "  -> parsed as a nested mapping; the block dies
      · it contains " #"  -> the rest of the line becomes a comment; text truncates
    A block scalar (>- or |) or a quoted string is immune to both, which is why the
    skills that load correctly all use `>-`.
    """
    m = FM_RE.match(text)
    if not m:
        return ["frontmatter block missing or malformed (must open AND close with ---)"]
    problems: list[str] = []
    keys: list[str] = []
    for line in m.group(1).split("\n"):
        if not line.strip() or line[0] in " \t" or line.lstrip().startswith("#"):
            continue                      # blank, block-scalar continuation, or comment
        km = re.match(r"([A-Za-z0-9_-]+):(.*)$", line)
        if not km:
            problems.append(f"unparseable line -> {line[:70]!r}")
            continue
        key, val = km.group(1), km.group(2).strip()
        keys.append(key)
        if not val or val[0] in "|>\"'":
            continue                      # block scalar or quoted — safe
        if (hit := PLAIN_COLON.search(val)) is not None:
            problems.append(
                f"'{key}' is an unquoted scalar containing ': ' near col "
                f"{len(key) + 2 + hit.start()} — YAML reads that as a nested mapping and "
                f"the whole frontmatter fails, so the skill silently disappears. "
                f"Fix: '{key}: >-' with the text indented on the following lines."
            )
        elif (hit := PLAIN_HASH.search(val)) is not None:
            problems.append(
                f"'{key}' is an unquoted scalar containing ' #' near col "
                f"{len(key) + 2 + hit.start()} — everything after it becomes a YAML "
                f"comment and is silently dropped. Fix: use a block scalar '{key}: >-'."
            )
        elif val[0] in YAML_INDICATORS:
            problems.append(f"'{key}' plain scalar starts with YAML indicator {val[0]!r}")
    for required in ("name", "description"):
        if required not in keys:
            problems.append(f"missing required frontmatter key '{required}'")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic skill integrity checks.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--quiet", action="store_true", help="Print only violations.")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    skills = find_skills(root)
    violations: list[str] = []
    seen: dict[str, str] = {}
    seen_real: dict[str, Path] = {}

    # Cache rubric checkpoint KEYS only (never their contents).
    rubric_cps: dict[str, set[str]] = {}
    rubric_path = root / RUBRIC_DIR
    if rubric_path.is_dir():
        for rf in rubric_path.glob("*.json"):
            try:
                rubric_cps[rf.stem] = set(json.loads(rf.read_text(encoding="utf-8")).keys())
            except Exception as exc:
                violations.append(f"R-RUBRIC  {rf.name}: unparseable ({type(exc).__name__})")

    gate_users: set[str] = set()

    for folder, path in skills:
        rel = path.relative_to(root).parent
        text = path.read_text(encoding="utf-8", errors="replace")

        # R-YAML: frontmatter must actually parse, or the skill vanishes with no error.
        # Checked first — the other rules read fields that are meaningless if this fails.
        for problem in frontmatter_problems(text):
            violations.append(f"R-YAML    {rel}: {problem}")

        # R-NAME: folder name is the invocation name; frontmatter must agree.
        fm = frontmatter_name(text)
        if fm != folder:
            violations.append(f"R-NAME    {rel}: folder '{folder}' != frontmatter '{fm}'")

        # R-DUP: names must be unique across every scope.
        # A symlink that re-exposes a global skill inside a nested repo (so that scope
        # can find it) resolves to the SAME file — that is one skill reached twice,
        # not an ambiguous name. Compare resolved paths before flagging.
        if folder in seen:
            if path.resolve() != seen_real[folder]:
                violations.append(f"R-DUP     '{folder}' defined twice: {seen[folder]} and {rel}")
        else:
            seen[folder] = str(rel)
            seen_real[folder] = path.resolve()

        # R-RUBRIC: every gate this skill invokes must have a matching checkpoint.
        for skill_name, cp in set(GATE_RE.findall(text)):
            gate_users.add(skill_name)
            if skill_name not in rubric_cps:
                violations.append(
                    f"R-RUBRIC  {rel}: calls gate '{skill_name}:{cp}' but "
                    f"{RUBRIC_DIR}/{skill_name}.json is MISSING (rename orphaned it?)"
                )
            elif cp not in rubric_cps[skill_name]:
                violations.append(
                    f"R-RUBRIC  {rel}: calls checkpoint '{skill_name}:{cp}' "
                    f"but that checkpoint is not defined in {skill_name}.json"
                )

        # R-PATH: absolute paths cited in the body must resolve.
        for raw in set(ABSPATH_RE.findall(text)):
            cited = raw.rstrip(".,;:)`")
            if PLACEHOLDER.search(cited):
                continue
            if not Path(cited).exists():
                violations.append(f"R-PATH    {rel}: dead absolute path -> {cited}")

    # R-ORPHAN: a rubric whose skill no longer exists under that name.
    for name in sorted(rubric_cps):
        if name not in seen and name not in gate_users:
            violations.append(
                f"R-ORPHAN  {RUBRIC_DIR}/{name}.json has no skill named '{name}' "
                f"(renamed or deleted — retarget or remove)"
            )

    if not args.quiet:
        print(f"scanned {len(skills)} skills under {root}")
        for folder, path in skills:
            print(f"  · {folder:<28} {path.relative_to(root).parent}")
        print()

    if violations:
        print(f"VIOLATIONS ({len(violations)}):")
        for v in violations:
            print(f"  {v}")
        return 1

    print("SKILL INTEGRITY OK — frontmatter parses, names and uniqueness hold, "
          "gate rubrics and cited paths all resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
