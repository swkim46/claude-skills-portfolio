#!/usr/bin/env python3
"""fact-check 판정표 기계 검사 — 제출 전에 *셀 수 있는 결함*을 수 초 안에 잡는다.

왜 필요한가(2026-09-30): 판정표 한 건이 비동기 감사에서 22회 FAIL했다(약 10시간). 지적의 상당수가
사람이 읽지 않아도 기계로 잡히는 것이었다 — 집계와 표가 어긋남, 교정 뒤 문구로 판정한 행,
판정 행이 없는 하위 클레임, MISMATCH인데 교정 행이 없음. 이것들을 감사(LLM·회차당 15분+)가 아니라
이 스크립트(결정론·수 초)가 먼저 잡게 한다. 감사 기준을 대체하지 않는다 — 바닥을 올릴 뿐이다.

검사
  (a) 집계 — 머리 집계 줄 · 게이트 3/4 줄 · 그룹 행 수 · 메모의 '하위 N'이 표와 같은가 (--write면 다시 쓴다)
  (b) 원 문구 — 행마다 '원 문구' 칸의 인용이 고정 클레임 목록(또는 지정 파일의 그 줄)에 그대로 있는가
  (c) 범위 — 클레임의 숫자·날짜·따옴표 인용·날짜 붙은 지명 출처가 어느 행의 원 문구에든 들어 있는가
       (도구 증거 파일과 판정표의 〈판정 범위 밖〉 절에 있는 토큰은 제외)
  (d) 기록 — UNVERIFIABLE 행은 검색 시각을, 모든 행은 'T1 시도' 칸을 채웠는가
  (e) 교정 연결 — MISMATCH·STALE 행이 교정 번호를 가리키고, 그 교정 행의 심각도가 규칙과 맞는가
       (MISMATCH → 🟡 또는 🔴 · STALE → ⏳)
  (f) 티어 일관성 — URL 칸에 URL이 하나뿐인 행끼리, 같은 URL이 다른 티어면 위반
       (티어는 출처의 성질이다 · 다르게 둘 이유가 있으면 그 행 비고에 '티어 차이: <이유>')
  (h) 접근 라벨(2026-10-13) — '유료·페이월·구독' 라벨은 그 행 URL을 fetch.py로 실제로 연 기록(chars>0)이 있어야 하고 '미시도'와
       같이 쓸 수 없다 · '403·봇차단' 라벨이 그 행 URL의 fetch 기록과 어긋나면 경고(라벨이 다른 출처 얘기일 수 있다) — 접속 기록은
       판정표 옆 프로젝트의 journal/fetch_log.jsonl(없으면 이 검사는 건너뛴다)
  (i) URL 칸(2026-10-13) — http(s) 링크가 있어야 한다. 로컬 경로만(data/·journal/·analysis/)·'위 …' 같은 되짚기는 위반
       (티어가 '도구'·'data'인 행은 캡처 파일 경로를 출처로 받는다) · MATCH인데 링크가 도메인 루트(경로·쿼리 없음)뿐이면 위반 —
       루트 주소로는 그 사실이 거기 있는지 아무도 확인할 수 없다(10-13 VST 근거: ferc.gov 루트만으로 T1·MATCH) ·
       값이 정말 첫 화면에 있으면 비고에 '첫 화면'·'메인 페이지'라고 밝힌다(감사가 그 화면을 연다)
  (k) 열지 않은 출처(2026-10-13) — MATCH인데 비고에 인용한 원문·본문·기사를 '미열람·열지 못·못 열·안 열'이라 적었으면 위반
       (T1 시도 칸의 '열지 못함'은 1차 출처 얘기라 T2로 확인한 MATCH를 막지 않는다)
  (g) 사전 점검 손 집계 — 〈lens 사전 점검〉 절에 행 수·판정 건수·a/b/c 집계를 손으로 적으면 위반
       (표를 고치면 낡는다 — 2026-10-09 2차 감사가 'G1 55행 52/2/1(실제 51/3/1)'로 잡았다. 집계는 LINT 줄,
       서술은 클레임·행 번호로 지목)
  2026-10-14 — 감사(condition-preserved·grading-basis·verdict-vocabulary-honest·unsourced-or-unlinked)가 4 run 연속 잡은 것 중
  셀 수 있는 것을 승격:
  (e′) UNVERIFIABLE 행이 가리킨 교정 번호는 교정표에 있어야 한다. 심각도 줄의 🔵 = 교정표 🔵 행 + 교정 없는 UNVERIFIABLE 행
       (예전엔 UNVERIFIABLE 수를 그대로 써서 교정표에 없는 🔵가 줄에 남았다 — 두 run 연속 같은 지적)
  (l) 단서 탈락 MISMATCH(비고·판정 칸에 '한정어·조건·단서·예외·전제·갈래 … 탈락·빠짐·생략')는 `단서: '<교정문에 들어갈 낱말>'` 또는
       `삭제: '<교정문에서 빠질 낱말>'`을 적고, 연결된 교정의 조치 문구가 실제로 그 낱말을 담거나(단서) 뺀다(삭제)
       — 판정은 맞게 하고 교정문에서 단서를 또 떨어뜨린 것(261013 '공유 예외'·'그 도로에 연결되는 길')을 막는다
  (o) MATCH 행이 '재확인 목록'으로 보내면 위반 — 이용 전에 다시 볼 것이 있으면 그 하위 클레임은 STALE(⏳ 교정)이다
  (p) 원문과의 차이를 적은 MATCH 행(원문은 … · 노트는 … 생략 · 한정어 차이)은 비고에 `같은 뜻: <이유>`가 있어야 한다
       — 같은 유형의 차이를 어떤 행은 MATCH, 어떤 행은 MISMATCH로 매긴 것이 매 run 걸렸다. 이유를 적어야 기준이 보인다
  (r) 교정표의 근거 칸에 URL이나 판정 행 지목(클레임 N · 3(e) · L120)이 있어야 한다 — 출처 이름·'— G1 표'만은 위반
  (n) 조치 문구의 수치가 연결된 판정 행·현재·근거 칸 어디에도 없으면 경고(원문 인용 없이 계산값·새 수치를 끼운 교정)
  2026-10-15 — 감사 74회 FAIL·0회 PASS. 16 run 전부에서 걸린 '부재 단정'과 주문 근거 '적합성', 감사가 반려한 '무시'를 작성 단계로:
  (s) 부재 단정 — 판정·비고·T1 시도 칸에 '리포트·공시·발표·의견… 없음/미발견/찾지 못'·'발행하지 않음'·'no report/not found' 류가 있으면
       같은 행에 `검색: <검색어(변형 포함)> · <어디> · <HH:MM>`이 있어야 한다 — 한 목록·한 표현으로 안 나온 것을 '없음'으로 적지 않는다
  (z) 주문 근거 판정표(`…주문근거_판정.md`) — 행마다 비고에 `받치는 것: <논지|크기|배분|보유|매도> — <그 근거가 떠받치는 시그널 문장의 요지>`
       (10자 이상). 인용은 맞는데 다른 회사·다른 지표로 논지를 받친 행(10-14 미국 IBKR 근거에 JPM 순이자이익)을 쓰는 순간 드러내게 한다
  (ⓙ) `무시:` — 감사 지적을 무시하는 줄은 반증(http 링크) 또는 행 지목(L<n> · 클레임 N)이 같은 줄에 있어야 한다. 문서 규칙 인용만으로
       무시하면 다음 회차가 '무시 사유 부당'으로 돌아온다(10-14 미국 주문 근거 4회차)
  (x) 원문 대조(2026-10-15) — MATCH 행의 `원문 인용`을 그 행 URL의 **보관 원문**(trade-run `fetch.py`가 `data/_raw_fc/`에 저장 · 접속 기록 `raw_path`)과
       대조한다(공백 무시 · '…'로 자른 조각마다 8자 이상). 보관 원문이 있는데 인용이 없으면 위반(못 읽고 MATCH · 의역 인용 · 단서 탈락) ·
       보관 원문이 없으면 경고(그 링크는 fetch.py로 열지 않았다)

사용
  python3 lint_verdict.py <판정표.md> --claims <클레임 목록.md> \
      [--exclude-from <도구 증거 파일> ...] [--write]
  python3 lint_verdict.py --selftest

종료 코드: 0 = LINT OK · 1 = 위반 있음 · 2 = 사용법/파일 오류
"""
import argparse
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

VERDICTS = ("MISMATCH", "UNVERIFIABLE", "STALE", "MATCH")   # 접두 판정 순서 중요(MATCH가 마지막)
COLS = {
    "orig": ("원 문구", "original wording"),
    "verdict": ("판정", "verdict"),
    "tier": ("티어", "tier"),
    "t1": ("t1 시도", "t1 attempt"),
    "note": ("비고", "note"),
    "sub": ("하위 클레임", "sub-claim"),
    "url": ("url",),
    "quote": ("원문 인용", "원문", "quote", "source quote"),
}
SEV = "🔴🟡⏳🔵⚪"
LINT_RE = re.compile(r"^LINT (OK|FAIL)\b.*$", re.M)
URL_RE = re.compile(r"https?://[^\s)\]|>\"'<]+")
PRECHECK_RE = re.compile(r"^##+\s*lens 사전 점검.*?(?=^##\s|\Z)", re.M | re.S)
HAND_COUNT_RE = re.compile(
    r"(?<![A-Za-z\d])\d+\s*행(?![동정사렬위])"                                          # 55행 · 14행은 (T2 행·행동은 아님)
    r"|행\s*\d+\s*개"                                                                  # T1 행 13개
    r"|(?<![\d/])\d+(?:\s*/\s*\d+){2,}(?![\d/])"                                       # 52/2/1 · 46/5/0/2
    r"|\b\d+\s*=\s*\d+(?:\s*\+\s*\d+)+"                                               # 67 = 59+6+0+2
    r"|\b(?:MATCH|MISMATCH|STALE|UNVERIFIABLE)\s*\d+\s*건")                           # MISMATCH 2건
# 2026-10-14 (e′)(l)(o)(p)(r)(n)
CORR_COLS = {"cur": ("현재", "current"), "act": ("조치", "action", "fix"), "basis": ("근거", "basis", "source")}
SAME_RE = re.compile(r"같은\s*뜻\s*[:：]")
DIFF_RE = re.compile(r"원문은|원문엔|원문에는|원 문구는|원문과 달리|노트는[^.;|]{0,40}(?:생략|뺐|빠뜨|빠졌|좁|넓)"
                     r"|(?:한정어|단서|조건|범위|기준 축|주어)\s*(?:차이|탈락|생략|누락)")
COND_RE = re.compile(r"(?:한정어|조건|단서|예외|전제|갈래)[^.;|]{0,60}?(?:탈락|빠짐|빠져|빠졌|누락|생략|뺌|뺐)")
KEEP_RE = re.compile(r"(단서|삭제)\s*[:：]\s*['‘\"“]([^'’\"”]{2,80})['’\"”]")
BASIS_PTR = re.compile(r"https?://|클레임\s*\d+|(?<![\w])\d+\s*\([a-z]\)|\bL\d+\b|판정\s*행|교정\s*\d+", re.I)   # '교정 13과 같은 근거'는 그 행으로 이어진다
DROP_ALL = re.compile(r"^\W*(?:그|해당|이)?\s*(?:줄|문장|행|항목)?\s*(?:을|를)?\s*삭제")
NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
# 2026-10-15 (s)(z)(ⓙ)
ABSENT_RE = re.compile(
    r"(?:리포트|보고서|공시|발표|공지|기사|의견|자료|안내|일정|언급|보도|가이던스|컨센서스|데이터|업데이트|정정)\s*(?:은|는|이|가|도)?\s*"
    r"(?:없[다음었고어]|미발견|찾지\s*못|못\s*찾|확인되지\s*않)"
    r"|(?:발행|공표|공시|발표|제공|게시)(?:하지|되지)\s*않"
    r"|\bno\s+(?:newer\s+|new\s+|such\s+|later\s+)?(?:report|filing|announcement|mention|update|guidance|article|data|disclosure|statement)s?\b"
    r"|\b(?:not|never)\s+(?:found|announced|published|disclosed|reported)\b|\bnone\s+(?:found|published)\b", re.I)
SEARCH_RE = re.compile(r"(?:검색\s*[:：]|검색어|WebSearch|web\s*search)[^|]{3,}?\d{1,2}:\d{2}", re.I)   # 검색 기록 — 표기 셋 다 인정
SUPPORT_RE = re.compile(r"받치는\s*것\s*[:：]\s*\**\s*(?:논지|크기|배분|보유|매도)[^|]{10,}")
IGNORE_RE = re.compile(r"무시\s*[:：]")
IGNORE_PTR = re.compile(r"https?://|\bL\d+\b|클레임\s*\d+")


def norm(s: str) -> str:
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = s.replace("\\|", "|")
    for a, b in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), (" ", " "), ("‑", "-"), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def split_row(line: str):
    cells = re.split(r"(?<!\\)\|", line.strip())
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return [c.strip() for c in cells]


def verdict_of(cell: str):
    c = cell.strip().lstrip("*").strip()
    for v in VERDICTS:
        if c.upper().startswith(v):
            return v
    return None


def load_claims(path: Path):
    """`N. (L<line>) <text>` 줄 → {N: text}. 전체 텍스트도 돌려준다."""
    txt = path.read_text(encoding="utf-8")
    claims = {}
    for m in re.finditer(r"^(\d+)\.\s+(?:\(L\d+\)\s*)?(.*)$", txt, re.M):
        claims[int(m.group(1))] = m.group(2)
    return claims, txt


def parse_tables(lines):
    """판정표(헤더에 '원 문구'+'판정')와 교정표(헤더에 '심각도')를 찾는다."""
    verdict_rows, corr_rows, groups = [], {}, []
    i, cur_group = 0, None
    while i < len(lines):
        ln = lines[i]
        mg = re.match(r"^###\s+(G\d+|그룹\s*\d+)\b", ln)
        if mg:
            cur_group = (mg.group(1), i)
            groups.append([mg.group(1), i, 0])
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|\s*:?-{2,}", lines[i + 1]):
            head = [h.lower() for h in split_row(ln)]
            idx = {}
            for key, names in COLS.items():
                for j, h in enumerate(head):
                    if any(h.startswith(n) for n in names):
                        idx.setdefault(key, j)
            is_verdict = "orig" in idx and "verdict" in idx
            is_corr = any(h.startswith("심각도") or h.startswith("severity") for h in head)
            cidx = {}
            for key, names in CORR_COLS.items():
                for k, h in enumerate(head):
                    if any(h.startswith(n) for n in names):
                        cidx.setdefault(key, k)
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                cells = split_row(lines[j])
                if is_verdict:
                    verdict_rows.append({"line": j + 1, "cells": cells, "idx": idx,
                                         "group": cur_group[0] if cur_group else None})
                    if groups and cur_group and groups[-1][0] == cur_group[0]:
                        groups[-1][2] += 1
                elif is_corr and cells and re.match(r"^\d+$", cells[0]):
                    corr_rows[int(cells[0])] = {"line": j + 1, "sev": cells[1] if len(cells) > 1 else "",
                                                "cells": cells, "cidx": cidx}
                j += 1
            i = j
            continue
        i += 1
    return verdict_rows, corr_rows, groups


def cell(row, key):
    j = row["idx"].get(key)
    return row["cells"][j] if j is not None and j < len(row["cells"]) else ""


def ccell(c, key):
    """교정표 행의 칸(현재·조치·근거). 열을 못 찾으면 빈 문자열."""
    j = (c.get("cidx") or {}).get(key)
    cells = c.get("cells") or []
    return cells[j] if j is not None and j < len(cells) else ""


def corr_refs(row):
    """이 행이 가리키는 이 표의 교정 번호 — '그 표 교정 10'·'국내 판정표 교정 3'처럼 다른 표를 가리킨 것은 뺀다."""
    blob = " ".join(row["cells"])
    return [int(m.group(1)) for m in re.finditer(r"(?:교정|correction)\s*(\d+)", blob, re.I)
            if not re.search(r"표\s*(?:의\s*)?$", blob[max(0, m.start() - 6):m.start()])]


def excerpt_and_loc(orig: str):
    """원 문구 칸 → (인용 조각들, 위치). 인용은 따옴표 안, '…'로 나뉜 조각을 순서대로 대조한다."""
    o = norm(orig)
    m = re.search(r'"([^"]{4,})"', o)
    q = m.group(1) if m else re.sub(r"\(.*?\)\s*$", "", o).strip()
    parts = [p.strip(" .,;") for p in re.split(r"…|\.\.\.", q) if len(p.strip(" .,;")) >= 4]
    loc = None
    mc = re.search(r"(?:클레임|claim)\s*(\d+)", o, re.I)
    mf = re.search(r"([\w./가-힣()\-]+\.md)\s*:?\s*L(\d+)", o)
    if mf:
        loc = ("file", mf.group(1), int(mf.group(2)))
    elif mc:
        loc = ("claim", int(mc.group(1)))
    return parts, loc


def find_file(name: str, base_dirs):
    p = Path(name)
    if p.is_file():
        return p
    for b in base_dirs:
        for cand in (b / name, *b.rglob(Path(name).name)):
            if cand.is_file():
                return cand
    return None


TOKEN_RE = [
    re.compile(r"(?<![\w.])[-+−]?\$?\d[\d,]*(?:\.\d+)?(?:%|bp|B|M|K|x)?(?![\w.])"),   # 수치
    re.compile(r"\b\d{2}-\d{2}\b"),                                                     # MM-DD
]


def claim_tokens(text: str):
    """판정 행이 덮어야 할 토큰: 수치·날짜·따옴표 인용·날짜 붙은 괄호 출처."""
    t = norm(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text))        # 링크는 표시 텍스트만
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\(계산:[^)]*\)", " ", t)                             # 계산식은 자체계산 행이 덮는다
    toks = set()
    for rx in TOKEN_RE:
        for m in rx.finditer(t):
            tok = m.group(0).replace("−", "-")
            if re.fullmatch(r"[-+]?\d", tok):                          # 한 자리 정수는 서수·번호가 대부분
                continue
            toks.add(tok)
    for m in re.finditer(r'"([^"]{6,})"', t):
        toks.add('"' + m.group(1) + '"')
    for m in re.finditer(r"\(([^()]*\b\d{2}-\d{2}\b[^()]*)\)", t):
        inner = m.group(1).strip()
        if re.search(r"[A-Za-z가-힣]{3,}", inner) and len(inner) <= 60:
            toks.add("(" + inner + ")")
    return toks


def lint(path: Path, claims_path: Path, excl_paths, write=False):
    errs, warns = [], []
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    claims, claims_txt = load_claims(claims_path)
    base_dirs = [path.parent, path.parent.parent, claims_path.parent, claims_path.parent.parent]
    rows, corr, groups = parse_tables(lines)
    if not rows:
        errs.append("판정표를 찾지 못했다 — 헤더에 '원 문구'와 '판정' 열이 있어야 한다(fact-check §5 판정표 양식)")
        return errs, warns, Counter(), 0, text
    cnt = Counter()
    for r in rows:
        v = verdict_of(cell(r, "verdict"))
        if not v:
            errs.append("L%d 판정 칸이 4값(MATCH/MISMATCH/STALE/UNVERIFIABLE)으로 시작하지 않는다: %r"
                        % (r["line"], cell(r, "verdict")[:40]))
            continue
        r["v"] = v
        cnt[v] += 1
    total = sum(cnt.values())

    # (b) 원 문구
    cache = {}
    for r in rows:
        o = cell(r, "orig")
        if not o.strip() or o.strip() in ("-", "—"):
            errs.append("L%d 원 문구 칸이 비었다" % r["line"])
            continue
        parts, loc = excerpt_and_loc(o)
        if not loc:
            errs.append("L%d 원 문구에 위치가 없다 — '(클레임 N)' 또는 '(<파일>.md:L<n>)'를 붙여라" % r["line"])
            continue
        if loc[0] == "claim":
            hay = norm(claims.get(loc[1], ""))
            if not hay:
                errs.append("L%d 클레임 %d이 클레임 목록에 없다" % (r["line"], loc[1]))
                continue
        else:
            f = cache.get(loc[1]) or find_file(loc[1], base_dirs)
            cache[loc[1]] = f
            if not f:
                errs.append("L%d 원 문구 파일을 찾지 못했다: %s" % (r["line"], loc[1]))
                continue
            fl = f.read_text(encoding="utf-8", errors="ignore").split("\n")
            hay = norm(" ".join(fl[max(0, loc[2] - 3): loc[2] + 2]))
        pos = 0
        for p in parts:
            k = hay.find(p, pos)
            if k < 0:
                errs.append("L%d 원 문구 조각이 고정본에 없다(교정 뒤 문구를 판정했나?): %r" % (r["line"], p[:60]))
                break
            pos = k + len(p)

    # (c) 범위
    covered = norm(" ".join(cell(r, "orig") for r in rows))
    excl = ""
    for ep in excl_paths:
        try:
            excl += norm(Path(ep).read_text(encoding="utf-8", errors="ignore")) + " "
        except OSError:
            warns.append("제외 파일을 못 읽었다: %s" % ep)
    m = re.search(r"^##+\s*판정 범위 밖.*?(?=^##\s)", text, re.M | re.S)
    if m:
        excl += norm(m.group(0))
    for n, ct in sorted(claims.items()):
        for tok in sorted(claim_tokens(ct)):
            if tok in covered:
                continue
            bare = tok.strip('"()')
            if bare and (bare in covered):
                continue
            if bare and bare in excl:
                continue
            if tok.startswith("(") and tok.endswith(")"):
                # 도구 수치로만 된 괄호(예: 기저율 '(09-28 close 389.44 below the 20-day line …, n=157)')는
                # 안의 수치가 전부 도구 증거·판정 행에 있으면 출처 지명이 아니다.
                nums = [x for rx in TOKEN_RE for x in rx.findall(bare)]
                if nums and all(x in excl or x in covered for x in nums) and not re.search(
                        r"\b(?:via|per|according|보도|인용)\b|[A-Z][a-z]+ (?:AM|PM|Markets|Closer|Macro)\b", bare):
                    continue
            errs.append("클레임 %d의 '%s'를 덮는 판정 행이 없다(행 추가 또는 〈판정 범위 밖〉에 이유와 함께 명시)" % (n, tok[:60]))

    # (d) 기록
    for r in rows:
        if r.get("v") == "UNVERIFIABLE":
            blob = cell(r, "verdict") + " " + cell(r, "note")
            if not re.search(r"\d{1,2}:\d{2}|\d{1,2}:\dx", blob):
                errs.append("L%d UNVERIFIABLE인데 검색 시각이 없다(검색어·시각을 적어라)" % r["line"])
        if "t1" in r["idx"]:
            t1 = cell(r, "t1").strip()
            if not t1 or t1 in ("-", "—"):
                errs.append("L%d 'T1 시도' 칸이 비었다(URL·상태, 'T1 자체', 또는 '미시도 — 사유')" % r["line"])
    if "t1" not in rows[0]["idx"]:
        errs.append("판정표에 'T1 시도' 열이 없다(fact-check §5 판정표 양식)")

    # (e) 교정 연결
    for r in rows:
        if r.get("v") not in ("MISMATCH", "STALE"):
            continue
        blob = " ".join(r["cells"])
        nums = [int(x) for x in re.findall(r"(?:교정|correction)\s*(\d+)", blob, re.I)]
        if not nums:
            errs.append("L%d %s인데 교정 번호(교정 N)가 없다" % (r["line"], r["v"]))
            continue
        okset = "🟡🔴" if r["v"] == "MISMATCH" else "⏳"
        for k in nums:
            c = corr.get(k)
            if not c:
                errs.append("L%d 교정 %d 행이 교정표에 없다" % (r["line"], k))
            elif not any(ch in c["sev"] for ch in okset):
                errs.append("교정 %d(L%d) 심각도 '%s'가 %s 행과 맞지 않는다(%s)"
                            % (k, c["line"], c["sev"][:6], r["v"], okset))

    # (f) 티어 일관성 — 티어는 출처의 성질이다. URL 칸에 하나만 있는 행끼리 같은 URL이면 같은 티어여야 한다.
    #     *실사례(2026-10-09 1차 감사): 같은 BusinessWire 전재본이 한 행은 T1, 다른 행은 T2였다.*
    by_url = defaultdict(list)
    for r in rows:
        if "url" not in r["idx"]:
            break
        urls = {u.rstrip(".,;") for u in URL_RE.findall(cell(r, "url"))}
        mt = re.match(r"\s*\**\s*(T[123])\b", cell(r, "tier"))
        if mt and len(urls) == 1:
            by_url[urls.pop()].append((r["line"], mt.group(1), "티어 차이" in cell(r, "note")))
    for u, lst in by_url.items():
        if len({t for _, t, _ in lst}) > 1 and not any(x for _, _, x in lst):
            by_t = defaultdict(list)
            for ln, t, _ in lst:
                by_t[t].append("L%d" % ln)
            errs.append("같은 URL이 행마다 다른 티어(%s) — 맞추거나, 다르게 둔 행의 비고에 '티어 차이: <이유>'를 적어라: %s"
                        % (" / ".join("%s %s" % (t, ",".join(ls[:4]) + ("…" if len(ls) > 4 else ""))
                                      for t, ls in sorted(by_t.items())), u[:90]))

    # (h)(i)(k) — 2026-10-13: 감사 48건이 전부 FAIL이었고 '유료' 라벨(열어 보지 않고 붙임)·URL 칸 로컬 경로·
    #   '미열람'인데 MATCH가 매 run 걸렸다. 셀 수 있는 것은 감사가 아니라 여기서 먼저 잡는다.
    flog = {}
    raw_of, proj_dir = {}, None
    for cand in (path.parent.parent / "journal" / "fetch_log.jsonl", path.parent / "journal" / "fetch_log.jsonl"):
        if cand.is_file():
            proj_dir = cand.parent.parent
            for ln in cand.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    fr = json.loads(ln)
                except ValueError:
                    continue
                flog.setdefault(str(fr.get("url", "")).rstrip("/"), []).append(fr)
                if fr.get("raw_path"):
                    raw_of[str(fr.get("url", "")).rstrip("/")] = fr["raw_path"]
            break
    for r in rows:
        blob = " ".join(r["cells"])
        urls = [u.rstrip(".,;)") for u in URL_RE.findall(blob)]
        tierc = cell(r, "tier")
        if "url" in r["idx"]:
            ucell = cell(r, "url")
            if not URL_RE.search(ucell) and not re.match(r"\s*\**\s*(도구|data)", tierc, re.I):
                if re.search(r"(^|\s)(data|journal|analysis)/|위\s|above|same as", ucell):
                    errs.append("L%d URL 칸에 링크가 없다(로컬 경로·'위 …' 되짚기) — 그 행의 http 링크를 그대로 적어라: %r"
                                % (r["line"], ucell[:60]))
                elif ucell.strip() not in ("", "-", "—") or r.get("v") == "MATCH":
                    errs.append("L%d URL 칸에 http 링크가 없다: %r" % (r["line"], ucell[:60]))
        if r.get("v") == "MATCH":
            cand = URL_RE.findall(cell(r, "url")) if "url" in r["idx"] else urls
            cand = [u.rstrip(".,;)") for u in cand]
            front = re.search(r"첫\s*화면|메인\s*페이지|홈\s*화면|homepage|front page", cell(r, "note") + " " + cell(r, "t1"), re.I)
            if cand and not front and all(re.match(r"^https?://[^/?#\s]+/?$", u) for u in cand):
                errs.append("L%d MATCH인데 출처가 도메인 루트 주소뿐이다(%s) — 그 사실이 실제로 보이는 하위 페이지 주소를 적어라"
                            % (r["line"], cand[0][:60]))
        if re.search(r"유료|페이월|paywall|구독", blob, re.I):
            if "미시도" in blob:
                errs.append("L%d '유료' 라벨과 '미시도'가 같이 있다 — 열어 보지 않은 출처를 유료로 적지 않는다(fetch.py로 열어 게이트 문구를 인용)" % r["line"])
            elif flog and not any(fr.get("chars", 0) for u in urls for fr in flog.get(u.rstrip("/"), [])):
                errs.append("L%d '유료' 라벨인데 그 URL을 연 기록(fetch_log chars>0)이 없다 — fetch.py로 열어 로그인·구독 게이트 문구를 비고에 인용하라" % r["line"])
        if flog and re.search(r"\b403\b|봇\s?차단", blob):
            got = [fr for u in urls for fr in flog.get(u.rstrip("/"), [])]
            if got and not any("403" in str(fr.get("err", "")) for fr in got):
                warns.append("L%d '403/봇차단' 라벨인데 그 행 URL의 접속 기록은 %r — 라벨이 이 행의 다른 출처 얘기가 아니면 기록과 맞춰 적어라"
                            % (r["line"], str(got[-1].get("err") or "성공(%s자)" % got[-1].get("chars"))[:50]))
            elif not got:
                warns.append("L%d '403/봇차단' 라벨의 URL에 fetch 기록이 없다 — fetch.py로 연 결과를 남기면 감사가 확인할 수 있다" % r["line"])
        if r.get("v") == "MATCH" and re.search(r"(원문|본문|기사|페이지|문서|보고서|자료)\s*(을|를|은|는)?\s*(미열람|열지 못|못 열|안 열|미개방)",
                                               cell(r, "note")):
            errs.append("L%d MATCH인데 비고에 인용한 원문을 열지 못했다고 적혀 있다 — 열지 않은 출처로 MATCH를 주지 않는다(UNVERIFIABLE)" % r["line"])

    # (x) 원문 대조 — 보관 원문이 있는 MATCH 행만 위반으로 · 없으면 경고 (2026-10-15)
    no_raw = 0
    for r in rows:
        if r.get("v") != "MATCH" or "quote" not in r["idx"]:
            continue
        q = cell(r, "quote")
        segs = [x for x in (re.sub(r"\s+", "", norm(sg)).strip("\"'“”‘’「」") for sg in re.split(r"…|\.\.\.", q))
                if len(x) >= 8]
        if not segs:
            continue
        urls = [u.rstrip(".,;)").rstrip("/") for u in URL_RE.findall(cell(r, "url"))] if "url" in r["idx"] else []
        texts = []
        for u in urls:
            rp = raw_of.get(u)
            if rp and proj_dir is not None and (proj_dir / rp).is_file():
                texts.append(re.sub(r"\s+", "", norm((proj_dir / rp).read_text(encoding="utf-8", errors="ignore"))))
        if not texts:
            no_raw += 1
            continue
        miss = [sg for sg in segs if not any(sg in t for t in texts)]
        if miss:
            errs.append("L%d MATCH인데 원문 인용 조각이 보관 원문에 없다(%r) — 원문을 다시 열어 그대로 옮기거나(의역·요약 금지), "
                        "단서가 빠졌으면 판정을 고친다" % (r["line"], miss[0][:40]))
    if no_raw:
        warns.append("MATCH %d행은 보관 원문이 없어 인용을 대조하지 못했다 — 원문은 `fetch.py`로 열어야 보관된다" % no_raw)

    # (g) 사전 점검 손 집계 — 서술에 손으로 옮긴 숫자는 표를 고치면 낡는다(집계는 LINT 줄 · 서술은 클레임·행 지목).
    for m in PRECHECK_RE.finditer(text):
        base = text[:m.start()].count("\n") + 1
        for k, ln in enumerate(m.group(0).split("\n")):
            hits = [h.group(0) for h in HAND_COUNT_RE.finditer(ln)]
            if hits:
                errs.append("L%d 〈lens 사전 점검〉에 손으로 센 집계 %s — 표를 고치면 낡는다. 집계는 LINT 줄에 맡기고 "
                            "서술은 클레임·행(L…)을 지목하라" % (base + k, " · ".join("'%s'" % h for h in hits[:5])))

    # (e′)(l)(o)(p) — 행 단위 (2026-10-14)
    uncorrected_u = 0
    for r in rows:
        v = r.get("v")
        vn = cell(r, "verdict") + " " + cell(r, "note")
        refs = corr_refs(r)
        if v == "UNVERIFIABLE":
            if not refs:
                uncorrected_u += 1
            for k in refs:
                if k not in corr:
                    errs.append("L%d UNVERIFIABLE가 가리킨 교정 %d 행이 교정표에 없다(🔵 각주 또는 라벨 교체 행을 둘 것)" % (r["line"], k))
        if v == "MISMATCH" and COND_RE.search(vn):
            keeps = KEEP_RE.findall(vn)
            if not keeps:
                errs.append("L%d 단서 탈락 MISMATCH인데 `단서: '<교정문에 들어갈 낱말>'`(또는 `삭제: '<빠질 낱말>'`) 표기가 없다 — "
                            "교정문이 그 단서를 실제로 담는지 기계로 대조한다" % r["line"])
            acts = [(k, ccell(corr[k], "act")) for k in refs if k in corr and ccell(corr[k], "act")]
            acts = [(k, a) for k, a in acts if not DROP_ALL.match(norm(a))]
            for kind, w in keeps:
                if not acts:
                    break
                hit = [k for k, a in acts if norm(w) in norm(a)]
                if kind == "단서" and not hit:
                    errs.append("L%d 단서 '%s'가 연결된 교정(%s)의 조치 문구에 없다 — 교정문이 단서를 다시 떨어뜨렸다"
                                % (r["line"], w[:40], ", ".join(str(k) for k, _ in acts)))
                if kind == "삭제" and len(hit) == len(acts):
                    errs.append("L%d 삭제할 '%s'가 교정(%s)의 조치 문구에 남아 있다"
                                % (r["line"], w[:40], ", ".join(str(k) for k in hit)))
        if v == "MATCH":
            if re.search(r"재확인\s*목록", " ".join(r["cells"])):
                errs.append("L%d MATCH 행이 '재확인 목록'으로 보낸다 — 이용 전에 다시 볼 것이 있으면 그 하위 클레임은 STALE(⏳ 교정)로, "
                            "원 문구가 말하지 않은 후속 사실이면 행과 잇지 말고 목록에 따로 적어라" % r["line"])
            d = DIFF_RE.search(vn)
            if d and not SAME_RE.search(vn):
                errs.append("L%d 원문과의 차이(%r)를 적은 MATCH인데 `같은 뜻: <이유>`가 없다 — 이유를 적거나, 뜻이 바뀌면 MISMATCH"
                            % (r["line"], d.group(0)[:20]))

    # (s)(z) — 행 단위 (2026-10-15)
    order_table = "주문근거_판정" in path.name
    for r in rows:
        own = " ".join((cell(r, "verdict"), cell(r, "note"), cell(r, "t1")))
        a = ABSENT_RE.search(own)
        if a and not SEARCH_RE.search(" ".join(r["cells"])):
            errs.append("L%d 부재 단정(%r)인데 같은 행에 `검색: <검색어(변형 포함)> · <어디> · <HH:MM>`이 없다 — 어디서 무엇으로 언제 "
                        "찾았는지 적거나, 못 찾은 것이면 '없음'이 아니라 '확인 못 함'으로" % (r["line"], a.group(0)[:24]))
        if order_table and not SUPPORT_RE.search(cell(r, "note")):
            errs.append("L%d 주문 근거 행인데 비고에 `받치는 것: <논지|크기|배분|보유|매도> — <떠받치는 시그널 문장 요지>`가 없다 — "
                        "이 근거가 그 매수의 무엇을 받치는지 적어라(다른 회사·다른 지표면 여기서 드러난다)" % r["line"])
    # (ⓙ) 무시 — 반증 링크나 행 지목이 같은 줄에
    for k, ln in enumerate(text.split("\n"), 1):
        if IGNORE_RE.search(ln) and not IGNORE_PTR.search(ln):
            errs.append("L%d `무시:`인데 반증(http 링크)도 행 지목(L<n> · 클레임 N)도 없다 — 문서 규칙 인용만으로는 감사 지적을 무시하지 않는다" % k)

    # (r)(n) — 교정표 행 단위
    linked = defaultdict(list)
    for r in rows:
        for k in corr_refs(r):
            linked[k].append(" ".join(r["cells"]))
    for k, c in sorted(corr.items()):
        if "basis" in (c.get("cidx") or {}):
            b = ccell(c, "basis")
            if not BASIS_PTR.search(b):
                errs.append("교정 %d(L%d) 근거 칸에 URL도 판정 행 지목(클레임 N · 3(e) · L…)도 없다: %r" % (k, c["line"], b[:50]))
        act = ccell(c, "act")
        if act:
            hay = norm(" ".join(linked.get(k, [])) + " " + ccell(c, "basis") + " " + ccell(c, "cur"))
            new_nums = sorted({n for n in NUM_RE.findall(norm(act)) if len(n.replace(",", "").replace(".", "")) >= 2
                               and n not in hay})
            if new_nums:
                warns.append("교정 %d(L%d) 조치의 수치 %s가 연결 판정 행·현재·근거 칸에 없다 — 원문 인용을 판정 행이나 근거 칸에 먼저 두거나 "
                             "`(계산: …)`을 붙여라" % (k, c["line"], ", ".join(new_nums[:4])))

    # (a) 집계 — 비교하고, --write면 다시 쓴다
    sev = Counter()
    for c in corr.values():
        for ch in SEV:
            if c["sev"].strip().startswith(ch):
                sev[ch] += 1
    new = text

    def fix(pattern, render, label):
        nonlocal new
        ms = list(re.finditer(pattern, new))
        if not ms:
            warns.append("%s 줄을 찾지 못했다" % label)
            return
        for mm in ms:
            want = render(mm)
            if mm.group(0) != want:
                (lambda: None)()
                if not write:
                    errs.append("%s 불일치: '%s' → 표 기준 '%s'" % (label, mm.group(0)[:80], want[:80]))
        if write:
            new = re.sub(pattern, lambda mm: render(mm), new)

    fix(r"\*\*MISMATCH \d+건 · STALE \d+건 · 🔴\(비가역\) (\d+)건\*\* · UNVERIFIABLE \d+건 · MATCH \d+건 \(하위 클레임 \d+건\)",
        lambda mm: "**MISMATCH %d건 · STALE %d건 · 🔴(비가역) %s건** · UNVERIFIABLE %d건 · MATCH %d건 (하위 클레임 %d건)"
        % (cnt["MISMATCH"], cnt["STALE"], mm.group(1), cnt["UNVERIFIABLE"], cnt["MATCH"], total), "머리 집계")
    fix(r"MATCH \d+ · MISMATCH \d+ · STALE \d+ · UNVERIFIABLE \d+",
        lambda mm: "MATCH %d · MISMATCH %d · STALE %d · UNVERIFIABLE %d"
        % (cnt["MATCH"], cnt["MISMATCH"], cnt["STALE"], cnt["UNVERIFIABLE"]), "게이트 3")
    if corr:
        # 🔵 = 교정표 🔵 행 + 교정 없는 UNVERIFIABLE 행(2026-10-14 · 예전엔 UNVERIFIABLE 수를 그대로 써서 교정표와 어긋났다)
        blue_note = (" (🔵 = 교정표 %d행 + 교정 없는 UNVERIFIABLE %d행)" % (sev["🔵"], uncorrected_u)) if uncorrected_u else ""
        fix(r"🔴 (\d+) / 🟡 \d+ / ⏳ \d+ / 🔵 \d+ / ⚪ \d+(?: \(🔵 = 교정표 \d+행 \+ 교정 없는 UNVERIFIABLE \d+행\))?",
            lambda mm: "🔴 %d / 🟡 %d / ⏳ %d / 🔵 %d / ⚪ %d%s"
            % (sev["🔴"], sev["🟡"], sev["⏳"], sev["🔵"] + uncorrected_u, sev["⚪"], blue_note), "게이트 4 심각도")
    for g, gi, n in groups:
        pat = r"(^###\s+%s\b[^\n]*?)(\d+)행" % re.escape(g)
        mm = re.search(pat, new, re.M)
        if mm and int(mm.group(2)) != n:
            if write:
                new = new[:mm.start(2)] + str(n) + new[mm.end(2):]
            else:
                errs.append("그룹 %s 머리 '%s행' ≠ 표 %d행" % (g, mm.group(2), n))
    for mm in list(re.finditer(r"하위 (\d+)\)", new)):
        if int(mm.group(1)) != total and not write:
            errs.append("메모 '하위 %s' ≠ 표 %d" % (mm.group(1), total))
    if write:
        new = re.sub(r"하위 \d+\)", "하위 %d)" % total, new)
    return errs, warns, cnt, total, new


def stamp_line(cnt, total, ok):
    t = datetime.now().strftime("%Y-%m-%d %H:%M")
    return ("LINT %s (lint_verdict.py %s · 행 %d · MATCH %d · MISMATCH %d · STALE %d · UNVERIFIABLE %d)"
            % ("OK" if ok else "FAIL", t, total, cnt["MATCH"], cnt["MISMATCH"], cnt["STALE"], cnt["UNVERIFIABLE"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verdict", nargs="?")
    ap.add_argument("--claims")
    ap.add_argument("--exclude-from", action="append", default=[])
    ap.add_argument("--write", action="store_true", help="집계 숫자를 표 기준으로 다시 쓰고, 통과하면 LINT OK 줄을 쓴다")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.verdict or not a.claims:
        ap.print_usage()
        return 2
    vp, cp = Path(a.verdict), Path(a.claims)
    if not vp.is_file() or not cp.is_file():
        print("파일 없음: %s / %s" % (vp, cp), file=sys.stderr)
        return 2
    errs, warns, cnt, total, new = lint(vp, cp, a.exclude_from, write=a.write)
    if a.write:
        vp.write_text(new, encoding="utf-8")
        errs, warns, cnt, total, new = lint(vp, cp, a.exclude_from, write=False)
        txt = vp.read_text(encoding="utf-8")
        txt = LINT_RE.sub("", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        if not errs:
            m = re.search(r"^\*\*MISMATCH \d+건[^\n]*$", txt, re.M)
            line = stamp_line(cnt, total, True)
            txt = (txt[:m.end()] + "\n\n" + line + txt[m.end():]) if m else (line + "\n\n" + txt)
        vp.write_text(txt, encoding="utf-8")
    for w in warns:
        print("  ※ " + w)
    if errs:
        print("LINT FAIL — 위반 %d건 (%s)" % (len(errs), vp.name))
        for e in errs:
            print("  - " + e)
        return 1
    print(stamp_line(cnt, total, True))
    return 0


SAMPLE_CLAIMS = """# AI 클레임 — 표본
1. (L10) The museum opens at 09:00 and charges $25 for adults (City guide 03-15).
2. (L12) Entry is free on 06-01 for all visitors.
"""
SAMPLE_VERDICT = """# fact-check 판정 — 표본

**MISMATCH 1건 · STALE 0건 · 🔴(비가역) 0건** · UNVERIFIABLE 0건 · MATCH 3건 (하위 클레임 4건)

| 단계 | 수행 | 증빙 |
|---|---|---|
| 3. 내용 일치 | ✓ | MATCH 3 · MISMATCH 1 · STALE 0 · UNVERIFIABLE 0 |
| 4. 심각도 | ✓ | 🔴 0 / 🟡 1 / ⏳ 0 / 🔵 0 / ⚪ 0 |

## 교정 지시서

| # | 심각도 | 위치 | 현재 | 조치 | 근거 |
|---|---|---|---|---|---|
| 1 | 🟡 | 본문 | "$25 for adults" | 원문대로 "$30" | https://example.org/visit |

### G1 — 박물관 — 4행

| 원 문구(위치) | 하위 클레임 | 판정 | 티어 | 원문 인용 | URL | T1 시도 | 비고 |
|---|---|---|---|---|---|---|---|
| "opens at 09:00" (클레임 1) | 개장 시각 | MATCH | T1 | "Open daily 9 am" | https://example.org/visit | T1 자체 | — |
| "charges $25 for adults" (클레임 1) | 성인 요금 | MISMATCH — 원문 $30 | T1 | "Adults $30" | https://example.org/visit | T1 자체 | 교정 1 |
| "(City guide 03-15)" (클레임 1) | 지명 출처 | MATCH | T2 | — | https://example.org/guide | https://example.org 200 | — |
| "Entry is free on 06-01" (클레임 2) | 무료입장일 | MATCH | T1 | "Free entry June 1" | https://example.org/visit | T1 자체 | — |

## lens 사전 점검

| lens | 적용 결과 |
|---|---|
| condition-preserved | 클레임 1 성인 요금의 조건(성인) 보존 — 요금 행은 MISMATCH(교정 1) |
"""


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "claims.md").write_text(SAMPLE_CLAIMS, encoding="utf-8")
        (dp / "v.md").write_text(SAMPLE_VERDICT, encoding="utf-8")
        e1, _, cnt, total, _ = lint(dp / "v.md", dp / "claims.md", [])
        ok1 = not e1 and total == 4 and cnt["MISMATCH"] == 1
        bad = SAMPLE_VERDICT.replace("MATCH 3건 (하위 클레임 4건)", "MATCH 2건 (하위 클레임 4건)") \
                            .replace('"opens at 09:00" (클레임 1)', '"opens at 10:00" (클레임 1)') \
                            .replace("| 교정 1 |", "| — |")
        (dp / "bad.md").write_text(bad, encoding="utf-8")
        e2, _, _, _, _ = lint(dp / "bad.md", dp / "claims.md", [])
        kinds = {("머리 집계" in x) for x in e2} | {("고정본에 없다" in x) for x in e2} | {("교정 번호" in x) for x in e2}
        ok2 = len(e2) >= 3 and True in kinds
        rc = main([str(dp / "bad.md"), "--claims", str(dp / "claims.md"), "--write"])
        fixed = (dp / "bad.md").read_text(encoding="utf-8")
        ok3 = rc == 1 and "MATCH 3건 (하위 클레임 4건)" in fixed and "LINT OK" not in fixed
        # (f)·(g): 같은 URL 다른 티어 · 사전 점검의 손 집계 — 사유를 적으면 (f)는 통과
        bad2 = SAMPLE_VERDICT.replace('| 무료입장일 | MATCH | T1 |', '| 무료입장일 | MATCH | T2 |') \
                             .replace("요금 행은 MISMATCH(교정 1)", "4행 전부 확인 · MISMATCH 1건 · 3/1/0")
        (dp / "bad2.md").write_text(bad2, encoding="utf-8")
        e4, _, _, _, _ = lint(dp / "bad2.md", dp / "claims.md", [])
        why_ok = bad2.replace('"Free entry June 1" | https://example.org/visit | T1 자체 | — |',
                              '"Free entry June 1" | https://example.org/visit | T1 자체 | 티어 차이: 표제만 확인 |')
        (dp / "bad3.md").write_text(why_ok, encoding="utf-8")
        e5, _, _, _, _ = lint(dp / "bad3.md", dp / "claims.md", [])
        ok4 = (any("다른 티어" in x for x in e4) and any("손으로 센 집계" in x for x in e4)
               and not any("다른 티어" in x for x in e5))
        bad5 = SAMPLE_VERDICT.replace('| "Free entry June 1" | https://example.org/visit | T1 자체 | — |',
                                      '| "Free entry June 1" | data/run_evidence/x.md | 미시도 — 유료 | — |') \
                             .replace('| "Open daily 9 am" | https://example.org/visit | T1 자체 | — |',
                                      '| "Open daily 9 am" | https://example.org/visit | T1 자체 | 원문 미열람 — 요약만 봤다 |')
        (dp / "bad5.md").write_text(bad5, encoding="utf-8")
        e6, _, _, _, _ = lint(dp / "bad5.md", dp / "claims.md", [])
        ok5 = (any("URL 칸" in x for x in e6) and any("'유료' 라벨과 '미시도'" in x for x in e6)
               and any("열지 못했다고" in x for x in e6))
        bad6 = SAMPLE_VERDICT.replace('| "Open daily 9 am" | https://example.org/visit | T1 자체 | — |',
                                      '| "Open daily 9 am" | https://example.org/ | T1 자체 | — |')
        (dp / "bad6.md").write_text(bad6, encoding="utf-8")
        e7, _, _, _, _ = lint(dp / "bad6.md", dp / "claims.md", [])
        ok5 = ok5 and any("도메인 루트" in x for x in e7)
        ok4 = ok4 and ok5
        ok6 = selftest_261014(dp)
        ok7 = selftest_261015(dp)
        ok6 = ok6 and ok7
        ok4 = ok4 and ok6
        print("selftest:", "OK" if (ok1 and ok2 and ok3 and ok4) else "FAIL", "| clean=%s errs=%d" % (ok1, len(e2)),
              "| write-fixes-counts=%s" % ok3, "| tier+hand-count=%s" % ok4, "| h/i/k=%s" % ok5, "| e′/l/o/p/r/n=%s" % ok6)
        if not ok1:
            for x in e1:
                print("   clean err:", x)
        if not ok4:
            for x in e4 + e5:
                print("   f/g err:", x)
        return 0 if (ok1 and ok2 and ok3 and ok4) else 1


def selftest_261015(dp: Path) -> bool:
    """(s)(z)(ⓙ) — 통과·실패 짝. 실패 사례는 10-14 감사가 실제로 잡은 형태."""
    res = {}

    def run(name, text):
        (dp / name).write_text(text, encoding="utf-8")
        e, w, _, _, _ = lint(dp / name, dp / "claims.md", [])
        return e

    base = SAMPLE_VERDICT
    row = '| "Entry is free on 06-01" (클레임 2) | 무료입장일 | MATCH | T1 | "Free entry June 1" | https://example.org/visit | T1 자체 | — |'
    ab = base.replace(row, row.replace("| T1 자체 | — |", "| T1 자체 | 더 새 공지 없음 — 안내는 이것뿐 |"))
    res["s 검색 기록 없는 부재 단정 적발"] = any("부재 단정" in x for x in run("s1.md", ab))
    res["s 검색 기록 있으면 통과"] = not any("부재 단정" in x for x in run("s2.md", ab.replace(
        "더 새 공지 없음 — 안내는 이것뿐", "더 새 공지 없음 — 검색: 'free entry'·'무료 입장' · 공식 공지 목록 · 11:25")))
    ab2 = base.replace(row, row.replace("| T1 자체 | — |", "| T1 해당 없음 — 재단은 이 지표를 발행하지 않음 | — |"))
    res["s T1 칸의 '발행하지 않음'도 부재 단정"] = any("부재 단정" in x for x in run("s3.md", ab2))
    res["s 부재가 아닌 '교정 없음'은 통과"] = not any("부재 단정" in x for x in run("s4.md", base.replace(
        row, row.replace("| T1 자체 | — |", "| T1 자체 | 교정 없음 · 차이 없음 |"))))
    order = run("AI클레임_261015_kr_주문근거_판정.md", base)
    res["z 주문 근거 판정표 — 받치는 것 없는 행 적발"] = sum("받치는 것" in x for x in order) == 4
    sup = base
    for i, (old, new) in enumerate(((("| T1 자체 | — |"), "| T1 자체 | 받치는 것: 논지 — 개장 시간이 방문 계획을 받친다 |"),)):
        sup = sup.replace(old, new)
    sup = sup.replace("| T1 자체 | 교정 1 |", "| T1 자체 | 교정 1 · 받치는 것: 크기 — 요금이 예산 몫을 정한다 |") \
             .replace("| https://example.org 200 | — |", "| https://example.org 200 | 받치는 것: 논지 — 출처 지명이 근거 등급을 받친다 |")
    res["z 받치는 것을 다 적으면 통과"] = not any("받치는 것" in x for x in run("AI클레임_261015_us_주문근거_판정.md", sup))
    res["z 일반 판정표엔 요구하지 않음"] = not any("받치는 것" in x for x in run("plain.md", base))
    ign = base + "\n| grading-basis | **무시: 이유** — stage5 규칙에 따라 재고정했다 |\n"
    res["ⓙ 반증 없는 무시 적발"] = any("무시" in x for x in run("j1.md", ign))
    res["ⓙ 행 지목 있는 무시 통과"] = not any("`무시:`" in x for x in run("j2.md", ign.replace("재고정했다", "재고정했다 — L12 원 문구 그대로")))
    # (x) 원문 대조 — 프로젝트 모양(analysis/·journal/·data/_raw_fc/)을 만든다
    pj = dp / "proj"
    (pj / "analysis").mkdir(parents=True, exist_ok=True)
    (pj / "journal").mkdir(exist_ok=True)
    (pj / "data" / "_raw_fc").mkdir(parents=True, exist_ok=True)
    (pj / "analysis" / "claims.md").write_text(SAMPLE_CLAIMS, encoding="utf-8")
    vx = pj / "analysis" / "vx.md"
    vx.write_text(base, encoding="utf-8")
    raw = pj / "data" / "_raw_fc" / "a.txt"
    log = pj / "journal" / "fetch_log.jsonl"
    log.write_text(json.dumps({"url": "https://example.org/visit", "chars": 50, "raw_path": "data/_raw_fc/a.txt"}) + "\n", encoding="utf-8")
    raw.write_text("Museum hours: Open daily 9 am. Adults $30. Free entry June 1 for everyone.", encoding="utf-8")
    e, w, _, _, _ = lint(vx, pj / "analysis" / "claims.md", [])
    res["x 보관 원문에 인용이 있으면 통과"] = not any("보관 원문에 없다" in x for x in e)
    raw.write_text("Museum hours: opens at ten. Adults $30.", encoding="utf-8")
    e, w, _, _, _ = lint(vx, pj / "analysis" / "claims.md", [])
    res["x 보관 원문에 없는 MATCH 인용 적발"] = any("보관 원문에 없다" in x for x in e)
    log.write_text(json.dumps({"url": "https://example.org/visit", "chars": 50}) + "\n", encoding="utf-8")
    e, w, _, _, _ = lint(vx, pj / "analysis" / "claims.md", [])
    res["x 보관 원문이 없으면 경고만"] = not any("보관 원문에 없다" in x for x in e) and any("보관 원문이 없어" in x for x in w)
    for k, v in res.items():
        print("  %s %s" % ("✓" if v else "✗", k))
    return all(res.values())


def selftest_261014(dp: Path) -> bool:
    """(e′)(l)(o)(p)(r)(n) — 통과·실패 사례를 짝으로. 실패 사례는 261013·261014 감사가 실제로 잡은 형태."""
    res = {}

    def run(name, text):
        (dp / (name + ".md")).write_text(text, encoding="utf-8")
        e, w, _, _, _ = lint(dp / (name + ".md"), dp / "claims.md", [])
        return e, w

    base = SAMPLE_VERDICT
    # (l) 단서 탈락 — 표기 없음 / 교정문에 단서 없음 / 교정문에 단서 있음
    cond = base.replace("| MISMATCH — 원문 $30 | T1 | \"Adults $30\" | https://example.org/visit | T1 자체 | 교정 1 |",
                        "| MISMATCH — 원문 $30 | T1 | \"Adults $30\" | https://example.org/visit | T1 자체 | 한정어 탈락(성인만) · 교정 1 |")
    e, _ = run("l1", cond)
    res["l 표기 없음 적발"] = any("단서 탈락 MISMATCH" in x for x in e)
    e, _ = run("l2", cond.replace("한정어 탈락(성인만)", "한정어 탈락(성인만) 단서: 'for adults'"))
    res["l 교정문에 단서 없음 적발"] = any("조치 문구에 없다" in x for x in e)
    e, _ = run("l3", cond.replace("한정어 탈락(성인만)", "한정어 탈락(성인만) 단서: 'for adults'")
               .replace('원문대로 "$30"', '원문대로 "$30 for adults"'))
    res["l 단서 담은 교정 통과"] = not e
    # (o)(p) MATCH 행
    e, _ = run("o", base.replace("| \"Open daily 9 am\" | https://example.org/visit | T1 자체 | — |",
                                 "| \"Open daily 9 am\" | https://example.org/visit | T1 자체 | 연장 여부는 재확인 목록 |"))
    res["o MATCH→재확인 목록 적발"] = any("재확인 목록" in x for x in e)
    p = base.replace("| \"Free entry June 1\" | https://example.org/visit | T1 자체 | — |",
                     "| \"Free entry June 1\" | https://example.org/visit | T1 자체 | 원문은 'June 1' 표기 |")
    e, _ = run("p1", p)
    res["p 차이 적은 MATCH 적발"] = any("같은 뜻" in x for x in e)
    e, _ = run("p2", p.replace("원문은 'June 1' 표기", "원문은 'June 1' 표기 — 같은 뜻: 06-01과 같은 날"))
    res["p 같은 뜻 이유 있으면 통과"] = not e
    # (r) 근거 칸
    e, _ = run("r", base.replace("| 원문대로 \"$30\" | https://example.org/visit |", "| 원문대로 \"$30\" | 박물관 안내 — G1 표 |"))
    res["r 근거 칸 이름만 적발"] = any("근거 칸" in x for x in e)
    # (e′)(a) UNVERIFIABLE — 교정 없는 행은 🔵에 더하고, 없는 교정 번호는 적발
    u = base.replace("**MISMATCH 1건 · STALE 0건 · 🔴(비가역) 0건** · UNVERIFIABLE 0건 · MATCH 3건",
                     "**MISMATCH 1건 · STALE 0건 · 🔴(비가역) 0건** · UNVERIFIABLE 1건 · MATCH 2건") \
        .replace("MATCH 3 · MISMATCH 1 · STALE 0 · UNVERIFIABLE 0", "MATCH 2 · MISMATCH 1 · STALE 0 · UNVERIFIABLE 1") \
        .replace("| 무료입장일 | MATCH | T1 |", "| 무료입장일 | UNVERIFIABLE(검색 10:20 — 안내 없음) | T1 |")
    e, _ = run("u1", u)
    res["a 교정 없는 UNVERIFIABLE → 🔵 1 요구"] = any("게이트 4 심각도" in x and "🔵 1" in x for x in e)
    e, _ = run("u2", u.replace("| T1 자체 | — |\n\n## lens", "| T1 자체 | 교정 9 |\n\n## lens"))
    res["e′ 없는 교정 번호 적발"] = any("교정 9 행이 교정표에 없다" in x for x in e)
    # (n) 새 수치 — 경고만
    e, w = run("n", base.replace('원문대로 "$30"', '원문대로 "$30"(약 20% 인상)'))
    res["n 새 수치 경고(위반 아님)"] = not e and any("수치" in x for x in w)
    for k, v in res.items():
        print("  %s %s" % ("✓" if v else "✗", k))
    return all(res.values())


if __name__ == "__main__":
    sys.exit(main())
