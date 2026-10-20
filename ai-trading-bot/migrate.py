#!/usr/bin/env python3
"""일회성 이전 — journal/·config/·data/·signals/·analysis/의 원장·기록 파일을 저장소(trading.db) 한 파일로 옮긴다.

왜(2026-10-17 결정 · Phase3_데이터베이스화_설계_v1_0.md): 사실·데이터의 원본을 문서 안 문장에서 DB 행으로 옮긴다.
이전은 한 번뿐이고 되돌릴 수 있어야 한다 — 그래서 순서가 정해져 있다.

  --apply 순서
    1) 저장소가 이미 있으면 거부(--force면 옛 저장소를 보관 폴더로 옮기고 진행)
    2) 원장 원본을 `<root>/journal/_pre_db/<YYMMDD_HHMM>/`에 **복사**(해시 기록 · 아직 아무것도 지우지 않는다)
    3) 저장소를 만들고 전부 싣는다(아래 '싣는 것')
    4) 검사(doctor) — 내보내기 7개 + 검사 전용 4개가 보관본과 바이트 단위로 같은가 · 원장 파일마다 행 수가 맞는가 ·
       로그·배분은 다시 써 봐도 바이트가 같은가 · `PRAGMA foreign_key_check`가 비었는가
    5) 하나라도 다르면 멈춘다 — 원본은 그대로 두고 저장소는 보관 폴더로 치우고 첫 차이 위치를 보여 준다
    6) 통과하면 내보내기 7개를 읽기 전용 렌더본으로 쓰고, 내보내기가 아닌 journal 원장(22종)을 journal/에서 지운다
       (보관 폴더에 원본이 있다 · config 파일은 지우지 않는다)

  싣는 것
    판단 원장(논지·시나리오·지도·일정·배분·유니버스 범위/대기·기준가·현재 run·고점·재투자 대기) · jsonl 로그 16종 + 자산 곡선 ·
    설정(감시 목록·시총 상위·업종 대응표) · 출처 원장 1,833행(옛 모양 그대로 + 출처·run·종류·시효 칸) · 판정표 →
    ev_verdict(행마다 사실 id를 찾고 맞춘 방법은 match 칸 · 못 찾은 행은 ev_verdict_orphan) · 재료 파일 → mat_issue·mat_link·
    mat_ws_event · run 산출물(시그널·승인·체결·스냅숏) · 손실 판정 · 실전 점검표 · 감사 원장 복사 ·
    자기주식 보유 스냅숏(co_treasury_holding)

명령:
  python3 migrate.py --dry [--root DIR]                  # 분류·행 수 표만(아무것도 쓰지 않는다)
  python3 migrate.py --apply [--root DIR] [--force]
  python3 migrate.py --rollback <보관 폴더> [--root DIR]   # 보관본을 제자리로 · 저장소는 보관 폴더로 치운다

--root를 주면 그 폴더(아래에 journal/ config/ data/ signals/ analysis/)를 대상으로 한다. AITB_DB가 없으면
저장소는 `<root>/journal/trading.db`다. 실제 폴더(--root 없음)에 AITB_DB를 지정한 채 --apply는 거부한다
(임시 저장소로 실제 원장을 지우는 사고를 막는다).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).absolute().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402

FC_SCRIPTS = HERE.parent / ".claude" / "skills" / "fact-check" / "scripts"
if str(FC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FC_SCRIPTS))
try:
    import lint_verdict as lv  # noqa: E402  — 판정표 파서(parse_tables)를 그대로 쓴다
except ImportError:  # 판정표 이전만 건너뛴다(보고에 남는다)
    lv = None

KST = timezone(timedelta(hours=9))
I = re.I

# ───────────────────────────── 대상 파일 ─────────────────────────────
REAL_HERE = HERE                    # 실제 프로젝트 뿌리(db.py와 같은 폴더) — 실제 폴더 보호 판정·db 뿌리 되돌리기에 쓴다
EXPORT_REL = {n: p.relative_to(db.HERE).as_posix() for n, (p, _f) in db.EXPORTS.items()}
CHECK_REL = {n: p.relative_to(db.HERE).as_posix() for n, (p, _f) in db.CHECK_ONLY.items()}
LOG_REL = {n: f"journal/{fn}" for n, (_t, _a, fn) in db.LOGS.items()}
# 내보내기가 아닌 journal 원장 — 검사 통과 뒤 journal/에서 지운다(보관 폴더에 원본)
REMOVE_FIXED = ["journal/allocation.jsonl", "journal/calendar.json", "journal/current_run.json",
                "journal/position_peaks.json", "journal/scenarios.json", "journal/universe_pending.json"]
REMOVE_FIXED += [r for n, r in LOG_REL.items() if n != "feedback"]
CONFIG_LOADED = ["config/main_names.json", "config/sector_bridge.json", "config/benchmark.json"]
CONFIG_KEPT = ["config/limits.json", "config/universe_rules.json", "config/costs.json", "config/feedback_policy.json",
               "config/holidays.json", "config/dart_corp_codes.json"]

RUN_RX = re.compile(r"(\d{6})_(kr|us)(?![a-z])")


def _rid(stamp, mk) -> str:
    return db.run_id(stamp, mk)


def _date_of(stamp: str) -> str:
    """스탬프 '261016' → '2026-10-16'(형식 변환만 — 세션 날짜 규칙은 세지 않는다)."""
    return f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:6]}"


def _run_order(rid: str) -> tuple:
    st, mk = rid.split("_")
    return (st, 0 if mk == "kr" else 1)


def rd_json(root: Path, rel: str):
    p = root / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def rd_jsonl(root: Path, rel: str) -> list:
    p = root / rel
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def refill_files(root: Path) -> list:
    return sorted((root / "journal").glob("refill_*.json"))


def remove_list(root: Path) -> list:
    return REMOVE_FIXED + [p.relative_to(root).as_posix() for p in refill_files(root)]


def archive_list(root: Path) -> list:
    out = list(EXPORT_REL.values()) + remove_list(root) + CONFIG_LOADED
    return [r for r in dict.fromkeys(out) if (root / r).exists()]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ───────────────────────────── URL ─────────────────────────────
URL_RX = re.compile(r"https?://[^\s)\]|>\"'<]+")


def nurl(u: str) -> str:
    """출처 키(ev_source.url) — 조각(#) 제거 · utm_* 칸 제거 · 스킴·호스트 소문자. 나머지 질의는 원래 표기 그대로."""
    u = u.strip().rstrip(".,;")
    p = urlsplit(u)
    q = "&".join(seg for seg in p.query.split("&") if seg and not seg.split("=", 1)[0].lower().startswith("utm_"))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, q, ""))


def loose(u: str) -> str:
    """대조용 느슨한 키 — 스킴·www.·끝 '/'·질의 순서 무시(판정표 URL ↔ 출처 원장 URL)."""
    p = urlsplit(nurl(u))
    h = p.netloc[4:] if p.netloc.startswith("www.") else p.netloc
    q = "&".join(sorted(seg for seg in p.query.split("&") if seg))
    return h + p.path.rstrip("/") + ("?" + q if q else "")


def urls_in(text) -> list:
    return URL_RX.findall(str(text or ""))


# ───────────────────────────── 종목 이름표 ─────────────────────────────

def company_index(root: Path) -> list:
    """(바늘, 시장, 종목) — 감시 목록 이름·별칭 + 시총 상위 이름. 긴 이름부터 대조한다."""
    out = []
    wl = rd_json(root, "config/watchlist.json") or {}
    for m in ("KR", "US"):
        for r in wl.get(m) or []:
            for n in [r.get("name")] + list(r.get("aliases") or []):
                if isinstance(n, str):
                    out.append((n, m, str(r.get("ticker"))))
    mn = rd_json(root, "config/main_names.json") or {}
    for m in ("KR", "US"):
        for r in mn.get(m) or []:
            if isinstance(r, dict) and isinstance(r.get("name"), str):
                out.append((r["name"], m, str(r.get("ticker"))))
    seen, keep = set(), []
    for n, m, t in out:
        n = n.strip()
        latin = bool(re.fullmatch(r"[\x00-\x7f]+", n))
        if (latin and len(n) < 3) or (not latin and len(n) < 2) or (n, m, t) in seen:
            continue
        seen.add((n, m, t))
        keep.append((n, m, t, latin))
    keep.sort(key=lambda x: -len(x[0]))
    return keep


def companies_in(text: str, cidx: list) -> set:
    """글 안에 나온 종목 {(시장, 종목)} — 긴 이름이 먼저 자리를 차지한다(삼성전자 안의 '삼성'을 따로 세지 않는다)."""
    t = str(text or "")
    taken = [False] * len(t)
    hit = set()
    for n, m, tk, latin in cidx:
        rx = re.compile((r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])") if latin else re.escape(n))
        for mm in rx.finditer(t):
            if any(taken[mm.start():mm.end()]):
                continue
            for k in range(mm.start(), mm.end()):
                taken[k] = True
            hit.add((m, tk))
    return hit


def resolve_token(tok: str, cidx: list):
    """링크 원문 매칭 낱말 → ('company', 'KR:005930') 또는 ('keyword', 낱말)."""
    tl = tok.strip()
    hits = {(m, t) for n, m, t, latin in cidx if (n.lower() == tl.lower() if latin else n == tl)}
    if len(hits) == 1:
        m, t = next(iter(hits))
        return "company", f"{m}:{t}"
    return "keyword", tl


# ───────────────────────────── 출처 원장 분류 ─────────────────────────────
PTR = [re.compile(p, I) for p in (
    r"^\s*S\d+\s*$", r"^fact-check [^—]*—\s*클레임 [\d·, ]+ 대조 출처", r"^Batch .{0,40}correction source",
    r"^fact-check 교정이 노트에 들인 대조 링크", r"^fact-check ②-[AB] 판정표 보강 행이 인용한 출처",
    r"^Schedule/source link added by fact-check", r"^노트 인용 출처 — ")]
TOOL_SRC = re.compile(r"KIS|broker|base_rates|price_levels|levels\.py|market_map|ingest\.py|캡처|capture|tools_\d|"
                      r"regret\.py|priced|threepoint|account_status|stops\.py|intraday", I)
KIND_RULES = [
    ("analyst", re.compile(r"목표(주)?가|target price|price target|\bPT\b|투자의견|Overweight|Underweight|Outperform|"
                           r"Equal-?weight|(up|down)grade[sd]?\b|analyst target|Street-high|레이팅|"
                           r"(증권|투자증권|Securities).{0,30}(추정|전망|[Pp]review)", I)),
    ("guidance", re.compile(r"guidance|가이던스|\bguide\b|outlook for|forecast(s|ing)? .{0,20}(revenue|sales|EPS)|"
                            r"expects (revenue|sales|EPS)", I)),
    ("consensus", re.compile(r"consensus|컨센|에프앤가이드|FactSet|LSEG estimate|Visible Alpha|estimates? (of|for)\b", I)),
    ("order", re.compile(r"공급계약|단일판매|수주|\bPPA\b|contract (award|worth|valued)|order (worth|valued|for \$)|awarded a", I)),
    ("event", re.compile(r"to report .{0,40}results|will report|plans to report|to announce .{0,30}results|실적 발표(일| 예정)|"
                         r"컨퍼런스\s?콜|콘퍼런스\s?콜|conference call|earnings call|webcast|results (on|and call)|발표 예정|"
                         r"blackout|블랙아웃|\bD-\d+\b", I)),
    ("earnings", re.compile(r"(\b[1-4]Q\d{2}\b|\bQ[1-4]\b|FQ[1-4]|FY\d{2}|분기|반기|[1-4]Q).{0,80}"
                            r"(매출|영업이익|순이익|revenue|EPS|net income|NII|operating (profit|income)|gross margin|"
                            r"free cash flow|FCF)|(매출|영업이익|순이익|revenue|EPS|net income|NII).{0,40}"
                            r"(\b[1-4]Q\d{2}\b|분기|FQ[1-4])", I)),
    ("disclosure", re.compile(r"DART|공시|\b8-K\b|\b10-Q\b|\b10-K\b|\b6-K\b|Form 4|13F|20-F|대량보유|자기주식|자사주|"
                              r"증권신고서|사업보고서|반기보고서", I)),
    ("release", re.compile(r"\bCPI\b|\bPPI\b|JOLTS|payrolls|nonfarm|고용보고서|실업률|unemployment rate|\bGDP\b|\bPCE\b|"
                           r"소매판매|retail sales|\bISM\b|\bPMI\b|산업생산|industrial production|무역수지|수출입|"
                           r"jobless claims|실업수당|WPSR|import prices|수입물가|Beige Book|소비자심리|consumer sentiment|"
                           r"housing starts", I)),
    ("policy", re.compile(r"Section 232|tariff|관세|executive order|행정명령|\bEO\b|법안|bill (passed|signed)|regulation|규정|"
                          r"규제|FERC|금감원|금융위|기재부|sanction|제재|FOMC .{0,30}(인상|인하|동결|hike|cut|hold|raised|lowered)|"
                          r"기준금리|policy rate|rate decision|금통위", I)),
    ("commodity", re.compile(r"Brent|브렌트|\bWTI\b|유가|copper|구리|\bLME\b|ULSD|diesel|디젤|경유|crack|크랙|DRAM|NAND|"
                             r"HBM.{0,10}가격|gold|금값|natural gas|천연가스|\bLNG\b|정제마진|휘발유|gasoline|uranium|우라늄|"
                             r"polysilicon|폴리실리콘|lithium|리튬", I)),
    ("market_state", re.compile(r"KOSPI|코스피|코스닥|KOSDAQ|S&P\s?500|Nasdaq|나스닥|\bNDX\b|Russell|\bDow\b|10-year|10년물|"
                                r"2년물|30년물|yield|국채|FedWatch|Kalshi|Polymarket|순매수|순매도|\bVIX\b|\bOAS\b|spread|"
                                r"환율|USD/KRW|원/달러|원·달러|달러인덱스|DXY|closed? (at|up|down)|마감|종가|장중|intraday|"
                                r"board|보드", I)),
]
STATIC_KINDS = {"earnings", "disclosure", "order", "release", "event"}
DATED_KINDS = {"analyst", "guidance", "consensus", "policy", "commodity"}
INTERP_RX = re.compile(r"\[추정\]|\[자체계산\]|자체계산|\(계산:")
MONTHS = {m: i for i, ms in enumerate([("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
                                       ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
                                       ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
                                       ("dec", "december")], 1) for m in ms}
DATE_ISO = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
DATE_EN = re.compile(r"\b(Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sept?|September|"
                     r"Oct|October|Nov|November|Dec|December)\.?\s+(\d{1,2}),\s+(20\d{2})\b")
EOK = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)\s*억\s*원")
PCT_SALES = re.compile(r"매출(?:액)?(?:의|\s*대비)?\s*(\d+(?:\.\d+)?)\s*%")
RCPNO = re.compile(r"rcp[Nn]o=(\d{14})")


def placeholder_reason(s: dict):
    f = str(s.get("fact") or "").strip()
    if not f:
        return "빈 사실"
    if "등록 대기" in str(s.get("source") or ""):
        return "출처 등록 대기"
    if any(p.search(f) for p in PTR):
        return "대조 출처 포인터"
    return None


def classify(s: dict) -> tuple:
    """(kind, valid_class) — 결정론 규칙. 맞는 규칙이 없으면 legacy."""
    if placeholder_reason(s):
        return "placeholder", None
    f = str(s.get("fact") or "")
    if s.get("tier") == "data" or TOOL_SRC.search(str(s.get("source") or "")):
        k = "market_state"
    else:
        k = next((n for n, rx in KIND_RULES if rx.search(f)), "legacy")
    if INTERP_RX.search(f):
        return k, "interp"
    if k in STATIC_KINDS:
        return k, "static"
    if k in DATED_KINDS:
        return k, "dated"
    if k == "market_state":
        return k, "live"
    return k, None


def _full_dates(text: str) -> set:
    out = {f"{y}-{mo}-{d}" for y, mo, d in DATE_ISO.findall(text)}
    for mon, d, y in DATE_EN.findall(text):
        out.add(f"{y}-{MONTHS[mon.lower().rstrip('.')]:02d}-{int(d):02d}")
    return out


def typed_row(s: dict, kind: str, cidx: list):
    """규칙으로 **확실히** 뽑히는 것만 상세 행으로(표, 칸). 아니면 None — 숫자를 짐작하지 않는다."""
    f = str(s.get("fact") or "")
    url = (urls_in(s.get("url")) or [None])[0]
    if kind == "event":
        ds = _full_dates(f)
        cs = companies_in(f, cidx)
        if len(ds) == 1 and len(cs) == 1:
            m, t = next(iter(cs))
            ek = "earnings" if re.search(r"results|earnings|실적|conference call|콘퍼런스|컨퍼런스", f, I) else "event"
            return "co_event", {"ticker": t, "market": m, "kind": ek, "date": next(iter(ds))}
    if kind == "order" and re.search(r"공급계약|단일판매", f) and ("dart" in str(url or "").lower() or "DART" in str(s.get("source") or "")):
        amts = EOK.findall(f)
        cs = {x for x in companies_in(f, cidx) if x[0] == "KR"}
        if len(amts) == 1 and len(cs) == 1:
            m, t = next(iter(cs))
            v = float(amts[0].replace(",", "")) * 1e8
            pct = PCT_SALES.findall(f)
            rc = RCPNO.search(str(s.get("url") or ""))
            return "co_order", {"ticker": t, "market": m, "amount": int(v) if v.is_integer() else v, "currency": "KRW",
                                "pct_of_sales": float(pct[0]) if len(pct) == 1 else None,
                                "announced_at": _date_of(rc.group(1)[2:8]) if rc else None}
    if kind == "disclosure":
        rcs = set(RCPNO.findall(str(s.get("url") or "")))
        cs = {x for x in companies_in(f, cidx) if x[0] == "KR"}
        if len(rcs) == 1 and len(cs) == 1:
            m, t = next(iter(cs))
            rc = next(iter(rcs))
            return "co_disclosure", {"ticker": t, "market": m, "doc_no": rc, "filed_at": _date_of(rc[2:8]), "url": url}
    return None


def parse_sources(root: Path) -> dict:
    doc = rd_json(root, "journal/sources.json") or {"schema_version": "1.1", "sources": []}
    rows = doc.get("sources") or []
    byrun = defaultdict(list)
    for s in rows:
        for lab in s.get("runs") or []:
            byrun[lab].append(s)
    smap = {}
    for lab, rs in byrun.items():
        for i, s in enumerate(rs, 1):
            smap[(lab, i)] = s["id"]
    return {"doc": doc, "rows": rows, "byrun": byrun, "smap": smap, "byid": {s["id"]: s for s in rows}}


def label_run(lab: str):
    m = re.match(r"^(kr|us):(\d{6})$", str(lab))
    return _rid(m.group(2), m.group(1)) if m else None


def fact_plan(src: dict, cidx: list) -> dict:
    """사실마다 kind·valid_class·출처 키·run·상세 행 — 파일만으로 계산(--dry도 같은 값)."""
    out = {}
    for s in src["rows"]:
        kind, vc = classify(s)
        us = urls_in(s.get("url"))
        tr = typed_row(s, kind, cidx) if kind != "placeholder" else None
        runs = [label_run(x) for x in s.get("runs") or []]
        out[s["id"]] = {"kind": kind, "valid_class": vc, "urls": [nurl(u) for u in us], "typed": tr,
                        "placeholder": placeholder_reason(s), "runs": runs, "labels": list(s.get("runs") or [])}
    return out


# ───────────────────────────── 판정표 ─────────────────────────────
VFILE_RX = re.compile(r"^AI클레임_(\d{6})_(kr|us)(?:_(A|B|주문근거))?_판정\.md$")
STAG_RX = re.compile(r"\[S\s*(\d+(?:\s*[·,]\s*S?\s*\d+)*)\]")
NUM_RX = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
HEADER_ALIAS = [  # 옛 판정표 머리(260928·260929 US) → parse_tables가 아는 이름
    (re.compile(r"^claim$", I), "Original wording"), (re.compile(r"^클레임\(요지\)$"), "원 문구"),
    (re.compile(r"^verbatim source quote$", I), "Quote"), (re.compile(r"^원문 실제 문구"), "원문 인용"),
    (re.compile(r"^출처 · 티어$"), "URL"),
]


def _stags(text: str) -> set:
    out = set()
    for grp in STAG_RX.findall(str(text or "")):
        out |= {int(x) for x in re.findall(r"\d+", grp)}
    return out


def _nums(t: str) -> set:
    return {x.replace(",", "") for x in NUM_RX.findall(str(t or "")) if len(x.replace(",", "")) >= 2}


def _prefer_real(cands: set, byid: dict) -> set:
    real = {i for i in cands if not placeholder_reason(byid[i])}
    return real or cands


def _alias_headers(lines: list) -> list:
    out = list(lines)
    for i, ln in enumerate(out[:-1]):
        if ln.startswith("|") and re.match(r"^\|\s*:?-{2,}", out[i + 1]):
            cells = lv.split_row(ln)
            new = []
            for c in cells:
                for rx, rep in HEADER_ALIAS:
                    if rx.search(c.strip()):
                        c = rep
                        break
                new.append(c)
            out[i] = "| " + " | ".join(new) + " |"
    return out


def parse_verdicts(root: Path, src: dict) -> dict:
    res = {"files": {}, "rows": [], "unmatched": [], "fallback": [], "skipped": [], "methods": Counter()}
    if lv is None:
        res["skipped"].append("lint_verdict를 불러오지 못했다")
        return res
    smap, byid = src["smap"], src["byid"]
    for p in sorted((root / "analysis").glob("AI클레임_*_판정.md")):
        m = VFILE_RX.match(p.name)
        if not m:
            res["skipped"].append(p.name)
            continue
        st, mk, batch = m.groups()
        lab, rid = f"{mk}:{st}", _rid(st, mk)
        lines = p.read_text(encoding="utf-8").splitlines()
        vr, _cr, _g = lv.parse_tables(lines)
        if not vr:
            vr, _cr, _g = lv.parse_tables(_alias_headers(lines))
            if vr:
                res["fallback"].append(p.name)
        cp = root / "analysis" / (f"AI클레임_{st}_{mk}" + (f"_{batch}" if batch else "") + ".md")
        claims = lv.load_claims(cp)[0] if cp.exists() else {}
        fu = defaultdict(set)
        for s in src["byrun"].get(lab, []):
            for u in urls_in(s.get("url")):
                fu[loose(u)].add(s["id"])
        gu = defaultdict(set)
        if not res.get("_gu"):
            for s in src["rows"]:
                for u in urls_in(s.get("url")):
                    gu[loose(u)].add(s["id"])
            res["_gu"] = gu
        gu = res["_gu"]
        nfile = Counter()
        for r in vr:
            cells = r["cells"]
            vcell = lv.cell(r, "verdict")
            verdict = lv.verdict_of(vcell)
            orig, sub, note, ucell = lv.cell(r, "orig"), lv.cell(r, "sub"), lv.cell(r, "note"), lv.cell(r, "url")
            tcell = lv.cell(r, "tier") or ucell
            mt = re.search(r"T\s*([123])", tcell)
            tier = int(mt.group(1)) if mt else None
            base = {"file": f"analysis/{p.name}", "line": r["line"], "run_id": rid, "batch": batch, "verdict": verdict,
                    "tier": tier, "tier_cell": tcell[:40], "orig": orig, "sub": sub, "note": note, "url": ucell,
                    "group": r.get("group")}
            if not verdict:
                base["reason"] = "판정 칸을 읽지 못함"
                res["unmatched"].append(base)
                nfile["unmatched"] += 1
                continue
            blob = " ".join(cells)
            ids = set(re.findall(r"SRC-\d{4,}", blob)) & set(byid)
            us = [loose(u) for u in urls_in(ucell)]
            cu = set().union(*[fu.get(u, set()) for u in us]) if us else set()
            cs = {smap[(lab, n)] for n in _stags(orig) | _stags(sub) | _stags(note) if (lab, n) in smap}
            cnos = [int(x) for x in re.findall(r"(?:클레임|claim)\s*(\d+)", orig, I)]
            csc, cu2 = set(), set()
            for n in cnos:
                ct = claims.get(n, "")
                csc |= {smap[(lab, k)] for k in _stags(ct) if (lab, k) in smap}
                for u in urls_in(ct):
                    cu2 |= fu.get(loose(u), set())
            # 같은 URL에 실제 사실과 fact-check 포인터 행(자리표시)이 같이 있으면 실제 사실을 고른다
            cu, cs, csc, cu2 = _prefer_real(cu, byid), _prefer_real(cs, byid), _prefer_real(csc, byid), _prefer_real(cu2, byid)
            fid, how = None, None
            if len(ids) == 1:
                fid, how = next(iter(ids)), "id"
            elif len(ids) > 1 and len(ids & cu) == 1:
                fid, how = next(iter(ids & cu)), "id+url"
            elif len(cu) == 1:
                fid, how = next(iter(cu)), "url"
            elif cu and len(cu & cs) == 1:
                fid, how = next(iter(cu & cs)), "url+S"
            elif cu and len(cu & csc) == 1:
                fid, how = next(iter(cu & csc)), "url+S(클레임)"
            elif not cu and len(cs) == 1:
                fid, how = next(iter(cs)), "S"
            elif not cu and len(csc) == 1:
                fid, how = next(iter(csc)), "S(클레임)"
            elif not cu and len(cu2) == 1:
                fid, how = next(iter(cu2)), "url(클레임)"
            elif cu:
                rn = _nums(" ".join((sub, orig, lv.cell(r, "quote"))))
                sc = sorted(((len(rn & _nums(str(byid[i].get("fact")) + " " + str(byid[i].get("quote") or ""))), i)
                             for i in cu), reverse=True)
                if sc[0][0] > 0 and (len(sc) == 1 or sc[0][0] > sc[1][0]):
                    fid, how = sc[0][1], "url+수치"
            if fid:
                mm = lv.SUPPORT_RE.search(note) if hasattr(lv, "SUPPORT_RE") else None
                res["rows"].append({**base, "fact_id": fid, "how": how, "support": mm.group(0) if mm else None})
                res["methods"][how] += 1
                nfile["matched"] += 1
                continue
            if cu:
                base["reason"], base["candidates"] = "같은 run·같은 URL 사실이 여럿", sorted(cu)[:8]
            elif us:
                g = set().union(*[gu.get(u, set()) for u in us])
                base["reason"] = "URL이 다른 run의 사실에만 있음" if g else "URL이 출처 원장에 없음"
                base["candidates"] = sorted(g)[:8]
            elif "material_" in ucell or "data/" in ucell:
                base["reason"] = "재료 파일 인용(출처 원장 행 없음)"
            else:
                base["reason"] = "URL·S 번호·사실 id 없음"
            res["unmatched"].append(base)
            nfile["unmatched"] += 1
        res["files"][p.name] = {"rows": len(vr), **nfile}
    res.pop("_gu", None)
    return res


# ───────────────────────────── 재료 ─────────────────────────────
ISSUE_RX = re.compile(r"^## \[(?P<topic>[^\]]+)\]\s*(?P<source>.+?)\s+—\s+(?P<day>\d{4}/\d{2}/\d{2})\s*$")
LINKS_RX = re.compile(r"^### 이 발행분의 링크\s*\((\d+)\)")
LINK_RX = re.compile(r"^- \[(?P<title>.*)\] (?P<url>https?://\S+)\s*$")
LWB_RX = re.compile(r"^### \[(?P<title>.*)\]\((?P<url>https?://[^\s)]+)\)\s*(?:←\s*(?P<m>.*))?$")
EVENT_RX = re.compile(r"^- \*\*(?P<h>.+)\*\*\s*(?:\((?P<meta>.*)\))?\s*$")
MAT_RX = re.compile(r"^material_(\d{6})R?_(kr|us)(?:_\d+)?\.md$")


def material_files(root: Path) -> list:
    d = root / "data"
    main = sorted(p for p in d.glob("material_*.md") if MAT_RX.match(p.name))
    sup = sorted(p for p in (d / "_superseded").glob("material_*.md") if MAT_RX.match(p.name))
    return main + sup


def parse_material(path: Path, root: Path) -> dict:
    m = MAT_RX.match(path.name)
    rid = _rid(m.group(1), m.group(2))
    rel = path.relative_to(root).as_posix()
    out = {"rel": rel, "run_id": rid, "issues": [], "ws_note": None, "events": [], "bodies": []}
    mode, cur, ev, lb = None, None, None, None

    def close_issue():
        nonlocal cur
        if cur is not None:
            cur["body"] = "\n".join(cur.pop("_b")).strip()
            out["issues"].append(cur)
        cur = None

    def close_body():
        nonlocal lb
        if lb is not None:
            lb["body"] = "\n".join(lb.pop("_b")).strip()
            out["bodies"].append(lb)
        lb = None

    for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        mi = ISSUE_RX.match(ln)
        if mi:
            close_issue()
            close_body()
            mode = "issue"
            cur = {"topic": mi.group("topic"), "source": mi.group("source").strip(), "day": mi.group("day").replace("/", "-"),
                   "line": i, "links": [], "_b": [], "_in_links": False}
            continue
        if ln.startswith("## ") or ln.startswith("# "):
            close_issue()
            close_body()
            mode = "ws" if ln.startswith("## 세상공부") else "lw" if ln.startswith("## 링크 원문") else None
            ev = None
            continue
        if mode == "issue":
            if LINKS_RX.match(ln):
                cur["_in_links"] = True
                continue
            if cur["_in_links"]:
                ml = LINK_RX.match(ln)
                if ml:
                    cur["links"].append((ml.group("title"), ml.group("url")))
                continue
            cur["_b"].append(ln)
        elif mode == "ws":
            mn = re.search(r"출처 `([^`]+)`", ln)
            if mn and out["ws_note"] is None and not ln.startswith("-"):
                out["ws_note"] = mn.group(1)
            if ln.startswith("- "):
                me = EVENT_RX.match(ln)
                ev = {"headline": (me.group("h") if me else ln[2:]).strip(), "meta": (me.group("meta") if me else None),
                      "line": i, "lines": []}
                out["events"].append(ev)
            elif ev is not None and re.match(r"^\s+- ", ln):
                tag = "[AI]" if "[AI]" in ln else "[추정]" if "[추정]" in ln else "근거"
                ev["lines"].append((ln.rstrip(), tag, i))
        elif mode == "lw":
            mb = LWB_RX.match(ln)
            if mb:
                close_body()
                lb = {"title": mb.group("title"), "url": mb.group("url"), "line": i,
                      "match": re.findall(r"`([^`]+)`", mb.group("m") or ""), "_b": []}
                continue
            if ln.startswith("### "):
                close_body()
                continue
            if lb is not None:
                lb["_b"].append(ln)
    close_issue()
    close_body()
    for it in out["issues"]:
        it.pop("_in_links", None)
        it["read_ref"] = it["body"].startswith("*(이미 읽음")
    return out


def material_plan(root: Path) -> dict:
    """파일만으로 센 재료 수(중복은 본문 해시로 한 번만)."""
    seen, ev_seen = set(), set()
    n = Counter()
    for p in material_files(root):
        pm = parse_material(p, root)
        n["files"] += 1
        for it in pm["issues"]:
            if it["read_ref"]:
                n["read_ref"] += 1
                continue
            if not it["body"]:
                n["empty"] += 1
                continue
            h = hashlib.sha256(it["body"].encode("utf-8")).hexdigest()
            if h in seen:
                n["dup_issue"] += 1
                continue
            seen.add(h)
            n["issues"] += 1
            n["links"] += len(it["links"])
        for e in pm["events"]:
            for key in [(pm["ws_note"], e["headline"], e["meta"])] + [(pm["ws_note"], e["headline"], x[0].strip()) for x in e["lines"]]:
                h = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()
                if h not in ev_seen:
                    ev_seen.add(h)
                    n["ws_rows"] += 1
        n["link_bodies"] += len(pm["bodies"])
    return dict(n)


# ───────────────────────────── run 집합 ─────────────────────────────

def runs_seen(root: Path, src: dict) -> dict:
    """run_id → {stamp, market, 출처}. 파일 이름·원장 칸에서 본 모든 run."""
    runs = {}

    def add(st, mk, why):
        if not (st and re.fullmatch(r"\d{6}", str(st)) and str(mk).lower() in ("kr", "us")):
            return
        rid = _rid(st, mk)
        runs.setdefault(rid, {"stamp": st, "market": str(mk).lower(), "seen": set()})["seen"].add(why)

    pats = [("signals", "signal_*.json", "signal"), ("signals", "approved_*.json", "approved"),
            ("journal", "trades_*.json", "trades"), ("data", "snapshot_*.json", "snapshot"),
            ("data", "material_*.md", "material"), ("analysis", "분석노트_*.md", "note"),
            ("analysis", "AI클레임_*_판정.md", "verdict"), ("journal", "refill_*.json", "refill"),
            ("journal", "worklog_*.md", "worklog")]
    for d, g, why in pats:
        for p in (root / d).glob(g):
            mm = re.match(r"^[^_]+_(\d{6})_(kr|us)(?![a-z])", p.name)
            if mm:
                add(mm.group(1), mm.group(2), why)
    for s in src["rows"]:
        for lab in s.get("runs") or []:
            mm = re.match(r"^(kr|us):(\d{6})$", str(lab))
            if mm:
                add(mm.group(2), mm.group(1), "sources")
    for r in rd_jsonl(root, "journal/allocation.jsonl"):
        add(r.get("stamp"), r.get("market"), "allocation")
    for r in rd_jsonl(root, "journal/sessions.jsonl"):
        add(r.get("stamp"), r.get("market"), "sessions")
    cr = rd_json(root, "journal/current_run.json")
    if isinstance(cr, dict):
        add(cr.get("stamp"), cr.get("market"), "current_run")
    return runs


def artifact_runs(root: Path) -> list:
    out = set()
    for d, g in (("signals", "signal_*.json"), ("signals", "approved_*.json"), ("journal", "trades_*.json")):
        for p in (root / d).glob(g):
            mm = re.match(r"^[^_]+_(\d{6})_(kr|us)(?![a-z])", p.name)
            if mm:
                out.add((mm.group(1), mm.group(2)))
    return sorted(out)


SNAP_RX = re.compile(r"^snapshot_(\d{6})_(kr|us)(?:_(.+))?\.json$")


# ───────────────────────────── 운영 기록 ─────────────────────────────

def parse_real_checks(root: Path) -> list:
    out = []
    for p in sorted((root / "data" / "run_evidence").glob("real_check_*.md")):
        mm = re.match(r"^real_check_(\d{6})_(kr|us)\.md$", p.name)
        if not mm:
            continue
        txt = p.read_text(encoding="utf-8")
        mt = re.search(r"\((\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) KST", txt)
        at = f"{mt.group(1)}T{mt.group(2)}:00+09:00" if mt else None
        lines = txt.splitlines()
        items = []
        for i, ln in enumerate(lines[:-1]):
            if ln.startswith("| 항목 |") and re.match(r"^\|\s*:?-{2,}", lines[i + 1]):
                for row in lines[i + 2:]:
                    if not row.startswith("|"):
                        break
                    cells = [c.strip() for c in row.strip().strip("|").split("|")]
                    if len(cells) >= 3:
                        items.append({"item": cells[0], "status": cells[1], "note": " | ".join(cells[2:])})
                break
        out.append({"file": p.relative_to(root).as_posix(), "run_id": _rid(mm.group(1), mm.group(2)), "at": at, "items": items})
    return out


def parse_loss_reviews(root: Path) -> list:
    out = []
    for p in sorted((root / "data" / "feedback").glob("손실판정_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({"file": p.relative_to(root).as_posix(), "doc": d})
    return out


# ───────────────────────────── 계획(파일만) ─────────────────────────────

def audit_ledger():
    """db.audit_mirror가 읽을 엔진 원장 — 예행·시험 저장소(AITB_DB)에서는 AITB_STEPGATE_LEDGER를 줄 때만(db 규칙과 같다)."""
    env = os.environ.get("AITB_STEPGATE_LEDGER")
    if env:
        return Path(env)
    if os.environ.get("AITB_DB"):
        return None
    return db.STEPGATE_DIR / "promotion_ledger.jsonl"


def plan(root: Path) -> dict:
    """싣기 전 분류·행 수 — --dry가 보여 주고, --apply가 검사 기준으로 쓴다."""
    src = parse_sources(root)
    cidx = company_index(root)
    fp = fact_plan(src, cidx)
    inv = []

    def row(cat, rel, n, table, fate):
        inv.append({"분류": cat, "파일": rel, "행": n, "대상": table, "이전 뒤": fate})

    th = rd_json(root, "journal/theses.json") or {}
    sc = rd_json(root, "journal/scenarios.json") or {}
    mm = rd_json(root, "journal/market_map.json") or {}
    cal = rd_json(root, "journal/calendar.json") or {}
    row("판단 원장", "journal/theses.json", len(th.get("theses") or []), "jd_thesis(+leg·fact·event)", "렌더본")
    row("판단 원장", "journal/scenarios.json", len(sc.get("scenarios") or []), "jd_scenario(+judgment·response·list)", "보관 뒤 삭제")
    row("판단 원장", "journal/market_map.json", f"축 {len(mm.get('axes') or [])} · 섹터 {len(mm.get('sectors') or [])}",
        "dim_axis·dim_sector(+자식)", "렌더본")
    row("판단 원장", "journal/calendar.json", len(cal.get("events") or []), "cal_event(+list)", "보관 뒤 삭제")
    row("판단 원장", "journal/allocation.jsonl", len(rd_jsonl(root, "journal/allocation.jsonl")), "jd_allocation", "보관 뒤 삭제")
    row("판단 원장", "journal/universe_coverage.json", len(rd_json(root, "journal/universe_coverage.json") or {}), "op_coverage", "렌더본")
    row("판단 원장", "journal/universe_pending.json", len(rd_json(root, "journal/universe_pending.json") or {}), "op_pending", "보관 뒤 삭제")
    row("판단 원장", "config/benchmark.json", len(rd_json(root, "config/benchmark.json") or {}), "op_benchmark_anchor", "파일 유지")
    row("판단 원장", "journal/current_run.json", 1 if (root / "journal/current_run.json").exists() else 0, "op_current_run", "보관 뒤 삭제")
    pk = rd_json(root, "journal/position_peaks.json") or {}
    row("판단 원장", "journal/position_peaks.json", sum(len(v) for k, v in pk.items() if isinstance(v, dict)),
        "ex_position_peak", "보관 뒤 삭제")
    for p in refill_files(root):
        row("판단 원장", p.relative_to(root).as_posix(), 1, "ex_refill", "보관 뒤 삭제")
    for n, rel in LOG_REL.items():
        row("로그", rel, len(rd_jsonl(root, rel)), db.LOGS[n][0], "렌더본" if n == "feedback" else "보관 뒤 삭제")
    row("로그", "journal/equity_curve.jsonl", len(rd_jsonl(root, "journal/equity_curve.jsonl")),
        "ex_equity_point(+position)", "렌더본")
    wl = rd_json(root, "config/watchlist.json") or {}
    row("설정", "config/watchlist.json", f"KR {len(wl.get('KR') or [])} · US {len(wl.get('US') or [])}",
        "dim_company(in_watchlist)", "렌더본")
    mn = rd_json(root, "config/main_names.json") or {}
    row("설정", "config/main_names.json", f"KR {len(mn.get('KR') or [])} · US {len(mn.get('US') or [])}",
        "dim_company.main_rank + sys_docmeta", "파일 유지")
    sb = rd_json(root, "config/sector_bridge.json") or {}
    row("설정", "config/sector_bridge.json", f"live {len(sb.get('links') or [])} · retired {len(sb.get('retired_links') or [])} · "
        f"gap {len(sb.get('kr_without_us') or []) + len(sb.get('us_without_kr') or [])}",
        "dim_sector_link(db.sector_bridge_save) + sys_docmeta", "파일 유지")
    for rel in CONFIG_KEPT:
        if (root / rel).exists():
            row("설정(이전 안 함)", rel, "—", "—", "파일 유지")
    row("사실", "journal/sources.json", len(src["rows"]), "ev_fact(+run_label·run) · ev_source · co_* 상세", "렌더본")

    vd = parse_verdicts(root, src)
    row("판정표", f"analysis/AI클레임_*_판정.md ({len(vd['files'])}개)", sum(f["rows"] for f in vd["files"].values()),
        f"ev_verdict(맞춘 행 {len(vd['rows'])} · 못 맞춘 행 {len(vd['unmatched'])} → ev_verdict_orphan)", "파일 유지")
    mp = material_plan(root)
    row("재료", f"data/material_*.md + _superseded ({mp.get('files', 0)}개)",
        f"발행분 {mp.get('issues', 0)} · 링크 {mp.get('links', 0)} · 세상공부 {mp.get('ws_rows', 0)} · 링크 원문 {mp.get('link_bodies', 0)}",
        "mat_issue·mat_link·mat_ws_event·mat_link_match", "파일 유지")
    snaps = sorted(p for p in (root / "data").glob("snapshot_*.json") if SNAP_RX.match(p.name))
    ar = artifact_runs(root)
    row("집행", f"signals/·journal/trades_* ({len(ar)} run)", len(ar), "ex_signal·ex_proposal·ex_approval·ex_order·ex_fill", "파일 유지")
    row("집행", f"data/snapshot_*.json ({len(snaps)}개)", len(snaps), "ex_balance·ex_position(phase=capture…)", "파일 유지")
    rs = runs_seen(root, src)
    row("집행", "(파일 이름·원장에서 본 run)", len(rs), "ex_run", "—")
    lr = parse_loss_reviews(root)
    row("운영", "data/feedback/손실판정_*.json", sum(len(x["doc"].get("rows") or []) for x in lr), "op_loss_review", "파일 유지")
    rc = parse_real_checks(root)
    row("운영", "data/run_evidence/real_check_*.md", sum(len(x["items"]) for x in rc), "op_real_check", "파일 유지")
    led = audit_ledger()
    row("운영", f"{led or '_stepgate/promotion_ledger.jsonl'}(읽기만)",
        sum(1 for ln in led.read_text(encoding="utf-8").splitlines() if ln.strip()) if led and led.exists() else 0,
        "op_audit(run별 audit_mirror)" if led else "— 예행 저장소(AITB_DB)라 복사 안 함(AITB_STEPGATE_LEDGER로 지정 가능)",
        "엔진 원장 그대로")
    ts = rd_json(root, "data/treasury_scan.json")
    ts = ts if isinstance(ts, dict) else {}
    nts = sum(1 for v in ts.values() if isinstance(v, dict) and v.get("stlm_dt"))
    row("종목", "data/treasury_scan.json", f"{len(ts)} (보유 {nts} · 조회 오류 {len(ts) - nts})",
        "co_treasury_holding(보유 스냅숏) · 오류 행은 sys_docmeta", "파일 유지")

    kinds = Counter((v["kind"], v["valid_class"]) for v in fp.values())
    raw_urls = {u for s in src["rows"] for u in urls_in(s.get("url"))}
    norm_urls = {nurl(u) for u in raw_urls}
    return {"inventory": inv, "src": src, "facts": fp, "kinds": kinds, "verdicts": vd, "materials": mp,
            "runs": rs, "artifact_runs": ar, "snapshots": snaps, "loss": lr, "real_check": rc, "cidx": cidx,
            "urls": {"raw": len(raw_urls), "normalized": len(norm_urls)}}


# ───────────────────────────── 싣기 ─────────────────────────────

def _empty_db(c) -> bool:
    return all(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
               for t in ("ev_fact", "jd_thesis", "jd_scenario", "dim_axis", "ex_run", "op_fetch"))


def _insert_log(c, table: str, r: dict) -> None:
    ts, mk, st, rid = db._log_cols(r)
    c.execute(f"INSERT INTO {table} (ts, market, stamp, rid, body) VALUES (?,?,?,?,?)",
              (ts, mk, st, rid, json.dumps(r, ensure_ascii=False)))


def load_judgment(root: Path, rep: dict) -> None:
    th = rd_json(root, "journal/theses.json")
    if th is not None:
        with db.tx():
            db.docmeta_put("theses", {**{k: v for k, v in th.items() if k != "theses"}, "_ko": list(th)})
            for i, t in enumerate(th.get("theses") or []):
                db.theses.put(t, ord_=i)
    sc = rd_json(root, "journal/scenarios.json")
    if sc is not None:
        with db.tx():
            db.docmeta_put("scenarios", {**{k: v for k, v in sc.items() if k != "scenarios"}, "_ko": list(sc)})
            for i, t in enumerate(sc.get("scenarios") or []):
                db.scenarios.put(t, ord_=i)
    mm = rd_json(root, "journal/market_map.json")
    if mm is not None:
        db.market_map_save(mm)
    cal = rd_json(root, "journal/calendar.json")
    if cal is not None:
        db.calendar_save(cal)
    with db.tx():
        for i, r in enumerate(rd_jsonl(root, "journal/allocation.jsonl")):
            db.STORES["allocation"].put(r, ord_=i)
    for rel, table in (("journal/universe_coverage.json", "op_coverage"), ("journal/universe_pending.json", "op_pending"),
                       ("config/benchmark.json", "op_benchmark_anchor")):
        d = rd_json(root, rel)
        if isinstance(d, dict):
            db.kv_save(table, d)
    cr = rd_json(root, "journal/current_run.json")
    if cr is not None:
        with db.tx():
            db.current_run_write(cr)
    pk = rd_json(root, "journal/position_peaks.json")
    if isinstance(pk, dict):
        extra = [k for k in pk if k not in ("_schema", "KR", "US")]
        if extra or pk.get("_schema") != 2:
            rep["warnings"].append(f"position_peaks.json에 렌더가 모르는 칸 {extra} · _schema={pk.get('_schema')}")
        with db.tx():
            for m in ("KR", "US"):
                for t, v in (pk.get(m) or {}).items():
                    db.peaks_set(m, t, v.get("peak_price"), v.get("peak_at"))
    with db.tx():
        for p in refill_files(root):
            mm2 = re.match(r"^refill_(\d{6})_(kr|us)\.json$", p.name)
            d = json.loads(p.read_text(encoding="utf-8"))
            if not mm2:
                rep["warnings"].append(f"재투자 대기 파일 이름 규칙 밖: {p.name} — 키를 파일 이름으로 둘 수 없어 건너뜀")
                continue
            if str(d.get("stamp")) != mm2.group(1) or str(d.get("market", "")).lower() != mm2.group(2):
                rep["warnings"].append(f"{p.name}: 파일 이름과 본문 stamp/market이 다르다(키는 파일 이름)")
            db.kv_put("ex_refill", _rid(mm2.group(1), mm2.group(2)), d)


def load_logs(root: Path) -> None:
    c = db.connect()
    with db.tx():
        for n, (table, _a, _f) in db.LOGS.items():
            for r in rd_jsonl(root, LOG_REL[n]):
                _insert_log(c, table, r)
        for i, r in enumerate(rd_jsonl(root, "journal/equity_curve.jsonl")):
            db._insert_rowdoc(db.SPEC["equity"], r, i)


def load_config(root: Path, rep: dict) -> None:
    wl = rd_json(root, "config/watchlist.json")
    if wl is not None:
        db.watchlist_save(wl)
    mn = rd_json(root, "config/main_names.json")
    if isinstance(mn, dict):
        with db.tx():
            db.docmeta_put("main_names", {**{k: v for k, v in mn.items() if k not in ("KR", "US")}, "_ko": list(mn)})
            for m in ("KR", "US"):
                if isinstance(mn.get(m), list):
                    db.main_names_refresh(m, mn[m], mn.get("as_of") or "", mn.get("source") or "")
    sb = rd_json(root, "config/sector_bridge.json")
    if isinstance(sb, dict):
        db.sector_bridge_save(sb)        # links → live · retired_links → retired · 한쪽만 있는 업종 → gap · 나머지 키는 docmeta


def load_facts(root: Path, pl: dict, rep: dict) -> None:
    src, fp = pl["src"], pl["facts"]
    c = db.connect()
    doc = src["doc"]
    with db.tx():
        db.docmeta_put("sources", {**{k: v for k, v in doc.items() if k != "sources"}, "_ko": list(doc)})
        for i, f in enumerate(src["rows"]):
            db.facts_store.put(f, ord_=i)
    # 출처(정규화 URL 하나 = 한 행) — 같은 URL 사실들이 한 출처를 가리킨다
    fetch_raw = {}
    for r in rd_jsonl(root, "journal/fetch_log.jsonl"):
        if r.get("raw_path") and r.get("url"):
            fetch_raw[nurl(str(r["url"]))] = r["raw_path"]
    by_url = defaultdict(list)
    for s in src["rows"]:
        for u in fp[s["id"]]["urls"]:
            by_url[u].append(s)
    tier_conflict = 0
    with db.tx():
        sid = {}
        for u, ss in by_url.items():
            tiers = {s.get("tier") for s in ss if isinstance(s.get("tier"), int) and not isinstance(s.get("tier"), bool)}
            if len(tiers) > 1:
                tier_conflict += 1
            rets = sorted(str(s.get("retrieved")) for s in ss if s.get("retrieved"))
            cur = c.execute("INSERT INTO ev_source (url, publisher, tier, retrieved_at, raw_path, title) VALUES (?,?,?,?,?,?)",
                            (u, None, next(iter(tiers)) if len(tiers) == 1 else None, rets[0] if rets else None,
                             fetch_raw.get(u), None))
            sid[u] = cur.lastrowid
        typed = Counter()
        for s in src["rows"]:
            p = fp[s["id"]]
            runs = [r for r in p["runs"] if r]
            subj = None
            if p["typed"]:
                table, cols = p["typed"]
                subj = ("company", f"{cols['market']}:{cols['ticker']}", cols["market"])
            c.execute("UPDATE ev_fact SET kind=?, valid_class=?, source_id=?, created_run=?, subject_type=?, subject_id=?, market=?"
                      " WHERE id=?",
                      (p["kind"], p["valid_class"], sid.get(p["urls"][0]) if p["urls"] else None, runs[0] if runs else None,
                       subj[0] if subj else None, subj[1] if subj else None, subj[2] if subj else None, s["id"]))
            for k, lab in enumerate(p["labels"]):
                rid = label_run(lab)
                if not rid:
                    continue
                c.execute("INSERT OR IGNORE INTO ev_fact_run (fact_id, run_id, s_no, added_at) VALUES (?,?,NULL,?)",
                          (s["id"], rid, s.get("retrieved") if k == 0 else None))
            if p["typed"]:
                table, cols = p["typed"]
                cols = {"fact_id": s["id"], **{k: v for k, v in cols.items() if v is not None or k in ("market",)}}
                c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
                typed[table] += 1
        # S 번호 — 그 run 태그가 붙은 행의 원장 순서(srcledger render의 S 칸과 같다 · 노트 §12와 774/774 일치 확인)
        for (lab, n), fid in src["smap"].items():
            rid = label_run(lab)
            if rid:
                c.execute("UPDATE ev_fact_run SET s_no=? WHERE fact_id=? AND run_id=?", (n, fid, rid))
    rep["facts"] = {"rows": len(src["rows"]), "sources": len(by_url), "tier_conflict_sources": tier_conflict,
                    "no_url": sum(1 for v in fp.values() if not v["urls"]),
                    "multi_url": sum(1 for v in fp.values() if len(v["urls"]) > 1), "typed": dict(typed),
                    "kinds": {f"{k}/{vc}": n for (k, vc), n in pl["kinds"].most_common()},
                    "placeholders": [{"id": i, "why": v["placeholder"]} for i, v in fp.items() if v["placeholder"]]}


def load_materials(root: Path, pl: dict, rep: dict) -> None:
    c = db.connect()
    cidx = pl["cidx"]
    canon = {}          # 본문 해시 → (issue_id, {정규 URL: link_id})
    n = Counter()
    with db.tx():
        for p in material_files(root):
            pm = parse_material(p, root)
            rid = pm["run_id"]
            filelinks = {}
            for it in pm["issues"]:
                if it["read_ref"]:
                    n["read_ref"] += 1
                    continue
                if not it["body"]:
                    n["empty"] += 1
                    continue
                h = hashlib.sha256(it["body"].encode("utf-8")).hexdigest()
                if h in canon:
                    n["dup_issue"] += 1
                    filelinks.update(canon[h][1])
                    continue
                first = it["body"].splitlines()[0].strip()
                subject = first.strip("*").strip() if first.startswith("**") else None
                iid = c.execute("INSERT INTO mat_issue (run_id, sender, source, topic, day, received_at, subject, body, chars,"
                                " raw_path, spam_rescued, first_run, content_sha, legacy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                                (rid, None, it["source"], it["topic"], it["day"], None, subject, it["body"], len(it["body"]),
                                 f"{pm['rel']}#L{it['line']}", None, rid, h)).lastrowid
                links = {}
                for k, (title, url) in enumerate(it["links"]):
                    lid = c.execute("INSERT INTO mat_link (issue_id, ord, url, title, fetched, raw_path, chars, run_id, body)"
                                    " VALUES (?,?,?,?,0,NULL,NULL,?,NULL)", (iid, k, url, title, rid)).lastrowid
                    links.setdefault(nurl(url), lid)
                    n["links"] += 1
                canon[h] = (iid, links)
                filelinks.update(links)
                n["issues"] += 1
            for b in pm["bodies"]:
                u = nurl(b["url"])
                lid = filelinks.get(u)
                raw = f"{pm['rel']}#L{b['line']}"
                if lid is not None:
                    has = c.execute("SELECT body FROM mat_link WHERE id=?", (lid,)).fetchone()[0]
                    if has is None:
                        c.execute("UPDATE mat_link SET fetched=1, body=?, chars=?, raw_path=? WHERE id=?",
                                  (b["body"], len(b["body"]), raw, lid))
                        n["link_bodies_attached"] += 1
                    else:
                        n["link_bodies_dup"] += 1
                else:
                    lid = c.execute("INSERT INTO mat_link (issue_id, ord, url, title, fetched, raw_path, chars, run_id, body)"
                                    " VALUES (NULL,NULL,?,?,1,?,?,?,?)",
                                    (b["url"], b["title"], raw, len(b["body"]), rid, b["body"])).lastrowid
                    n["link_bodies_standalone"] += 1
                for tok in b["match"]:
                    st, sv = resolve_token(tok, cidx)
                    c.execute("INSERT OR IGNORE INTO mat_link_match (link_id, subject_type, subject_id) VALUES (?,?,?)", (lid, st, sv))
                    n["link_match"] += 1
            wsd = None
            if pm["ws_note"]:
                mw = re.search(r"_(\d{6})_", pm["ws_note"])
                wsd = _date_of(mw.group(1)) if mw else None
            for e in pm["events"]:
                rows = [(e["headline"], e["meta"], "사건", None)] + [(e["headline"], ln, tag, (urls_in(ln) or [None])[0])
                                                                   for ln, tag, _i in e["lines"]]
                for head, line, tag, url in rows:
                    key = (pm["ws_note"], head, line.strip() if isinstance(line, str) else line)
                    h = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()
                    cur = c.execute("INSERT OR IGNORE INTO mat_ws_event (ws_note, ws_date, headline, tag, evidence_line, url,"
                                    " subject_type, subject_id, run_id, content_sha) VALUES (?,?,?,?,?,?,NULL,NULL,?,?)",
                                    (pm["ws_note"], wsd, head, tag, line, url, rid, h))
                    n["ws_rows"] += cur.rowcount
                    if tag == "사건":
                        n["ws_events"] += cur.rowcount
        # 옛 사실의 재료 출처 — 사실 URL이 어느 발행분의 링크와 같으면 origin='issue'
        link_issue = {}
        for lid, iid, url in c.execute("SELECT id, issue_id, url FROM mat_link WHERE issue_id IS NOT NULL ORDER BY id"):
            link_issue.setdefault(nurl(url), iid)
        k = 0
        for fid, v in pl["facts"].items():
            iid = next((link_issue[u] for u in v["urls"] if u in link_issue), None)
            if iid is not None:
                c.execute("UPDATE ev_fact SET origin='issue', origin_id=? WHERE id=?", (str(iid), fid))
                k += 1
        n["facts_origin_issue"] = k
    rep["materials"] = dict(n)


def load_runs(root: Path, pl: dict, rep: dict) -> None:
    c = db.connect()
    sess = {}
    for r in rd_jsonl(root, "journal/sessions.jsonl"):
        if r.get("stamp") and r.get("market"):
            sess[_rid(r["stamp"], r["market"])] = r
    cr = rd_json(root, "journal/current_run.json") or {}
    snaps = {}
    for p in pl["snapshots"]:
        m = SNAP_RX.match(p.name)
        snaps.setdefault(_rid(m.group(1), m.group(2)), []).append((m.group(3), p))
    with db.tx():
        for rid, r in sorted(pl["runs"].items(), key=lambda x: _run_order(x[0])):
            s = sess.get(rid) or {}
            started, sdate = s.get("started_kst"), s.get("session_date")
            if isinstance(cr, dict) and _rid(str(cr.get("stamp")), str(cr.get("market"))) == rid:
                started, sdate = started or cr.get("started"), sdate or cr.get("session_date")
            mode, cap = None, None
            for suf, p in snaps.get(rid, []):
                if suf is None:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    mode, cap = d.get("account_mode"), d.get("generated_at")
            c.execute("INSERT INTO ex_run (run_id, stamp, market, account_mode, session_date, started, capture_at, note)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (rid, r["stamp"], r["market"], mode, sdate, started, cap, "이전(migrate.py) · 본 곳: " + ",".join(sorted(r["seen"]))))
    imp = Counter()
    errs = []
    missing = {}
    for st, mk in pl["artifact_runs"]:
        try:
            out = db.import_run_artifacts(st, mk, here=root)
            for k, v in out.items():
                if k == "missing_facts":     # 시그널이 가리킨 사실 id가 원장에 없다(근거 연결만 빠진다)
                    missing[_rid(st, mk)] = len(v)
                elif isinstance(v, (int, float)):
                    imp[k] += v
        except Exception as e:  # 한 run의 파일이 깨졌으면 기록하고 검사에서 실패시킨다
            errs.append(f"{_rid(st, mk)}: {type(e).__name__}: {e}")
    nb, npos, skip = 0, 0, []
    with db.tx():
        for rid, lst in snaps.items():
            st, mk = rid.split("_")
            for suf, p in lst:
                d = json.loads(p.read_text(encoding="utf-8"))
                bal = d.get("balance")
                if not isinstance(bal, dict):
                    skip.append(p.name)
                    continue
                phase = "capture" if suf is None else f"capture_{suf}"
                db.balance_put(st, mk, phase, bal, at=d.get("generated_at"))
                npos += db.positions_put(st, mk, phase, bal.get("positions") or [], at=d.get("generated_at"))
                nb += 1
    rep["runs"] = {"ex_run": len(pl["runs"]), "import": dict(imp), "import_errors": errs,
                   "missing_facts": missing, "balances": nb,
                   "positions": npos, "snapshot_no_balance": skip}


def load_verdicts(pl: dict, rep: dict) -> None:
    c = db.connect()
    vd = pl["verdicts"]
    with db.tx():
        for r in vd["rows"]:
            label = f"L{r['line']} · {r.get('group') or '-'} · {r['orig'][:80]} · {r['sub'][:80]}"
            # fact_rev = 들여올 때의 사실 판(저장소 판 v2 · 판정이 본 사실의 판 — 들여온 직후라 지금 판)
            c.execute("INSERT INTO ev_verdict (fact_id, run_id, batch, round, verdict, tier, lens, note, support, auditor, at,"
                      " src_file, row_label, match, fact_rev) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                      "(SELECT COALESCE(rev, 1) FROM ev_fact WHERE id=?))",
                      (r["fact_id"], r["run_id"], r["batch"], None, r["verdict"], r["tier"], None, r["note"], r["support"],
                       None, _date_of(r["run_id"][:6]), r["file"], label, r["how"], r["fact_id"]))
        # 사실 id를 정하지 못한 행 — 버리지 않고 ev_verdict_orphan에(사유·후보 id와 함께)
        for r in vd["unmatched"]:
            c.execute("INSERT INTO ev_verdict_orphan (run_id, batch, verdict, tier, url, claim_text, sub, note, reason, candidates,"
                      " src_file, line) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (r["run_id"], r["batch"], r["verdict"], r["tier"], r["url"], r["orig"], r["sub"], r["note"], r["reason"],
                       json.dumps(r.get("candidates") or [], ensure_ascii=False), r["file"], r["line"]))
        # 검증 상태 — 사실마다 가장 최근 run의 판정 행만 본다
        latest = {}
        for r in vd["rows"]:
            cur = latest.get(r["fact_id"])
            if cur is None or _run_order(r["run_id"]) > _run_order(cur[0]):
                latest[r["fact_id"]] = (r["run_id"], [r])
            elif r["run_id"] == cur[0]:
                cur[1].append(r)
        st = Counter()
        ph = {fid for fid, v in pl["facts"].items() if v["placeholder"]}
        for fid, (rid, rows) in latest.items():
            if fid in ph:                     # 포인터 행은 판정 기록만 두고 검증 상태는 매기지 않는다
                st["placeholder(상태 없음)"] += 1
                continue
            vs = {r["verdict"] for r in rows}
            good = [r["tier"] for r in rows if r["verdict"] == "MATCH" and r["tier"] in (1, 2)]
            if "MISMATCH" in vs:
                # 옛 구조는 불일치를 문장에 교정·전파해 두었다 — 교정된 문장은 재검증된 적이 없으니 '미검증'으로 둔다
                # (다시 쓰이면 검증 큐에 오른다). 'disputed'는 새 구조에서 교정 대기 중인 사실에만 쓴다.
                st["none(옛 불일치 — 교정 반영)"] += 1
                c.execute("UPDATE ev_fact SET needs_review=0 WHERE id=?", (fid,))  # 판정 트리거가 세운 표시 — 교정은 이미 문장에 있다
                continue
            elif "STALE" in vs:
                status, tier = "expired", None
            elif good:
                status, tier = "verified", min(good)
            else:
                st["none"] += 1
                continue
            c.execute("UPDATE ev_fact SET verified_status=?, verified_at=?, verified_tier=?, verified_run=? WHERE id=?",
                      (status, _date_of(rid[:6]), tier, rid, fid))
            st[status] += 1
    rep["verdicts"] = {"files": len(vd["files"]), "parsed": sum(f["rows"] for f in vd["files"].values()),
                       "matched": len(vd["rows"]), "unmatched": len(vd["unmatched"]), "methods": dict(vd["methods"]),
                       "unmatched_reasons": dict(Counter(r["reason"] for r in vd["unmatched"])),
                       "fallback_header": vd["fallback"], "skipped": vd["skipped"], "fact_status": dict(st)}


def load_ops(root: Path, pl: dict, rep: dict) -> None:
    c = db.connect()
    coll, nl = [], 0
    with db.tx():
        for x in pl["loss"]:
            d = x["doc"]
            week = str(d.get("week"))
            rows = d.get("rows") or []
            db.docmeta_put(f"loss_review_{week}", d)          # 원본 문서 통째(키가 겹쳐 덮인 행이 있어도 여기 남는다)
            seen = Counter(r.get("thesis_id") or f"{str(r.get('market', '')).lower()}:{r.get('ticker')}" for r in rows)
            coll += [f"{week} {k}" for k, v in seen.items() for _ in range(v - 1)]   # 덮인 행 수만큼
            nl += db.loss_review_put(week, rows)
            # loss_review.py: 닫힌 논지의 pnl = 그 논지의 실현 손익(주간 순실현은 그 합) — net_realized 칸이 없는 옛 행만 채운다
            for r in rows:
                if r.get("net_realized") is None and r.get("kind") == "closed" and r.get("pnl") is not None:
                    tid = r.get("thesis_id") or f"{str(r.get('market', '')).lower()}:{r.get('ticker')}"
                    c.execute("UPDATE op_loss_review SET net_realized=? WHERE week=? AND thesis_id=? AND net_realized IS NULL",
                              (r["pnl"], week, tid))
        nrc = 0
        for x in pl["real_check"]:
            for it in x["items"]:
                c.execute("INSERT INTO op_real_check (run_id, item, status, note, at) VALUES (?,?,?,?,?)"
                          " ON CONFLICT(run_id, item) DO UPDATE SET status=excluded.status, note=excluded.note, at=excluded.at",
                          (x["run_id"], it["item"], it["status"], it["note"], x["at"]))
                nrc += 1
        # 자기주식 보유 스냅숏(DART 반기보고서 · 국내) — 조회 오류 행(주식총수 없음 등)은 결산일 '' · error 칸으로(30일 재조회 규칙이 읽는다)
        ts = rd_json(root, "data/treasury_scan.json")
        nts, errs = 0, {}
        for k, v in (ts.items() if isinstance(ts, dict) else []):
            if isinstance(v, dict) and not v.get("stlm_dt") and v.get("error"):
                c.execute("INSERT OR REPLACE INTO co_treasury_holding (market, ticker, name, stlm_dt, scanned, error)"
                          " VALUES ('KR',?,?,'',?,?)", (str(v.get("ticker") or k), v.get("name"), v.get("scanned"), v["error"]))
                errs[k] = v
                continue
            if not isinstance(v, dict) or not v.get("stlm_dt"):
                errs[k] = v
                continue
            c.execute("INSERT INTO co_treasury_holding (market, ticker, name, corp_cls, period, stlm_dt, issued, treasury,"
                      " ratio_pct, rcept_no, scanned) VALUES ('KR',?,?,?,?,?,?,?,?,?,?)",
                      (str(v.get("ticker") or k), v.get("name"), v.get("corp_cls"), v.get("period"), v["stlm_dt"], v.get("issued"),
                       v.get("treasury"), v.get("ratio_pct"), v.get("rcept_no"), v.get("scanned")))
            nts += 1
        if errs:
            db.docmeta_put("treasury_scan_errors", errs)  # 원본 그대로(검사용) — 오류 행 자체는 위에서 표에 들어갔다
    na = 0
    for rid in sorted(pl["runs"], key=_run_order):
        st, mk = rid.split("_")
        na += db.audit_mirror(st, mk)
    rep["ops"] = {"loss_review": nl, "loss_review_pk_collisions": coll, "real_check": nrc, "audit": na,
                  "treasury_holding": nts, "treasury_errors": len(errs)}


def migrate_from_files(root, pl: dict | None = None) -> dict:
    """root 아래 파일을 지금 저장소(db.db_path())에 싣는다 — 빈 저장소여야 한다. 보관·내보내기·삭제는 하지 않는다."""
    root = Path(root).absolute()
    t0 = time.time()
    pl = pl or plan(root)
    c = db.connect()
    if not _empty_db(c):
        raise RuntimeError(f"저장소 {db.db_path()}가 비어 있지 않다 — 이전은 빈 저장소에만 한다")
    rep = {"root": str(root), "db": str(db.db_path()), "warnings": [], "timing": {}}

    def step(name, fn, *a):
        t = time.time()
        fn(*a)
        rep["timing"][name] = round(time.time() - t, 2)

    # 싣는 동안 자동 내보내기를 막는다 — 검사 전에 렌더본을 쓰면 렌더가 다를 때 원본 자리를 덮는다(내보내기는 검사 통과 뒤 apply가)
    with db.hold_exports():
        step("설정", load_config, root, rep)
        step("사실", load_facts, root, pl, rep)
        step("판단 원장", load_judgment, root, rep)
        step("로그", load_logs, root)
        step("run 산출물", load_runs, root, pl, rep)
        step("재료", load_materials, root, pl, rep)
        step("판정표", load_verdicts, pl, rep)
        step("운영", load_ops, root, pl, rep)
        with db.tx():
            c.execute("INSERT OR REPLACE INTO sys_meta (k, v) VALUES ('migrated_at', ?)", (db.now_iso(),))
            c.execute("INSERT OR REPLACE INTO sys_meta (k, v) VALUES ('migrated_from', ?)", (str(root),))
    rep["timing"]["싣기 합계"] = round(time.time() - t0, 2)
    return rep


# ───────────────────────────── 검사 ─────────────────────────────

def first_diff(a: str, b: str) -> int:
    n = min(len(a), len(b))
    return next((i for i in range(n) if a[i] != b[i]), n)


def _fmt_of(text: str):
    """원본 파일의 JSON 형식(들여쓰기·끝 줄바꿈) — 같은 값으로 다시 써서 바이트가 같아지는 형식을 찾는다."""
    v = json.loads(text)
    for ind in (2, 1, None, 4):
        for tail in ("", "\n"):
            if json.dumps(v, ensure_ascii=False, indent=ind) + tail == text:
                return ("json", ind, tail)
    return None


def artifact_expect(root: Path, pl: dict) -> dict:
    """run 산출물 파일에서 센 기대 행 수(import_run_artifacts와 같은 규칙 — 같은 바이트의 승인 파일은 한 번)."""
    out = Counter()
    seen = set()
    for st, mk in pl["artifact_runs"]:
        sig = root / "signals" / f"signal_{st}_{mk}.json"
        if sig.exists():
            s = json.loads(sig.read_text(encoding="utf-8"))
            out["signal"] += 1
            out["proposal"] += len(s.get("proposals") or []) + len(s.get("refill_candidates") or [])
        for ap in sorted(root.glob(f"signals/approved_{st}_{mk}*.json")):
            h = sha256(ap)
            if h in seen:
                continue
            seen.add(h)
            a = json.loads(ap.read_text(encoding="utf-8"))
            out["approval"] += len(a.get("orders") or []) + len(a.get("rejected") or [])
        tp = root / "journal" / f"trades_{st}_{mk}.json"
        if tp.exists():
            tr = json.loads(tp.read_text(encoding="utf-8")).get("trades") or []
            out["order"] += len(tr)
            out["fill"] += sum(1 for t in tr if isinstance(t.get("fill"), dict) and t["fill"].get("verdict"))   # 판정 있는 체결만 관측(v8 가져오기와 같다)
    return out


def doctor_check(root: Path, ref: Path, pl: dict, rep: dict | None = None) -> dict:
    """ref(보관 폴더 — 원본과 같은 상대 경로)의 원본과 저장소 렌더를 비교한다."""
    c = db.connect()
    res = {"integrity": c.execute("PRAGMA integrity_check").fetchone()[0],
           "fk": [dict(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()],
           "same": {}, "counts": [], "fail": []}

    def cmp_text(label, rel, got):
        p = ref / rel
        if not p.exists():
            res["same"][label] = "원본 없음"
            return
        want = p.read_text(encoding="utf-8")
        if want == got:
            res["same"][label] = "SAME"
            return
        i = first_diff(want, got)
        res["same"][label] = f"DIFF @{i}"
        res["fail"].append(f"{label}: 첫 차이 {i}바이트째 — 원본 {want[max(0, i - 80):i + 60]!r} · 렌더 {got[max(0, i - 80):i + 60]!r}")

    # 원본의 들여쓰기·끝 줄바꿈은 마지막으로 쓴 도구마다 다르다(market_map.json: market_map.py 2칸 · 옛 fc_track1 1칸) —
    # 원본 형식으로 렌더해 바이트를 대조한다(내용·키 순서·값 타입이 같다는 증명). 이전 뒤 렌더본은 db.EXPORTS 형식으로 쓴다.
    def orig_fmt(rel, fmt):
        q_ = ref / rel
        if fmt[0] == "json" and q_.exists():
            return _fmt_of(q_.read_text(encoding="utf-8")) or fmt
        return fmt

    for n, (_p, fmt) in db.EXPORTS.items():
        cmp_text(n, EXPORT_REL[n], db.serialize(db.render(n), orig_fmt(EXPORT_REL[n], fmt)))
    for n, (_p, fmt) in db.CHECK_ONLY.items():
        cmp_text(n, CHECK_REL[n], db.serialize(db.render(n), orig_fmt(CHECK_REL[n], fmt)))
    # 로그·배분 — 다시 써 봐도 같은가(추가 검사)
    for n, rel in LOG_REL.items():
        if n != "feedback":
            cmp_text(f"log:{n}", rel, db.serialize(db.log_rows(n), ("jsonl",)))
    cmp_text("allocation", "journal/allocation.jsonl", db.serialize(db.STORES["allocation"].all(), ("jsonl",)))
    # 작은 kv 문서 — 값이 같은가(형식을 찾으면 바이트까지)
    kvs = [("benchmark", "config/benchmark.json", db.kv_all("op_benchmark_anchor")),
           ("current_run", "journal/current_run.json", db.current_run_read())]
    for p in refill_files(ref):
        m = re.match(r"^refill_(\d{6})_(kr|us)\.json$", p.name)
        if m:
            kvs.append((f"refill:{p.name}", f"journal/{p.name}", db.kv_get("ex_refill", _rid(m.group(1), m.group(2)))))
    for label, rel, val in kvs:
        p = ref / rel
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        f = _fmt_of(txt)
        if f and db.serialize(val, f) == txt:
            res["same"][label] = "SAME"
        elif json.loads(txt) == val:
            res["same"][label] = "값 같음"
        else:
            res["same"][label] = "DIFF"
            res["fail"].append(f"{label}: 값이 다르다")
    # 업종 대응표(db.sector_bridge_doc()이 옛 모양을 돌려준다)·시총 상위 — 값 비교(형식을 찾으면 바이트까지)
    p = ref / "config/sector_bridge.json"
    if p.exists():
        txt = p.read_text(encoding="utf-8")
        back = db.sector_bridge_doc()
        f = _fmt_of(txt)
        if f and db.serialize(back, f) == txt:
            res["same"]["sector_bridge"] = "SAME"
        elif back == json.loads(txt) and list(back) == list(json.loads(txt)):
            res["same"]["sector_bridge"] = "값 같음"
        else:
            res["same"]["sector_bridge"] = "DIFF"
            res["fail"].append("sector_bridge: sector_bridge_doc()이 원본과 다르다")
    mn = rd_json(ref, "config/main_names.json")
    if isinstance(mn, dict):
        ok = True
        for m in ("KR", "US"):
            got = [(r["ticker"], r["name"]) for r in db.main_names(m)]
            want = [(str(r["ticker"]), r.get("name")) for r in mn.get(m) or []]
            if [g[0] for g in got] != [w[0] for w in want]:
                ok = False
        res["same"]["main_names"] = "순서·종목 같음" if ok else "DIFF"
        if not ok:
            res["fail"].append("main_names: 순위 목록이 다르다")

    def cnt(label, src_n, table, where=""):
        dn = c.execute(f"SELECT COUNT(*) FROM {table} {('WHERE ' + where) if where else ''}").fetchone()[0]
        res["counts"].append((label, src_n, table + (f" [{where}]" if where else ""), dn))
        if src_n != dn:
            res["fail"].append(f"행 수 {label}: 원본 {src_n} · 저장소 {dn}")

    th = rd_json(ref, "journal/theses.json") or {}
    sc = rd_json(ref, "journal/scenarios.json") or {}
    mm = rd_json(ref, "journal/market_map.json") or {}
    cal = rd_json(ref, "journal/calendar.json") or {}
    cnt("theses", len(th.get("theses") or []), "jd_thesis")
    cnt("scenarios", len(sc.get("scenarios") or []), "jd_scenario")
    cnt("market_map.axes", len(mm.get("axes") or []), "dim_axis")
    cnt("market_map.sectors", len(mm.get("sectors") or []), "dim_sector")
    cnt("calendar", len(cal.get("events") or []), "cal_event")
    cnt("allocation", len(rd_jsonl(ref, "journal/allocation.jsonl")), "jd_allocation")
    for n, rel in LOG_REL.items():
        cnt(n, len(rd_jsonl(ref, rel)), db.LOGS[n][0])
    cnt("equity_curve", len(rd_jsonl(ref, "journal/equity_curve.jsonl")), "ex_equity_point")
    cnt("universe_coverage", len(rd_json(ref, "journal/universe_coverage.json") or {}), "op_coverage")
    cnt("universe_pending", len(rd_json(ref, "journal/universe_pending.json") or {}), "op_pending")
    cnt("benchmark", len(rd_json(ref, "config/benchmark.json") or {}), "op_benchmark_anchor")
    cnt("current_run", 1 if (ref / "journal/current_run.json").exists() else 0, "op_current_run")
    pk = rd_json(ref, "journal/position_peaks.json") or {}
    cnt("position_peaks", sum(len(v) for k, v in pk.items() if isinstance(v, dict)), "ex_position_peak")
    cnt("refill", len(refill_files(ref)), "ex_refill")
    wl = rd_json(ref, "config/watchlist.json") or {}
    cnt("watchlist", len(wl.get("KR") or []) + len(wl.get("US") or []), "dim_company", "in_watchlist=1")
    mn = rd_json(ref, "config/main_names.json") or {}
    cnt("main_names", len(mn.get("KR") or []) + len(mn.get("US") or []), "dim_company", "main_rank IS NOT NULL")
    sb = rd_json(ref, "config/sector_bridge.json") or {}
    cnt("sector_bridge", sum(len(sb.get(k) or []) for k in ("links", "retired_links", "kr_without_us", "us_without_kr")),
        "dim_sector_link")
    cnt("sources", len(pl["src"]["rows"]), "ev_fact")
    cnt("sources.runs", sum(len(s.get("runs") or []) for s in pl["src"]["rows"]), "ev_fact_run_label")
    cnt("판정표(맞춘 행)", len(pl["verdicts"]["rows"]), "ev_verdict")
    cnt("판정표(못 맞춘 행)", len(pl["verdicts"]["unmatched"]), "ev_verdict_orphan")
    ts = rd_json(root, "data/treasury_scan.json")
    ts = ts if isinstance(ts, dict) else {}
    cnt("자기주식 보유", sum(1 for v in ts.values() if isinstance(v, dict) and v.get("stlm_dt")), "co_treasury_holding", "stlm_dt<>''")
    cnt("자기주식 조회 오류", sum(1 for v in ts.values() if isinstance(v, dict) and not v.get("stlm_dt") and v.get("error")),
        "co_treasury_holding", "stlm_dt='' AND error IS NOT NULL")
    nsnap = sum(1 for p in pl["snapshots"] if isinstance(json.loads(p.read_text(encoding="utf-8")).get("balance"), dict))
    cnt("snapshots", nsnap, "ex_balance", "phase LIKE 'capture%'")
    cnt("ex_run", len(pl["runs"]), "ex_run")
    cnt("재료 발행분", pl["materials"].get("issues", 0), "mat_issue")
    cnt("세상공부 행", pl["materials"].get("ws_rows", 0), "mat_ws_event")
    cnt("실전 점검표", sum(len(x["items"]) for x in pl["real_check"]), "op_real_check")
    ex = artifact_expect(root, pl)
    cnt("시그널", ex["signal"], "ex_signal")
    cnt("제안(+재투자 후보)", ex["proposal"], "ex_proposal")
    cnt("승인 주문·거부", ex["approval"], "ex_approval")
    cnt("체결 기록 주문", ex["order"], "ex_order")
    cnt("체결", ex["fill"], "ex_fill")
    nloss = sum(len(x["doc"].get("rows") or []) for x in pl["loss"])
    coll = len(((rep or {}).get("ops") or {}).get("loss_review_pk_collisions") or [])
    cnt(f"손실 판정(키 충돌 {coll}행은 sys_docmeta에만)", nloss - coll, "op_loss_review")
    for e in ((rep or {}).get("runs") or {}).get("import_errors") or []:
        res["fail"].append(f"run 산출물 싣기 실패 {e}")
    ok = res["integrity"] == "ok" and not res["fk"] and not res["fail"]
    if res["fk"]:
        res["fail"].append(f"FK 위반 {len(res['fk'])}건: {res['fk'][:3]}")
    if res["integrity"] != "ok":
        res["fail"].append(f"integrity: {res['integrity']}")
    res["ok"] = ok
    return res


# ───────────────────────────── 보관·실행·되돌리기 ─────────────────────────────

def _db_files(p: Path) -> list:
    return [x for x in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")) if x.exists()]


def _move_db(target: Path, dest_dir: Path, name: str) -> list:
    db.reset()
    out = []
    for f in _db_files(target):
        d = dest_dir / (name + ".db" + f.name[len(target.name):])
        shutil.move(str(f), str(d))
        out.append(str(d))
    return out


def _setup_target(root: Path) -> Path:
    """저장소 경로와 db의 뿌리를 root로 맞춘다 — 사본(--root) 실행이면 db.set_root로 렌더본·백업 경로가 root를 따른다."""
    if root != REAL_HERE and not os.environ.get("AITB_DB"):
        os.environ["AITB_DB"] = str(root / "journal" / "trading.db")
    db.set_root(root)
    db.reset()
    return db.db_path()


def archive(root: Path) -> Path:
    base = root / "journal" / "_pre_db"
    stamp = datetime.now(KST).strftime("%y%m%d_%H%M")
    dest = base / stamp
    k = 2
    while dest.exists():
        dest = base / f"{stamp}_{k}"
        k += 1
    man = {}
    for rel in archive_list(root):
        s = root / rel
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        h = sha256(s)
        if sha256(d) != h:
            raise RuntimeError(f"보관 복사가 원본과 다르다: {rel}")
        man[rel] = {"sha256": h, "bytes": s.stat().st_size, "mode": oct(s.stat().st_mode & 0o777)}
    (dest / "manifest.json").write_text(json.dumps({"root": str(root), "at": db.now_iso(), "files": man,
                                                    "remove_after_ok": remove_list(root)}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    return dest


def apply(root: Path, force: bool = False) -> int:
    try:
        return _apply(root, force)
    finally:
        db.set_root(REAL_HERE)          # 사본 실행 뒤 db 뿌리를 제자리로


def rollback(root: Path, folder: str) -> int:
    try:
        return _rollback(root, folder)
    finally:
        db.set_root(REAL_HERE)


def _apply(root: Path, force: bool = False) -> int:
    t0 = time.time()
    root = root.absolute()
    if root == Path(REAL_HERE).absolute() and os.environ.get("AITB_DB") and not os.environ.get("AITB_EXPORT_REAL"):
        print("거부 — 실제 폴더에 임시 저장소(AITB_DB)로 이전하면 실제 원장을 지운다. AITB_DB를 빼거나 --root로 사본을 지정하라.")
        return 2
    missing = [r for r in REMOVE_FIXED + list(EXPORT_REL.values()) if not (root / r).exists()]
    if missing and any(r.endswith(("theses.json", "sources.json", "scenarios.json")) for r in missing):
        print(f"거부 — 원장 파일이 없다(이미 이전했나?): {missing[:6]} — 되돌리려면 --rollback <보관 폴더>")
        return 2
    target = _setup_target(root)
    old = _db_files(target)
    if old and not force:
        print(f"거부 — 저장소가 이미 있다: {target} (덮어쓰려면 --force — 옛 저장소는 보관 폴더로 옮긴다)")
        return 2
    ref = archive(root)
    print(f"보관: {ref.relative_to(root)} — 원본 {len(archive_list(root))}개 복사 · 해시 일치")
    if old:
        moved = _move_db(target, ref, "trading_replaced")
        print(f"옛 저장소를 옮김: {moved}")
    pl = plan(root)
    try:
        rep = migrate_from_files(root, pl)
    except Exception as e:
        moved = _move_db(target, ref, "trading_failed")
        print(f"싣기 실패: {type(e).__name__}: {e}\n원본은 그대로 · 저장소는 {moved}로 치움")
        raise
    t1 = time.time()
    doc = doctor_check(root, ref, pl, rep)
    rep["timing"]["검사"] = round(time.time() - t1, 2)
    rep["doctor"] = {k: v for k, v in doc.items() if k != "counts"}
    rep["doctor"]["counts"] = [list(x) for x in doc["counts"]]
    print_report(rep, doc, pl)
    if not doc["ok"]:
        moved = _move_db(target, ref, "trading_failed")
        (ref / "migrate_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print("\nDOCTOR FAIL — 멈춤. 원본은 그대로 둔다 · 저장소는 보관 폴더로 치움: " + ", ".join(moved))
        for f in doc["fail"][:20]:
            print("  ✗ " + f)
        return 1
    # 통과 — 내보내기 7개를 읽기 전용 렌더본으로, 내보내기가 아닌 journal 원장은 지운다
    man = json.loads((ref / "manifest.json").read_text(encoding="utf-8"))["files"]
    for n in db.EXPORTS:
        dest = root / EXPORT_REL[n]
        db.export(n, dest=dest)
        os.chmod(dest, 0o444)
    removed = []
    for rel in remove_list(root):
        p = root / rel
        if p.exists() and rel in man and sha256(p) == man[rel]["sha256"]:
            p.unlink()
            removed.append(rel)
        elif p.exists():
            rep["warnings"].append(f"보관 뒤 바뀐 파일이라 지우지 않음: {rel}")
    rep["removed"] = removed
    rep["timing"]["전체"] = round(time.time() - t0, 2)
    db.reset()                       # 연결을 닫아 WAL을 본 파일에 합친다(-wal·-shm이 남지 않게)
    (ref / "migrate_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nDOCTOR OK — 내보내기 7개 읽기 전용 렌더본 · journal 원장 {len(removed)}개 삭제(원본은 {ref.relative_to(root)})")
    print(f"저장소: {target} · 전체 {rep['timing']['전체']}초 · 보고서 {ref.relative_to(root)}/migrate_report.json")
    return 0


def _rollback(root: Path, folder: str) -> int:
    root = root.absolute()
    ref = Path(folder)
    if not ref.is_absolute():
        ref = ref if (ref / "manifest.json").exists() else root / "journal" / "_pre_db" / folder
    mf = ref / "manifest.json"
    if not mf.exists():
        print(f"보관 폴더가 아니다(manifest.json 없음): {ref}")
        return 2
    man = json.loads(mf.read_text(encoding="utf-8"))["files"]
    target = _setup_target(root)
    moved = _move_db(target, ref, "trading_rolledback_" + datetime.now(KST).strftime("%H%M%S")) if _db_files(target) else []
    bad = []
    for rel, info in man.items():
        s, d = ref / rel, root / rel
        if d.exists():
            os.chmod(d, 0o644)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        os.chmod(d, int(info.get("mode", "0o644"), 8))
        if sha256(d) != info["sha256"]:
            bad.append(rel)
    print(f"되돌림: 원본 {len(man)}개 제자리 · 저장소 {moved or '없음'} → 보관 폴더")
    if bad:
        print(f"✗ 해시 불일치: {bad}")
        return 1
    print("ROLLBACK OK")
    return 0


# ───────────────────────────── 출력 ─────────────────────────────

def print_inventory(pl: dict) -> None:
    print("| 분류 | 파일 | 행 | 대상 | 이전 뒤 |\n|---|---|---|---|---|")
    for r in pl["inventory"]:
        print(f"| {r['분류']} | {r['파일']} | {r['행']} | {r['대상']} | {r['이전 뒤']} |")
    print("\n사실 종류(kind/valid_class): " + " · ".join(f"{k}/{vc} {n}" for (k, vc), n in pl["kinds"].most_common()))
    ph = Counter(v["placeholder"] for v in pl["facts"].values() if v["placeholder"])
    print(f"자리표시 행: {sum(ph.values())} ({' · '.join(f'{k} {n}' for k, n in ph.items())})")
    ty = Counter(v["typed"][0] for v in pl["facts"].values() if v["typed"])
    print(f"상세 행 후보: {dict(ty)} · URL 원문 {pl['urls']['raw']}개 → 정규화 {pl['urls']['normalized']}개")
    vd = pl["verdicts"]
    print(f"판정표: 파일 {len(vd['files'])} · 행 {sum(f['rows'] for f in vd['files'].values())} · 맞춤 {len(vd['rows'])} "
          f"({' · '.join(f'{k} {n}' for k, n in vd['methods'].most_common())}) · 못 맞춤 {len(vd['unmatched'])} "
          f"({' · '.join(f'{k} {n}' for k, n in Counter(r['reason'] for r in vd['unmatched']).most_common())})"
          + (f" · 옛 머리 대응 {vd['fallback']}" if vd["fallback"] else ""))
    print(f"재료: {pl['materials']}")


def print_report(rep: dict, doc: dict, pl: dict) -> None:
    print("\n== 검사 ==")
    print(f"integrity {doc['integrity']} · FK 위반 {len(doc['fk'])}건")
    print(" · ".join(f"{k} {v}" for k, v in doc["same"].items()))
    print("| 원장 | 원본 행 | 표 | 저장소 행 |\n|---|---|---|---|")
    for label, sn, table, dn in doc["counts"]:
        print(f"| {label} | {sn} | {table} | {dn}{'' if sn == dn else ' ✗'} |")
    f = rep.get("facts", {})
    print(f"\n사실 {f.get('rows')} · 출처 {f.get('sources')}(티어 엇갈림 {f.get('tier_conflict_sources')}) · URL 없음 {f.get('no_url')} · "
          f"URL 여럿 {f.get('multi_url')} · 상세 행 {f.get('typed')} · 자리표시 {len(f.get('placeholders') or [])}")
    v = rep.get("verdicts", {})
    print(f"판정표 {v.get('files')}개 · 행 {v.get('parsed')} · 맞춤 {v.get('matched')} {v.get('methods')} · 못 맞춤 {v.get('unmatched')} "
          f"{v.get('unmatched_reasons')} · 검증 상태 {v.get('fact_status')}")
    print(f"재료 {rep.get('materials')}")
    print(f"run {rep.get('runs')}")
    print(f"운영 {rep.get('ops')}")
    if rep.get("warnings"):
        print("주의: " + " / ".join(rep["warnings"]))
    print(f"시간(초): {rep.get('timing')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="원장 파일 → trading.db 일회성 이전")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", metavar="보관폴더")
    ap.add_argument("--root", default=str(REAL_HERE))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root).absolute()
    if not (root / "journal").is_dir():
        print(f"journal/이 없다: {root}")
        return 2
    if a.dry:
        t = time.time()
        pl = plan(root)
        print(f"# 이전 예행(--dry) — {root} · 저장소 대상 {os.environ.get('AITB_DB') or root / 'journal' / 'trading.db'}\n")
        print_inventory(pl)
        print(f"\n(쓴 것 없음 · {time.time() - t:.1f}초)")
        return 0
    if a.apply:
        return apply(root, a.force)
    return rollback(root, a.rollback)


if __name__ == "__main__":
    sys.exit(main())
