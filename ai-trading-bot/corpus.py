#!/usr/bin/env python3
"""로컬 코퍼스 — **이미 프로젝트가 가진 자료**를 색인하고 검색한다.

왜 있는가: 파이프라인이 읽는 것은 *오늘 받은* 재료와 *상태* 파일뿐이다. 그런데 프로젝트는
매 run마다 1차 출처 원문(`_raw_sources/`)·섹터 리서치(`analysis/섹터_*.md`)·과거 노트·감리
리포트를 쌓는다. **그 누적분을 아무 단계도 읽지 않아서, 이미 가진 답을 '조사불가'로 닫는다.**

*실사례(2026-09-10): map 단계가 "KRX 정기변경 편입·제외 명단 미확보 → 조사불가"로 닫았는데,
그 명단이 하루 전 받아둔 `_raw_sources/KRX섹터지수_리밸런싱_260909.md`에 있었다. 그 파일은
한미반도체 3,000억·주성엔지니어링 1,800억 유입을 지목했고 둘 다 우리 유니버스 안이었다.
그날 종가는 주성엔지니어링 +7.26%·한미반도체 +2.00%였다.*

**파이프라인을 단계로 쪼개면서 이 구멍이 커졌다** — 단계마다 문맥을 새로 열면 "어제 그 파일을
받았다"는 기억이 사라진다. 기억을 없앤 대가로 **파일 경로를 만들어야** 하고, 이 스크립트가 그것이다.

사용:
    python3 corpus.py index                        # 무엇을 가지고 있나 (캡처 파일에 넣는다)
    python3 corpus.py search 리밸런싱 정기변경        # 전문 검색 (OR)
    python3 corpus.py search 정제마진 --out data/run_evidence/corpus_260910_kr.md
"""
from __future__ import annotations

import argparse
import html
import io
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 누적 자산이 쌓이는 곳. data/는 과거 재료도 포함한다(오늘 것만 보면 그저께 사건을 놓친다).
ROOTS = ["_raw_sources", "analysis", "journal", "data"]
TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".html", ".htm", ".pdf"}
# ★ 과거분을 제외하지 않는다. 이 도구의 존재 이유가 **누적분을 뒤지는 것**인데
#   `이전버전/`(옛 노트)과 `run_evidence/`(그날 도구 출력 = 숫자의 원본)를 빼면
#   "그저께 그 값이 얼마였나"를 영원히 못 찾는다. `_superseded/`는 재실행에 밀려난
#   이어받기·캡처 파일이고 그것도 이력이다. 캐시만 건너뛴다.
SKIP_DIRS = {".corpus_cache"}
MAX_BYTES = 4_000_000

# 읽기 실패 원장 — **실패를 '없음'으로 바꾸지 않기 위해** 모아서 산출물에 같이 낸다.
READ_FAILURES: list = []


def _strip_html(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S | re.I)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))


def read_text(p: Path) -> str:
    """파일에서 텍스트를 뽑는다. **못 읽으면 원장에 적는다** — 빈 문자열은 '없음'이 아니다.

    ★ 이 함수가 이 도구의 존재 이유와 정면으로 부딪히던 자리다. 예전에는 실패를
    stderr에만 찍고 빈 문자열을 돌려줬고, 검색 루프가 그것을 조용히 건너뛴 다음
    **"히트 0건 — 코퍼스에는 없다고 쓸 수 있다"**고 허가했다. 즉 *못 읽은 파일에
    답이 있어도* '없다'는 결론이 나왔다 — 고치려던 병을 그대로 앓고 있었다.
    이제 실패는 `READ_FAILURES`에 남고 **산출물과 `--out` 로그에 같이 실린다.**
    """
    try:
        if p.suffix.lower() == ".pdf":
            import pypdf
            r = pypdf.PdfReader(str(p))
            return "\n".join((pg.extract_text() or "") for pg in r.pages)
        raw = p.read_text(encoding="utf-8", errors="ignore")
        return _strip_html(raw) if p.suffix.lower() in (".html", ".htm") else raw
    except Exception as e:                                    # noqa: BLE001 — 어떤 파일이든 건너뛰되 이유를 남긴다
        rec = (str(p), f"{type(e).__name__}: {e}"[:160])
        if rec not in READ_FAILURES:
            READ_FAILURES.append(rec)
        print(f"  [읽기 실패] {p.name}: {type(e).__name__}", file=sys.stderr)
        return ""


def walk() -> list:
    out = []
    for root in ROOTS:
        base = HERE / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(HERE).parts):
                continue
            if p.stat().st_size > MAX_BYTES:
                continue
            out.append(p)
    return out


def title_of(p: Path) -> str:
    """첫 헤더 또는 첫 의미 있는 줄. 무엇이 담긴 파일인지 한눈에 보이게 한다."""
    t = read_text(p)[:4000]
    for line in t.splitlines():
        s = line.strip().lstrip("#").strip()
        if len(s) >= 6 and not s.startswith(("{", "[", "<", "//")):
            return s[:110]
    return "(제목 없음)"


def cmd_index(a) -> int:
    files = walk()
    buf = io.StringIO()
    print(f"■ 로컬 코퍼스 — {len(files)}개 파일 (프로젝트가 이미 가진 자료)", file=buf)
    print("  이 목록에 있는 것을 '없다'고 쓰지 말 것. 닫기 전에 `corpus.py search`로 뒤진다.\n", file=buf)
    by_root = {}
    for p in files:
        by_root.setdefault(p.relative_to(HERE).parts[0], []).append(p)
    for root, ps in by_root.items():
        print(f"  ── {root}/  ({len(ps)}개)", file=buf)
        for p in ps:
            st = p.stat()
            when = datetime.fromtimestamp(st.st_mtime).strftime("%m-%d")
            print(f"     {when}  {st.st_size:>8,}B  {p.relative_to(HERE)}", file=buf)
            if a.titles:
                print(f"                          └ {title_of(p)}", file=buf)
    if READ_FAILURES:            # --titles가 읽기를 시도했다면 실패도 여기 실린다
        print(f"\n  ★ 읽지 못한 파일 {len(READ_FAILURES)}개 — 이 파일들은 검색에도 안 걸린다:",
              file=buf)
        for path, why in READ_FAILURES:
            print(f"     ✗ {path}  ({why})", file=buf)
    out = buf.getvalue()
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out, encoding="utf-8")
        print(f"→ {a.out}")
    return 3 if READ_FAILURES else 0


def cmd_search(a) -> int:
    kws = [k for k in a.keywords if k.strip()]
    if not kws:
        print("검색어를 하나 이상 주어라.", file=sys.stderr)
        return 2
    files = walk()
    buf = io.StringIO()
    print(f"■ 코퍼스 검색 — {', '.join(kws)}  ({len(files)}개 파일 대상)", file=buf)
    print(f"  실행 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n", file=buf)
    total_hits, hit_files = 0, 0
    for p in files:
        t = read_text(p)
        if not t:
            continue
        lines = t.splitlines()
        hits = []
        for i, line in enumerate(lines, 1):
            if any(k in line for k in kws):
                hits.append((i, line.strip()[:260]))
        if not hits:
            continue
        hit_files += 1
        total_hits += len(hits)
        print(f"  ── {p.relative_to(HERE)}  ({len(hits)}건)", file=buf)
        for i, line in hits[:a.max_per_file]:
            print(f"     L{i:<5} {line}", file=buf)
        if len(hits) > a.max_per_file:
            print(f"     … 그 외 {len(hits)-a.max_per_file}건 (파일을 직접 열어라)", file=buf)
        print(file=buf)
    # ★ 못 읽은 파일이 있으면 '없다'고 쓸 수 없다 — 그 파일에 답이 있을 수 있다.
    if READ_FAILURES:
        print(f"  ★ 읽지 못한 파일 {len(READ_FAILURES)}개 — **이 목록이 비지 않는 한 "
              f"'코퍼스에 없다'고 쓸 수 없다.**", file=buf)
        for path, why in READ_FAILURES:
            print(f"     ✗ {path}  ({why})", file=buf)
        print("     → 손으로 열어 확인하거나, 의존 패키지를 깔고(예: PDF는 `pypdf`) 다시 돌려라.",
              file=buf)
        print(file=buf)
    if not total_hits:
        if READ_FAILURES:
            print(f"  히트 0건 — 그러나 **{len(READ_FAILURES)}개를 못 읽었으므로 "
                  f"'없다'는 결론은 아직 낼 수 없다.** 위 실패 목록을 먼저 해소하라.", file=buf)
        else:
            print("  히트 0건 · 읽기 실패 0건 — **여기까지 확인했으므로 "
                  "'코퍼스에는 없다'고 쓸 수 있다.**", file=buf)
        print("  단 검색어를 바꿔 다시 볼 것: 동의어·영문명·다른 표기·상위 개념.", file=buf)
    else:
        print(f"  합계 {total_hits}건 / {hit_files}개 파일", file=buf)
        print("  ★ 히트가 있으면 그 파일을 열어 읽는다. 목록만 보고 닫지 말 것.", file=buf)
    out = buf.getvalue()
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with Path(a.out).open("a", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print(f"→ {a.out} (append)")
    # 종료코드 3 = **검색은 돌았고 로그도 남았으나 일부 파일을 못 읽어 결과가 불완전**이다.
    # 실패한 명령이 아니므로 로그는 그대로 쓰되, 0(완전)과 구분해 호출자가 알 수 있게 한다.
    if READ_FAILURES:
        print(f"\n★ 읽기 실패 {len(READ_FAILURES)}개로 결과가 불완전하다 — "
              f"'코퍼스에 없다'로 닫지 말 것 (로그는 기록됐다).", file=sys.stderr)
        return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index", help="가진 자료 목록 — 캡처 파일에 넣는다")
    i.add_argument("--titles", action="store_true", help="파일마다 첫 헤더도 같이")
    i.add_argument("--out", default="")
    s = sub.add_parser("search", help="전문 검색 (OR)")
    s.add_argument("keywords", nargs="+")
    s.add_argument("--max-per-file", type=int, default=6)
    s.add_argument("--out", default="", help="검색 로그를 이 파일에 append (게이트가 확인한다)")
    a = ap.parse_args()
    return cmd_index(a) if a.cmd == "index" else cmd_search(a)


if __name__ == "__main__":
    sys.exit(main())
