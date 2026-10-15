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
    python3 extract_ai_claims.py analysis/분석노트_261014_kr_v1_0.md --batch A    # 3단 끝 — 그때까지의 [AI] 줄(§2)
    python3 extract_ai_claims.py analysis/분석노트_261014_kr_v1_0.md --batch B    # 4단 심층 끝 — A에 없는 줄만(번호는 A 다음부터)
    → analysis/AI클레임_261014_kr_A.md · _B.md

묶음(2026-10-14 · trade-run fact-check 배경화): 배경 fact-check ②를 두 묶음으로 띄운다 — A는 3단 끝에 §2를, B는 4단 심층 끝에
나머지를. 묶음마다 판정표(`…_A_판정.md`)·lint·전파·게이트 ②·감사가 따로 돈다. 번호는 run 안에서 겹치지 않게 B가 A 다음부터 잇는다.
"""
import argparse
import hashlib
from datetime import datetime
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


def source_lines(note_text: str, shared: list = None) -> dict:
    """{S번호: '↳ 대조 S3 · <url> · as-of … · 원문 "…"'} — 노트 §12 표의 원장 id(SRC-…)로 공용 원장 행을 찾는다(2026-10-15).
    클레임 줄 바로 아래에 들여 붙인다 — 판정 기준(원 문구)은 `N. (L줄) 문장` 줄뿐이라 검사(lint_verdict)에 섞이지 않는다."""
    import json
    ms = re.search(r"^##\s*(§12|출처|Sources).*", note_text, re.M | re.S)
    if not ms:
        return {}
    if shared is None:
        try:
            shared = (json.loads((Path(__file__).resolve().parent / "journal" / "sources.json").read_text(encoding="utf-8")) or {}).get("sources") or []
        except (OSError, ValueError):
            return {}
    by_id = {r.get("id"): r for r in shared}
    out = {}
    for ln in ms.group(0).split("\n"):
        m = re.match(r"^\|\s*(S\d+)\s*\|", ln)
        mi = re.search(r"\bSRC-\d+\b", ln)
        r = by_id.get(mi.group(0)) if (m and mi) else None
        if r:
            q = str(r.get("quote") or "").replace("\n", " ")
            out[m.group(1)] = (f"   ↳ 대조 {m.group(1)} · {r.get('url') or 'URL 없음'} · as-of {r.get('as_of') or '미확인'}"
                               + (f' · 원문 "{q[:200]}"' if q else " · 원문 인용 없음"))
    return out


def with_sources(lines: list, src: dict) -> list:
    """번호 줄마다 그 줄의 [S번호] 대조 줄을 아래에 붙인다."""
    out = []
    for l in lines:
        out.append(l)
        if re.match(r"^\d+\.\s", l):
            for s in dict.fromkeys(re.findall(r"\[(S\d+)\]", l)):
                if s in src:
                    out.append(src[s])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="분석노트의 [AI] 클레임을 추출해 *고정*한다")
    ap.add_argument("note", help="analysis/분석노트_YYMMDD_<mkt>_vN_M.md")
    ap.add_argument("--out", help="기본: 같은 폴더의 AI클레임_<YYMMDD>_<mkt>.md")
    ap.add_argument("--append", action="store_true",
                    help="이미 고정한 목록에 *새로 생긴* [AI] 줄만 다음 번호로 덧붙인다(§2 먼저 · §6 나중)")
    ap.add_argument("--force", action="store_true", help="고정본을 덮어쓴다(원 문구를 잃는다 — 거의 쓰지 말 것)")
    ap.add_argument("--batch", choices=["A", "B"],
                    help="배경 fact-check 묶음 — A = 지금까지의 [AI] 줄(3단 끝) · B = A에 없는 줄만, 번호는 A 다음부터(4단 심층 끝)")
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
        out = note.parent / (f"AI클레임_{stem}_{args.batch}.md" if args.batch else f"AI클레임_{stem}.md")
    start = 1
    if args.batch == "B":
        a_path = out.with_name(out.name.replace("_B.md", "_A.md"))
        if a_path.exists():
            a_lines = [l for l in a_path.read_text(encoding="utf-8").splitlines() if re.match(r"^\d+\.\s", l)]
            a_keys = {re.sub(r"^\d+\.\s+(?:\(L\d+\)\s*)?", "", l)[:60] for l in a_lines}
            claims = [(ln, t) for ln, t in claims if t[:60] not in a_keys]
            start = max((int(re.match(r"^(\d+)\.", l).group(1)) for l in a_lines), default=0) + 1
        else:
            print(f"묶음 A가 없다({a_path.name}) — B가 노트의 [AI] 줄 전부를 담는다", file=sys.stderr)

    # ★ 고정(2026-10-01): 이 파일은 fact-check가 판정하는 *원 문구*다. 260929 US는 교정 뒤에 다시 뽑거나
    # 이전버전 노트를 제자리에서 고쳐 원 문구가 사라졌고, 판정 기준이 행마다 달라져 감사가 22회 FAIL했다.
    sha = hashlib.sha256(note.read_bytes()).hexdigest()[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = f"고정: {now} KST · 원본 `{note}` sha256 {sha}"
    if out.exists() and not (args.force or args.append):
        print(f"이미 고정된 클레임 목록이 있다: {out} — 덮어쓰지 않는다. 새 [AI] 줄만 더하려면 --append"
              f"(원 문구를 버리고 다시 뽑으려면 --force)", file=sys.stderr)
        print(out)
        return 3
    if out.exists() and args.append:
        old = out.read_text(encoding="utf-8").rstrip("\n").split("\n")
        have = [re.sub(r"^\d+\.\s+(?:\(L\d+\)\s*)?", "", l) for l in old if re.match(r"^\d+\.\s", l)]
        keys = {h[:60] for h in have}
        new = [(ln, t) for ln, t in claims if t[:60] not in keys]
        if not new:
            print(f"[AI] 새 클레임 0건 — 고정본 그대로: {out}", file=sys.stderr)
            print(out)
            return 0
        n0 = len(have)
        old.append("")
        old.append(f"{stamp} · 추가 {len(new)}건(--append)")
        old += with_sources([f"{n0 + k}. (L{ln}) {t}" for k, (ln, t) in enumerate(new, 1)],
                            source_lines(note.read_text(encoding="utf-8")))
        old = [re.sub(r"총 \d+건", f"총 {n0 + len(new)}건", l) if l.startswith("출처:") else l for l in old]
        out.write_text("\n".join(old) + "\n", encoding="utf-8")
        print(f"[AI] +{len(new)}건 덧붙임(총 {n0 + len(new)}) → {out}", file=sys.stderr)
        print(out)
        return 0

    body = [f"# AI 클레임 — {note.name}" + (f" · 묶음 {args.batch}" if args.batch else ""), "",
            f"출처: `{note}` · 총 {len(claims)}건" + (f" · 번호 {start}부터(묶음 A 다음)" if start > 1 else ""),
            f"{stamp} · 이 목록이 fact-check의 판정 기준(원 문구)이다 — 노트를 고친 뒤 다시 만들지 않는다", ""]
    body += with_sources([f"{n}. (L{lineno}) {text}" for n, (lineno, text) in enumerate(claims, start)],
                         source_lines(note.read_text(encoding="utf-8")))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"[AI] {len(claims)}건 → {out}", file=sys.stderr)
    print(out)          # 경로를 stdout으로 — 파이프라인 계약
    return 0


if __name__ == "__main__":
    sys.exit(main())
