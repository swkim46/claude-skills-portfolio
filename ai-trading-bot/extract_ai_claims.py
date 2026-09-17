#!/usr/bin/env python3
"""
분석노트에서 `[AI]` 클레임을 뽑아 cache_claims.py가 읽는 형식으로 낸다.

왜 스크립트인가: `cache_claims.py`가 기대하는 입력은 **번호 매긴 목록**
(`1. (L41) 내용`)이다. 불릿으로 내면 "클레임을 못 읽었습니다"로 조용히 실패하고,
그러면 fact-check 단계가 통째로 건너뛰어진다 — 검증이 빠진 줄도 모르고 진행되는
것이 이 파이프라인에서 가장 위험한 실패 모드라 형식을 코드로 고정한다.
(Phase 2 리허설에서 실제로 이 불일치가 잡혔다.)

마크다운 줄바꿈으로 이어진 항목은 다음 불릿/헤더를 만날 때까지 이어 붙인다 —
문장이 잘리면 검증 대상이 달라진다.

사용:
    python3 extract_ai_claims.py analysis/분석노트_260903_kr_v1_0.md
    → analysis/AI클레임_260903_kr.md (경로를 stdout에 출력)
"""
import argparse
import re
import sys
from pathlib import Path

# 라벨을 **쓴** 줄만 잡는다. 백틱으로 감싼 `[AI]`는 라벨을 **언급**한 것이다 —
# 노트가 자기 표기 규칙을 설명하는 줄("`[AI]` 클레임 0건", "`[AI]`와 `[추정]`으로 갈라 단다")이
# 여기 걸리면 존재하지 않는 사실이 fact-check로 넘어가고, 검증할 수 없으니 MISMATCH가 나서
# run 전체가 막힌다. 2026-09-08 첫 실 분석에서 실제로 걸렸다.
TAG = re.compile(r"(?<!`)\[AI\](?!`)")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
HEADER = re.compile(r"^\s*#{1,6}\s")


def extract(note_path: Path) -> list:
    """반환: [(줄번호, 클레임 본문)] — 줄바꿈으로 이어진 항목은 합쳐서."""
    lines = note_path.read_text(encoding="utf-8").splitlines()
    claims, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not TAG.search(line):
            i += 1
            continue

        lineno = i + 1
        is_bullet = bool(BULLET.match(line))
        body = TAG.sub("", BULLET.sub("", line), count=1).strip()
        indent = len(lines[i]) - len(lines[i].lstrip())

        # 이어지는 줄 붙이기: 빈 줄·헤더·새 불릿·새 클레임을 만나면 종료.
        #
        # 들여쓰기 규칙은 **불릿 항목에만** 적용한다. 불릿에서는 들여쓰기가 얕아지면 다음
        # 항목이라는 뜻이지만, 문단은 여러 줄이 모두 왼쪽 끝에서 시작하므로 같은 규칙을 쓰면
        # 첫 줄에서 잘린다 — 그러면 fact-check가 **잘린 주장**을 받아 검증할 수 없다.
        # (2026-09-08 발견: 문단형 [AI] 클레임의 수치가 둘째 줄부터 통째로 빠졌다.)
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip() or HEADER.match(nxt) or BULLET.match(nxt) or TAG.search(nxt):
                break
            if is_bullet and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body += " " + nxt.strip()
            j += 1

        body = re.sub(r"\s+", " ", body).strip()
        if body:
            claims.append((lineno, body))
        i = j
    return claims


def main() -> int:
    ap = argparse.ArgumentParser(description="분석노트의 [AI] 클레임을 추출한다")
    ap.add_argument("note", help="analysis/분석노트_YYMMDD_<mkt>_vN_M.md")
    ap.add_argument("--out", help="기본: 같은 폴더의 AI클레임_<YYMMDD>_<mkt>.md")
    args = ap.parse_args()

    note = Path(args.note)
    if not note.is_file():
        print(f"노트를 찾을 수 없다: {note}", file=sys.stderr)
        return 2

    claims = extract(note)
    if not claims:
        print(f"[AI] 클레임 0건 — {note.name}", file=sys.stderr)

    if args.out:
        out = Path(args.out)
    else:
        m = re.search(r"분석노트_(.+?)_v\d+_\d+", note.stem)
        stem = m.group(1) if m else note.stem
        out = note.parent / f"AI클레임_{stem}.md"

    body = [f"# AI 클레임 — {note.name}", "",
            f"출처: `{note}` · 총 {len(claims)}건", ""]
    body += [f"{n}. (L{lineno}) {text}" for n, (lineno, text) in enumerate(claims, 1)]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"[AI] {len(claims)}건 → {out}", file=sys.stderr)
    print(out)          # 경로를 stdout으로 — 파이프라인 계약
    return 0


if __name__ == "__main__":
    sys.exit(main())
