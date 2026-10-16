#!/usr/bin/env python3
"""전역 스킬을 하위 스코프에서도 *검색되게* 심볼릭 링크로 노출한다.

왜 필요한가
-----------
Claude Code의 스킬 탐색은 **중첩 git 저장소 루트에서 멈춘다.** `claude/life/`처럼
자체 저장소인 하위 폴더에서 작업하면 상위 `claude/.claude/skills/`의 전역 스킬
(`skill-maintainer`·`gate-audit`·`writing-style`·`gemini-image`)이 **목록에서 조용히 사라진다.**
파일은 멀쩡하고 이름을 직접 타이핑하면 실행은 되므로 원인을 찾기 어렵다.

`permissions.additionalDirectories`(add-dir)는 **파일 접근 권한만** 준다 — 탐색 범위를
넓히지 않으므로 이 문제를 고치지 못한다(2026-09-04에 그렇게 조치했다가 2026-09-08에 재발 확인).

무엇을 하는가
-------------
대상 스코프의 `.claude/skills/` 안에 전역 스킬을 가리키는 **상대 심볼릭 링크**를 만든다.
복사가 아니라 링크이므로 원본은 `claude/.claude/skills/` 한 곳뿐이고, 원본을 고치면
그 즉시 모든 스코프에 반영된다(드리프트 0).

  python3 link_global_skills.py <스코프 경로>          # DRY-RUN
  python3 link_global_skills.py <스코프 경로> --apply
  python3 link_global_skills.py --list                 # 링크가 필요한 스코프 찾기

주의
----
- 링크는 저장소에 담지 말 것(클론하면 깨진다) → 대상 저장소의 `.gitignore`에 넣는다.
  `--apply`가 `.gitignore` 항목까지 같이 넣어준다.
- `check_skill_integrity.py`는 **realpath가 같으면 R-DUP로 보지 않는다**(같은 원본을 두 번
  만난 것이지 이름 충돌이 아니므로). 그 예외가 없으면 이 방식은 위반으로 잡힌다.
- 사용자 전역(`~/.claude/skills/`)에는 올리지 않는다 — 사용자가 명시적으로 원하지 않음.
"""
import argparse
import os
import unicodedata
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def n(p) -> str:
    """경로를 NFC로 정규화한 문자열. macOS 파일시스템은 한글을 NFD로 돌려주는데
    소스에 적힌 리터럴은 NFC라, 정규화 없이 relpath/비교를 하면 조용히 깨진다."""
    return unicodedata.normalize("NFC", str(p))


def rel(p, base=None) -> str:
    """base(기본 ROOT) 기준 상대경로 — NFC로 맞춘 뒤 계산."""
    try:
        return os.path.relpath(n(p), n(base if base is not None else ROOT))
    except Exception:
        return str(p)


GLOBAL_SKILLS_DIR = ROOT / ".claude" / "skills"
GITIGNORE_HEADER = (
    "\n# 상위 claude/.claude/skills 의 전역 스킬을 이 스코프에서도 검색되게 건 심볼릭 링크.\n"
    "# 원본은 claude/.claude/skills/ 한 곳뿐이므로 저장소에는 담지 않는다(클론 시 깨진 링크 방지).\n"
)


def global_skills():
    """전역 스킬 폴더명(지원 디렉터리 `_shared`·`_stepgate` 제외)."""
    return sorted(
        d.name for d in GLOBAL_SKILLS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_") and (d / "SKILL.md").is_file()
    )


def repo_root(path: Path):
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def find_cutoff_scopes():
    """탐색이 끊기는 스코프 = ROOT 아래의 중첩 저장소(ROOT 자신은 제외)."""
    scopes = []
    for gitdir in ROOT.glob("**/.git"):
        if "_archive" in gitdir.parts or "node_modules" in gitdir.parts:
            continue
        scope = gitdir.parent
        if scope != ROOT:
            scopes.append(scope)
    return sorted(set(scopes))


def link_scope(scope: Path, apply: bool) -> int:
    skills_dir = scope / ".claude" / "skills"
    names = global_skills()
    made = 0
    print(f"\n[{rel(scope)}]  전역 스킬 {len(names)}개")
    if not skills_dir.is_dir():
        print(f"  {'생성' if apply else 'would 생성'}: .claude/skills/")
        if apply:
            skills_dir.mkdir(parents=True, exist_ok=True)

    for name in names:
        dst = skills_dir / name
        if dst.exists() or dst.is_symlink():
            print(f"  skip  {name} (이미 있음)")
            continue
        link_target = rel(GLOBAL_SKILLS_DIR / name, skills_dir)
        if apply:
            dst.symlink_to(link_target)
            print(f"  link  {name} -> {link_target}")
        else:
            print(f"  would link  {name} -> {link_target}")
        made += 1

    root = repo_root(scope)
    if root and apply:
        gi = root / ".gitignore"
        prefix = rel(skills_dir, root)
        existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
        lines = [f"{prefix}/{n}" for n in names if f"{prefix}/{n}" not in existing]
        if lines:
            with gi.open("a", encoding="utf-8") as f:
                f.write(GITIGNORE_HEADER + "\n".join(lines) + "\n")
            print(f"  .gitignore  {len(lines)}줄 추가 ({rel(gi)})")
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scope", nargs="?", help="링크를 걸 스코프 경로")
    ap.add_argument("--apply", action="store_true", help="실제로 생성(기본은 DRY-RUN)")
    ap.add_argument("--list", action="store_true", help="탐색이 끊기는 스코프 목록만 출력")
    args = ap.parse_args()

    if args.list or not args.scope:
        print("전역 스킬:", ", ".join(global_skills()))
        print("\n탐색이 끊기는 스코프(중첩 저장소) — 여기서 작업하면 전역 스킬이 안 뜬다:")
        for s in find_cutoff_scopes():
            sd = s / ".claude" / "skills"
            linked = sd.is_dir() and any((sd / n).is_symlink() for n in global_skills())
            print(f"  {'✓ 링크됨' if linked else '✗ 미연결'}  {rel(s)}")
        if not args.scope:
            print("\n연결하려면: python3 link_global_skills.py <스코프> --apply")
        return 0

    scope = Path(args.scope).expanduser().resolve()
    if not n(scope).startswith(n(ROOT)):
        print(f"거부: {scope} 는 {ROOT} 아래가 아니다.", file=sys.stderr)
        return 2
    made = link_scope(scope, args.apply)
    if not args.apply and made:
        print("\n실제로 만들려면 --apply 를 붙여 다시 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
