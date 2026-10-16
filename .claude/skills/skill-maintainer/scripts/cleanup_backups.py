# -*- coding: utf-8 -*-
"""스킬 백업 파일 청소 — reorg/편집 중 만든 `_*_backup_*` 검증 스크래치를 정리하되, 복원점은 남긴다.

skill-maintainer §1 백업(`_<skill>_SKILL_backup_YYMMDD.md` 등)은 '복원/대조용 스크래치'다.
산출물 버전(보존 대상)과 달리 영구 보존은 아니지만, **스킬별 가장 최신 백업 1개는
직전 상태 복원점이므로 유지**하고, 그보다 오래된 중간 단계만 청소한다(기본 동작).

그룹핑(=스킬 lineage): 파일명에서 '_backup_' 앞부분이 같으면 한 스킬의 백업 계보로 본다.
  _research-organizer_SKILL_backup_260615.md, ..._260616.md, ..._260616_d.md
  → 그룹 '_research-organizer_skill' → mtime 최신 1개 keep, 나머지 delete.

★ 스캔 대상(경로를 박아두지 않고 이 파일 위치에서 리포 루트를 역산한다):
  - 리포 안의 **모든** `.claude/skills` (재귀) — 스킬은 폴더 스코프라 `life/`·`work/consulting/` 등
    여러 곳에 흩어져 있다. 한 곳만 보면 나머지 백업이 영영 안 잡힌다
  - 리포 루트 직속 파일(과거에 cwd로 흘린 것)
  - 사용자 전역 `~/.claude/skills` (있으면)

기본은 DRY-RUN(삭제 후보만 출력). 실제 삭제는 반드시 --apply.
  python cleanup_backups.py                 # 그룹별 최신 1개 보존, 나머지 후보 출력
  python cleanup_backups.py --apply         # 실제 삭제(그룹별 최신은 유지)
  python cleanup_backups.py --keep-days 1   # 추가로 최근 N일 이내 백업도 보존
  python cleanup_backups.py --purge --apply # ★ 최신본까지 전부 삭제(예외 — 위험)
  python cleanup_backups.py <경로> [<경로>] # 특정 폴더/파일만(폴더는 재귀)

매칭: 파일명에 '_backup_' 포함(.md/.py/그 외 확장자 무관).
보존: 경로에 '_archive'가 있으면 제외(standalone화 시 의도적 이관본 — §4).
"""
import sys, os, time

try:  # PowerShell 콘솔 cp949 mojibake 방지 — 출력 UTF-8 강제
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PRUNE = {".git", "node_modules", "__pycache__", ".venv", "venv"}  # 재귀 시 건너뛸 무거운 디렉터리

# 이 스크립트 위치에서 리포 루트를 역산한다 — 경로를 박아두면 OS·리포가 바뀔 때 조용히 0건이 된다.
#   <REPO>/.claude/skills/skill-maintainer/scripts/cleanup_backups.py  → 5단계 상위가 REPO
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "..", "..", ".."))
# 사용자 전역 스킬 폴더(있을 수도, 없을 수도)
HOME_SKILLS = os.path.join(os.path.expanduser("~"), ".claude", "skills")


def default_targets():
    """스캔 대상 = 리포 안의 모든 `.claude/skills` (재귀) + 리포 루트 직속 + 전역 스킬 폴더.

    스킬은 폴더 스코프라 `.claude/skills/`·`.claude/skills/`처럼
    여러 곳에 흩어져 있다. 한 곳만 박아두면 나머지 백업이 영영 안 잡힌다.
    """
    targets, seen = [], set()

    def add(path, recursive):
        rp = os.path.realpath(path)
        if rp not in seen and os.path.isdir(rp):
            seen.add(rp)
            targets.append((rp, recursive))

    for dirpath, dirs, _files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d.lower() not in PRUNE and "_archive" not in d.lower()]
        if os.path.basename(dirpath) == "skills" and \
           os.path.basename(os.path.dirname(dirpath)) == ".claude":
            add(dirpath, True)
            dirs[:] = []                      # 이미 재귀로 잡으므로 더 내려갈 필요 없음
    add(REPO_ROOT, False)                     # 루트에 흘린 것(과거 관행)
    add(HOME_SKILLS, True)
    return targets


def is_backup(name):
    n = name.lower()
    return "_backup_" in n and "_archive" not in n


def lineage_key(path):
    """한 스킬의 백업 계보 키 = 파일명에서 '_backup_' 앞부분(소문자)."""
    return os.path.basename(path).lower().split("_backup_", 1)[0]


def scan(path, recursive):
    """recursive=True 면 하위 전부, False 면 그 폴더의 직속 파일만(cwd 루트용)."""
    hits = []
    if os.path.isfile(path):
        if is_backup(os.path.basename(path)):
            hits.append(path)
        return hits
    if not os.path.isdir(path):
        return hits
    if recursive:
        for dirpath, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d.lower() not in PRUNE and "_archive" not in d.lower()]
            if "_archive" in dirpath.lower():
                continue
            for f in files:
                if is_backup(f):
                    hits.append(os.path.join(dirpath, f))
    else:
        for f in os.listdir(path):
            fp = os.path.join(path, f)
            if os.path.isfile(fp) and is_backup(f):
                hits.append(fp)
    return hits


def main():
    argv = sys.argv[1:]
    apply = "--apply" in argv
    purge = "--purge" in argv  # 최신본까지 전부 삭제(예외)
    argv = [a for a in argv if a not in ("--apply", "--purge")]

    keep_days = 0
    if "--keep-days" in argv:
        i = argv.index("--keep-days")
        try:
            keep_days = int(argv[i + 1])
        except (IndexError, ValueError):
            print("usage: --keep-days <N>")
            sys.exit(2)
        del argv[i:i + 2]

    if argv:  # 사용자가 명시한 경로: 폴더는 재귀, 파일은 그대로
        targets = [(p, True) for p in argv]
        for p, _r in targets:
            if not os.path.exists(p):
                print("경로 없음: %s" % p)
                sys.exit(2)
    else:  # 기본: 리포 안의 모든 .claude/skills(재귀) + 리포 루트 직속 + 전역 스킬 폴더
        targets = default_targets()

    cands = []
    for path, recursive in targets:
        cands.extend(scan(path, recursive))
    cands = sorted(set(cands))

    now = time.time()

    # 그룹별 최신(mtime 최대) 1개 = 복원점 → 기본 보존(--purge면 미보존)
    latest_in_group = {}
    if not purge:
        for p in cands:
            k = lineage_key(p)
            m = os.path.getmtime(p)
            if k not in latest_in_group or m > latest_in_group[k][1]:
                latest_in_group[k] = (p, m)
    keep_paths = {v[0] for v in latest_in_group.values()}

    to_delete, kept = [], []
    for p in cands:
        age = (now - os.path.getmtime(p)) / 86400.0
        if p in keep_paths:
            kept.append((p, age, "최신 복원점"))
        elif keep_days and age < keep_days:
            kept.append((p, age, "최근 %d일 이내" % keep_days))
        else:
            to_delete.append((p, age))

    scanned = "  ".join("%s%s" % (p, "" if r else " (루트만)") for p, r in targets)
    if not cands:
        print("백업 파일 없음 — 청소할 것이 없습니다. (스캔: %s)" % scanned)
        return

    mode = "[APPLY 삭제]" if apply else "[DRY-RUN 미리보기 — 삭제 안 함]"
    if purge:
        mode += " [PURGE — 최신본까지 전부]"
    print("== 스킬 백업 청소 %s ==" % mode)
    print("스캔: %s" % scanned)

    if kept:
        print("\n보존 %d개:" % len(kept))
        for p, age, why in kept:
            print("  keep  (%.1fd, %s) %s" % (age, why, p))

    print("\n삭제 대상 %d개:" % len(to_delete))
    deleted = 0
    for p, age in to_delete:
        if apply:
            try:
                os.remove(p)
                deleted += 1
                print("  DEL   (%.1fd) %s" % (age, p))
            except OSError as e:
                print("  FAIL  %s  (%s)" % (p, e))
        else:
            print("  would (%.1fd) %s" % (age, p))

    if apply:
        print("\n삭제 완료: %d개 (보존 %d개)." % (deleted, len(kept)))
    else:
        print("\n실제 삭제하려면 --apply 를 붙여 다시 실행하세요. (그룹별 최신 1개는 자동 보존, 전부 지우려면 --purge)")


if __name__ == "__main__":
    main()
