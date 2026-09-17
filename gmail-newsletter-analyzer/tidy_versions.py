#!/usr/bin/env python3
"""세상 공부 노트 버전 정리 — 날짜마다 최신 1개만 메인에 남긴다.

일반 산출물은 major를 메인에 누적하지만(`deliverable-versioning`), 이 노트는 **날짜가 계속
늘어나는** 일지다. 날짜마다 major를 쌓으면 메인 폴더가 금세 읽을 수 없게 된다.
그래서 여기서는 major/minor를 가리지 않고 **날짜당 최신 1개**만 남기고 전부 `이전버전/`으로 내린다.

    python3 tidy_versions.py           # DRY-RUN — 무엇이 내려갈지만 출력
    python3 tidy_versions.py --apply   # 실제 이동

다루는 형식은 둘이다. 그 밖의 파일은 손대지 않는다.

    세상공부_YYMMDD_노트_vN_M.md              ← 그날의 노트
    세상공부_YYMMDD_<종류>_<주제>_vN_M.md     ← 그날 노트에서 파생된 문서(추가리서치 등)

파생 문서는 **주제별로 따로** 최신 1개를 남긴다(노트와 섞어서 세지 않는다) — 같은 날 추가리서치가
둘이면 둘 다 메인에 남고, 각자의 옛 버전만 내려간다.

노트에 `노트`가 들어가는 건 정렬 때문이다. Finder는 한글을 라틴 문자보다 앞에 놓아서, 노트가
`..._vN_M.md`이면 파생 문서(`..._추가리서치_...`)가 노트 **위로** 올라간다. 둘 다 한글 단어를
쓰면 가나다순으로 `노트`(ㄴ) < `추가리서치`(ㅊ)가 되어 자연스럽게 노트가 먼저 온다.
"""
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.join(HERE, "세상공부")
ARCHIVE = os.path.join(NOTES, "이전버전")
# 그날의 노트
PAT = re.compile(r"^(세상공부_(\d{6}))_노트_v(\d+)_(\d+)\.md$")
# 그날 노트에서 파생된 문서 — 주제(slug)마다 독립 계보
EXTRA_PAT = re.compile(r"^세상공부_(\d{6})_(?!노트_v)(.+?)_v(\d+)_(\d+)\.md$")


def scan():
    """계보별로 {계보키: [(major, minor, 파일명)]}.

    계보키는 항상 튜플 `(날짜, 주제slug)` — 노트는 slug가 빈 문자열이라
    같은 날짜 안에서 노트가 파생 문서보다 먼저 온다(정렬 타입도 섞이지 않는다).
    """
    by_lineage = {}
    if not os.path.isdir(NOTES):
        raise SystemExit("노트 폴더 없음: %s" % NOTES)
    for name in os.listdir(NOTES):
        m = PAT.match(name)
        if m:
            by_lineage.setdefault((m.group(2), ""), []).append(
                (int(m.group(3)), int(m.group(4)), name))
            continue
        m = EXTRA_PAT.match(name)
        if m:
            by_lineage.setdefault((m.group(1), m.group(2)), []).append(
                (int(m.group(3)), int(m.group(4)), name))
    return by_lineage


def main():
    apply = "--apply" in sys.argv
    by_lineage = scan()
    if not by_lineage:
        raise SystemExit(
            "정리할 파일이 없습니다 "
            "(`세상공부_YYMMDD_노트_vN_M.md` · `세상공부_YYMMDD_<종류>_<주제>_vN_M.md`만 대상).")

    moves, keeps = [], []
    for key in sorted(by_lineage):
        versions = sorted(by_lineage[key])         # (major, minor) 오름차순
        keeps.append((key, versions[-1][2]))
        moves.extend((key, n) for _, _, n in versions[:-1])

    dates = {k[0] for k in by_lineage}
    extras = sum(1 for k in by_lineage if k[1])
    print("날짜 %d개 · 계보 %d개(노트 %d · 파생 %d) · 메인 유지 %d · 이전버전으로 이동 %d\n"
          % (len(dates), len(by_lineage), len(by_lineage) - extras, extras,
             len(keeps), len(moves)))
    for key, name in keeps:
        print("  keep  %s" % name)
    if not moves:
        print("\n이미 계보당 1개입니다.")
        return 0

    print()
    if apply:
        os.makedirs(ARCHIVE, exist_ok=True)
    for _key, name in moves:
        src, dst = os.path.join(NOTES, name), os.path.join(ARCHIVE, name)
        if apply:
            if os.path.exists(dst):                 # 이미 같은 이름이 보관돼 있으면 덮지 않는다
                print("  SKIP  %s (이전버전에 이미 있음)" % name)
                continue
            shutil.move(src, dst)
            print("  MOVE  %s" % name)
        else:
            print("  would %s" % name)

    if not apply:
        print("\n실제로 옮기려면 --apply 를 붙여 다시 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
