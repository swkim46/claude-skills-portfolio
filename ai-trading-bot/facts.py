#!/usr/bin/env python3
"""사실 저장소 — 조사 결과 하나 = `ev_fact` 머리 행 하나 + 종류별 상세 행 하나(판단에 쓰는 칸이 컬럼).

왜(2026-10-17 사용자 결정 · 설계 `Phase3_데이터베이스화_설계_v1_0.md`): 리서치 사실을 `journal/sources.json`에 문장으로 쌓고
문서 수십 개에 복사했다. run마다 같은 사실을 다시 조사·재검증했다(1,833행 · 재사용 0 · 판정 495행 · 감사 6회). 여기서는
- 사실은 한 번만 산다: 종류별 열쇠(KINDS의 key)가 같으면 기존 행을 다시 쓰고(빈 칸만 채움) 이번 run의 S 번호만 단다.
- 검증 상태는 사실에 붙는다: static·verified는 다시 큐에 오르지 않는다(주문 근거만 예외 — 주문은 더 빡빡하게 본다).
- 교정은 UPDATE 한 번이다(rev를 올려 판 기록 트리거가 옛 값을 남긴다) — 전파가 없다.
- 노트 §12·클레임 목록·판정표는 이 표를 읽어 만든 렌더본이다. 형식은 옛 `srcledger.py`·`fc_track1.py`·`lint_verdict.py`
  출력을 글자 그대로 잇는다 — 게이트의 봉인 기준이 산출 파일의 글자를 센다.

저장소를 여는 코드는 `db.py` 하나다. 이 모듈은 `db.connect()`·`db.tx()`로만 쓴다.

명령(python3 facts.py <명령> …):
  add        --stamp --market --batch <jsonl> [--minor] | --kind K --col k=v … [--as-of --source --url --quote --tier --minor]
  mention    --stamp --market --batch <jsonl>                 재료가 종목·섹터·축을 어떻게 다뤘나(mat_mention · §3 보드의 원본)
             행: issue_id|link_id|ws_event_id · subject_type · subject_id(섹터는 보드 코드·이름 · '*' = 그 발행분의 남은 섹터 전부)
                 · stance · headline · board_verdict(새 정보·살아 있음·반증·해당 없음) · fact_ids
  before-news --stamp --market                                뉴스보다 먼저 찾은 사실 수 · 재료 지연 → ex_run
  known      --ticker X | --sector S | --axis A | --indicator I [--market] [--since 14d] [--stamp]
  queue      --stamp --market [--batch A|B|track1] [--append] [--out]
  verdict    --batch <jsonl> [--stamp --market --label A|B|track1 --round N]
  lint       --stamp --market --batch A|B|track1
  correct    F-000731 --set col=val … --reason "…" [--verdict <id>] [--stamp --market]
  check-refs --stamp --market
  check      --stamp --market [--note] [--out]                옛 srcledger check(세 시점 대조)와 같은 검사·형식
  render-s12 --stamp --market [--out]                         옛 srcledger render(§12)와 같은 표
  render-claims  --stamp --market --batch A|B|track1 [--out] [--append] [--force]
  render-verdict --stamp --market --batch A|B|track1 [--out]
  decide     --stamp --market --mode pre|post [--signal <signal json>]
  reuse      --stamp --market [--days N] [--brief]
  line       F-000731                                         한 줄 렌더
  kinds      [--kind K]                                       종류별 받는 칸 — 필수·하나 이상·선택·별칭(리서치 행을 쓰기 전에 본다)
  render-board --stamp --market [--out]                        노트 §3 섹터 보드(보드 기록 + 섹터 상태 + 재료 언급 mat_mention)
  reverse    --stamp --market --ticker T --qty N --why "…" [--kind hold_overturned]   보유 판단 재판정 매도(소급은 decide --mode post만)
"""
from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# absolute()(resolve 아님) — 작업 사본이 이 파일을 심볼릭 링크로 물려받을 때 그 사본의 폴더를 HERE로 삼는다(db.py와 같다).
# resolve()면 링크 원본 폴더가 sys.path 맨 앞에 끼어 사본의 다른 모듈 대신 원본 모듈을 불러온다.
HERE = Path(__file__).absolute().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import db  # noqa: E402

KST = db.KST
ANALYSIS = HERE / "analysis"
QUOTE_MIN = 20                      # 원문 인용 최소 길이(srcledger와 같다) — 숫자 하나·제목 한 줄로 인용을 갈음하지 않게
QUOTE_EXEMPT = ("disclosure",)      # 원문 인용을 강제하지 않는 종류 — 공시는 접수번호·보고서명·URL이 원문 그 자체다(stage3 〈돌려주는 행〉)
VERDICTS = ("MISMATCH", "UNVERIFIABLE", "STALE", "MATCH")   # 나쁜 순서 — '가장 나쁜 판정'을 고를 때 이 순서를 쓴다
VALID = ("static", "dated", "live", "interp")
ORIGINS = ("issue", "link", "ws_event", "dart", "research", "tool")
BATCHES = ("A", "B", "track1")
MAX_ROUNDS_DEFAULT = 5
# srcledger와 같은 정규식 — '링크'와 완료 스탬프의 정의(게이트가 같은 정의로 센다)
RX_URL = re.compile(r"https?://[^\s)\]\"'<>]+")
_SEC = r"(§[0-9A-Za-z-]+|머리말|작업기록-\w+)"
RX_DONE = re.compile(r"<!--\s*✓\s*" + _SEC + r"\s*-->")
RX_WRITTEN = re.compile(r"<!--\s*written\s+" + _SEC + r"\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*-->")


class Reject(ValueError):
    """행을 넣지 않는다(반쪽 행 금지) — 이유를 그대로 보여 준다."""


# ───────────────────────────── 종류(KINDS) ─────────────────────────────

class Kind:
    """종류 하나 = 상세 표 하나.
    req     판단에 쓰는 칸(없으면 거부). 'quote'는 load-bearing일 때만(--minor는 강제에서 빠진다), 'as_of'는 머리 칸
    req_any 묶음 중 하나는 있어야 한다(예: 가이던스 low·high·mid)
    key     같은 사실인가(중복이면 재사용) · '@as_of'=기준일(날짜) · '@period'=머리 period
    slot    dated 종류에서 '같은 자리' — 더 새 as-of가 오면 옛 행은 expired
    alias   입력 이름 → 컬럼(상세 표의 'kind' 칸은 사실 종류 'kind'와 겹쳐 별칭으로만 받는다)"""

    def __init__(self, table, req, valid, key, slot="", subject=None, period=None, as_of=None, render="",
                 alias=None, req_any=(), tool=False):
        self.table, self.valid, self.render = table, valid, render
        self.req, self.key, self.slot = req.split(), key.split(), slot.split()
        self.req_any = [g.split() for g in req_any]
        self.subject, self.period, self.as_of = subject, period, as_of
        self.alias = alias or {}
        self.tool = tool


def _stmt_subject(d: dict) -> tuple:
    if d.get("ticker"):
        return "ticker", d["ticker"]
    if d.get("org"):
        return "org", d["org"]
    return "person", d.get("speaker")


def _event_subject(d: dict) -> tuple:
    return ("ticker", d["ticker"]) if d.get("ticker") else ("event", d.get("kind"))


KINDS = {
    # ③ 거시
    "release": Kind("ma_release", "indicator_code period actual as_of url quote", "dated",
                    key="indicator_code period @as_of", slot="indicator_code period",
                    subject=("indicator", "indicator_code"), period="period",
                    render="{indicator_code} {period} {actual}{unit}[ (예상 {consensus})][ · 이전 {prior}][ · 수정 {revised}]"
                           "[ · 발표 {released_at}][ · 다음 발표 {next_release}]"),
    "policy": Kind("ma_policy", "body title as_of url quote", "dated", alias={"policy_kind": "kind"},
                   key="body title status", slot="body title", subject=("org", "body"),
                   render="{body}[ ({jurisdiction})][ {kind}] {title}[ — {status}][ · 결정 {decided_at}][ · 기한 {deadline}]"),
    # ⑤ 세부시장
    "metric": Kind("sc_metric", "subject_type subject_id metric_code value as_of url quote", "dated",
                   key="subject_type subject_id metric_code period @as_of", slot="subject_type subject_id metric_code period",
                   subject=("@subject_type", "subject_id"), period="period",
                   render="{subject_id} {metric_code}[ {period}] {value}{unit}[ (이전 {prior})][ · 변화 {chg}][ · {trend}]"),
    # ⑥ 종목
    "earnings": Kind("co_earnings", "ticker period metric actual as_of url quote", "static",
                     key="ticker period metric basis", subject=("ticker", "ticker"), period="period",
                     render="{ticker} {period} {metric} {actual}{unit}[ ({basis})][ · 예상 {consensus}][ · 전년 {prior_year}]"
                            "[ · 전년비 {yoy}][ · 전분기비 {qoq}][ · 서프라이즈 {surprise_pct}%]"),
    "guidance": Kind("co_guidance", "ticker period metric as_of url quote", "dated", req_any=("low high mid",),
                     key="ticker period metric @as_of", slot="ticker period metric", subject=("ticker", "ticker"),
                     period="period",
                     render="{ticker} {period} {metric} 가이던스[ {low}][–{high}][ 중간 {mid}]{unit}[ (이전 {prior_low}–{prior_high})]"
                            "[ · {direction}]"),
    "consensus": Kind("co_consensus", "ticker period metric mean as_of url quote", "dated",
                      key="ticker period metric provider @as_of", slot="ticker period metric provider",
                      subject=("ticker", "ticker"), period="period",
                      render="{ticker} {period} {metric} 컨센서스 {mean}{unit}[ ({n_analysts}명)][ · {provider}]"),
    "analyst": Kind("co_analyst", "ticker house date action target_new url quote", "dated", as_of="date",
                    key="ticker house date", slot="ticker house", subject=("ticker", "ticker"), period="est_period",
                    render="{ticker} {house}[ {analyst}] {date} {action}[ {rating}] 목표가 [{target_old}→]{target_new}[ {currency}]"
                           "[ · {est_period} 추정 영업이익 {est_op}][ · 추정 매출 {est_rev}]"),
    "order": Kind("co_order", "ticker amount announced_at url quote", "static", as_of="announced_at",
                  key="ticker announced_at counterparty amount", subject=("ticker", "ticker"),
                  render="{ticker} 수주 {amount}[ {currency}][ — {counterparty}][ {product}][ (매출 대비 {pct_of_sales}%)]"
                         "[ · 기간 {start_date}~{end_date}] · 공시 {announced_at}[ · 수주잔고 {backlog_after}]"),
    "disclosure": Kind("co_disclosure", "ticker type filed_at title url", "static", as_of="filed_at",
                       key="ticker filed_at title", subject=("ticker", "ticker"),
                       render="{ticker} 공시 {type} '{title}' · {filed_at}[ ({doc_no})]"),
    "action": Kind("co_action", "ticker kind as_of url quote", "dated", alias={"action_kind": "kind"},
                   key="ticker kind decided_at status", slot="ticker kind decided_at", subject=("ticker", "ticker"),
                   render="{ticker} {kind}[ {amount}][ {currency}][ {shares}주][ {pct}%][ · {status}][ · 결정 {decided_at}]"
                          "[ · {period_start}~{period_end}]"),
    "statement": Kind("co_statement", "speaker date quote url", "static", as_of="date",
                      key="speaker date quote", subject=_stmt_subject,
                      render='{speaker}[ ({role})][ · {org}] {date}: "{quote}"'),
    "level": Kind("co_level", "ticker kind as_of", "live", alias={"level_kind": "kind"}, req_any=("value lo",), tool=True,
                  key="market ticker kind @as_of", slot="market ticker kind", subject=("ticker", "ticker"),
                  render="{ticker} {kind}[ {value}][ (구간 {lo}–{hi})][ · {method}]"),
    "event": Kind("co_event", "kind date as_of url", "dated", alias={"event_kind": "kind"},
                  key="ticker kind date", slot="ticker kind @period", subject=_event_subject,
                  render="[{ticker} ]{kind} {date}[ {time}][ {tz}][ · {summary}]"),
    # ④ 시장 관측(도구 · live · 검증 큐 밖) — 표 mk_obs는 db 요청 대기(없으면 거부한다)
    "obs": Kind("mk_obs", "subject field value ts", "live", tool=True, as_of="ts",
                key="subject field ts", slot="subject field", subject=("asset", "subject"),
                render="{subject} {field} {value}{unit} @ {ts}"),
}

# 입력 행의 머리 칸(상세 표 칸이 아닌 것)
HEADER_IN = {"as_of", "url", "quote", "tier", "source", "source_label", "publisher", "page_title", "raw_path",
             "load_bearing", "minor", "market", "period", "valid_class", "origin", "origin_id", "retrieved",
             "links", "axis", "sector", "tickers",
             "source_version", "raw_sha"}      # v3 — 이 사실이 본 본문 판(fetch.py가 찍은 `[원문] v<판>` · 또는 그 판의 sha256 앞자리)
# correct가 고칠 수 있는 머리 칸
HEADER_EDIT = {"as_of", "quote", "url", "source_label", "tier", "period", "valid_class", "load_bearing", "market", "legacy_text"}
# 값이 바뀌면 검증 상태를 되돌리는 머리 칸(출처 이름·등급 같은 표기는 값이 아니다)
VALUE_HEADER = {"as_of", "quote", "url", "period", "legacy_text"}


# ───────────────────────────── 작은 도구 ─────────────────────────────

def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and (not v.strip() or v.strip() == "미확인"))


def _tier(v):
    """1·2·3 또는 'data'(도구·브로커 값). 'T1' 표기도 받는다."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = str(v).strip().lower()
    if s in ("data", "도구", "tool"):
        return "data"
    s = s.lstrip("t").strip()
    if s in ("1", "2", "3"):
        return int(s)
    raise Reject(f"tier는 1|2|3|data: {v!r}")


def _tlabel(t) -> str:
    if t in (None, ""):
        return "—"
    return "data" if t == "data" else f"T{t}"


def _vfmt(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        if v != v:
            return None
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.6f}".rstrip("0").rstrip(".")
    s = str(v).strip()
    return s or None


def _unit(u) -> str:
    u = _vfmt(u)
    if not u:
        return ""
    return u if (u.startswith("%") or u in ("bp", "bps", "¢", "x", "배")) else " " + u


_SEG = re.compile(r"\[([^\[\]]*)\]")
_PH = re.compile(r"\{(\w+)\}")


def _fill(tpl: str, d: dict) -> str:
    """한 줄 틀 — `{칸}`은 값, `[ … ]`은 안의 칸이 하나라도 비면 통째로 뺀다. 값은 한 번만 넣는다(값 안의 {}는 그대로)."""
    def ph(text, optional):
        miss = False

        def rep(m):
            nonlocal miss
            k = m.group(1)
            if k == "unit":
                return _unit(d.get("unit"))
            v = _vfmt(d.get(k))
            if v is None:
                miss = True
                return ""
            return v
        out = _PH.sub(rep, text)
        return "" if (optional and miss) else out
    parts, pos = [], 0
    for m in _SEG.finditer(tpl):
        parts.append(ph(tpl[pos:m.start()], False))
        parts.append(ph(m.group(1), True))
        pos = m.end()
    parts.append(ph(tpl[pos:], False))
    return re.sub(r"\s{2,}", " ", "".join(parts)).strip()


def _num(v):
    """숫자 문자열은 숫자로(타입 없는 칸만) — 판단이 비교·계산에 쓴다."""
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if re.fullmatch(r"[-+]?\d+", s):
            return int(s)
        if re.fullmatch(r"[-+]?\d*\.\d+", s):
            return float(s)
    return v


def _norm_url(u) -> str:
    """같은 출처를 알아보는 정규화 — 열쇠는 한 곳(`db.norm_url` · 원문 보관과 사실 등록이 같은 규칙을 쓴다)."""
    return db.norm_url(u)


# 판정표의 렌더 시각 두 곳(`facts.py check-refs <시각>` · `facts.py lint <시각>`)만 — 재렌더 비교에서 뺀다(내용 속 날짜·시각은 그대로 비교)
_NOW_RX = re.compile(r"(?<=facts\.py check-refs )\d{4}-\d{2}-\d{2} \d{2}:\d{2}|(?<=facts\.py lint )\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def _now_min() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def _dt(s):
    """ISO·날짜 문자열 → 시간대 있는 datetime(없으면 KST로 본다)."""
    if not s:
        return None
    s = str(s).strip().replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", str(s)) else str(s).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=KST)


def _run(run) -> tuple:
    """'261016_us' · ('261016','us') → (run_id, stamp, market 소문자)."""
    if isinstance(run, (tuple, list)):
        st, mk = run
    else:
        st, mk = str(run).split("_", 1)
    mk = str(mk).lower()
    return db.run_id(st, mk), str(st), mk


_COLS: dict = {}


def _colinfo(table: str) -> dict:
    """표 컬럼 {이름: 선언 타입} — 표가 없으면 {}(db 요청 대기 표)."""
    key = (str(db.db_path()), table)
    if key not in _COLS:
        rows = db.connect().execute(f"PRAGMA table_info({table})").fetchall()
        _COLS[key] = {r["name"]: (r["type"] or "").upper() for r in rows}
    return _COLS[key]


def _dcols(k: Kind) -> list:
    return [c for c in _colinfo(k.table) if c != "fact_id"]


_INTERNAL = ("created_at", "updated_at", "rev", "ko", "extra")


def kinds_text(only: str = "") -> str:
    """종류별 받는 칸 — 리서치 지시문·행을 쓰기 전에 본다(모르는 칸은 행째 거부된다)."""
    out = []
    for name, k in KINDS.items():
        if only and name != only:
            continue
        cols = [c for c in _dcols(k) if c not in _INTERNAL]
        req = [c for c in k.req]
        opt = [c for c in cols if c not in req and not any(c in g for g in k.req_any) and c not in k.alias.values()]
        line = [f"■ {name} ({k.table} · {k.valid}{' · 도구 전용' if k.tool else ''})",
                f"  필수: {' '.join(req)}" + ("" if name not in QUOTE_EXEMPT else " (quote 강제 없음)")]
        for g in k.req_any:
            line.append(f"  하나 이상: {' | '.join(g)}")
        if k.alias:
            line.append("  별칭: " + " · ".join(f"{a}→{c}" for a, c in k.alias.items()))
        line.append(f"  선택: {' '.join(opt) or '없음'}")
        out.append("\n".join(line))
    if only and not out:
        return f"모르는 종류 {only!r} — {', '.join(KINDS)}\n"
    out.append("머리 칸(모든 종류): as_of url quote tier source market period origin origin_id minor load_bearing — 이 밖의 칸은 행째 거부된다")
    return "\n".join(out) + "\n"


def _vcols() -> set:
    return set(_colinfo("ev_verdict"))


def _read_rows(path: str) -> list:
    """JSONL(행마다 객체 · # 주석·빈 줄 무시) 또는 JSON 배열."""
    txt = Path(path).read_text(encoding="utf-8")
    if txt.lstrip().startswith("["):
        return json.loads(txt)
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]


# ───────────────────────────── 한 줄 렌더 ─────────────────────────────

def render_line(fact_id) -> str:
    """사실 한 줄(종류별 틀) — 노트·§12·클레임 목록·sources.json 렌더본이 같은 줄을 쓴다. 옛 이전 행은 옛 문장 그대로."""
    if not fact_id:
        return ""
    f = db.q1("SELECT * FROM ev_fact WHERE id=?", (fact_id,))
    if not f:
        return ""
    if f.get("legacy_text"):
        # 이전된 옛 행은 이전 때 종류가 붙어도(공시·일정 일부) 상세 칸이 다 차 있지 않다 — 옛 문장이 원본이다
        return f["legacy_text"]
    k = KINDS.get(f.get("kind"))
    if k is None or not _colinfo(k.table):
        return f.get("kind") or ""
    d = db.q1(f"SELECT * FROM {k.table} WHERE fact_id=?", (fact_id,))
    if not d:
        return f.get("legacy_text") or f.get("kind") or ""
    if f["kind"] == "statement" and d.get("quote") and len(d["quote"]) > 200:
        d = {**d, "quote": d["quote"][:200] + "…"}
    line = _fill(k.render, d)
    if f["kind"] == "disclosure":
        vals = db.q("SELECT name, value, unit FROM co_disclosure_value WHERE fact_id=? ORDER BY ord", (fact_id,))
        if vals:
            line += " · " + "; ".join(f"{v['name']} {_vfmt(v['value']) or ''}{_unit(v['unit'])}".strip() for v in vals)
    if f["kind"] == "event" and d.get("confirmed") is not None:
        line += " (확정)" if d["confirmed"] else " (예정)"
    return line


# 노트 자리표 {{F-id}}가 보여 줄 '그 종류의 본값'(단위 포함) — 한 줄(render_line)이 아니라 문장 안에 끼우는 값 하나.
# 틀 여럿이면 앞에서부터 채워지는 첫 틀(가이던스: 범위 → 중간값 → 하단).
VALUE = {
    "release": ("{actual}{unit}",),
    "policy": ("{title}[ — {status}]",),
    "metric": ("{value}{unit}",),
    "earnings": ("{actual}{unit}",),
    "guidance": ("{low}–{high}{unit}", "{mid}{unit}", "{low}{unit}", "{high}{unit}"),
    "consensus": ("{mean}{unit}",),
    "analyst": ("{target_new}[ {currency}]",),
    "order": ("{amount}[ {currency}]",),
    "disclosure": ("{title}",),
    "action": ("{amount}[ {currency}]", "{shares}주", "{pct}%", "{kind}[ · {status}]"),
    "statement": ('"{quote}"',),
    "level": ("{value}", "{lo}–{hi}"),
    "event": ("{date}[ {time}][ {tz}]",),
    "obs": ("{value}{unit}",),
}


class UnknownField(KeyError):
    """자리표 {{F-id.칸}}의 칸이 그 사실에 없다."""


def render_value(fact_id, field=None) -> str:
    """노트 자리표 값 — field 없음: 종류의 본값(단위 포함 · VALUE) · 'as_of': 기준일(없으면 '미확인') · 'line': 한 줄(render_line) ·
    그 밖: 상세 표 또는 머리 표의 그 칸 값. 옛 이전 행(legacy)의 본값은 옛 문장이다. 없는 사실은 ''(호출자가 먼저 걸러야 한다)."""
    if not fact_id:
        return ""
    f = db.q1("SELECT * FROM ev_fact WHERE id=?", (fact_id,))
    if not f:
        return ""
    if field == "line":
        return render_line(fact_id)
    if field == "as_of":
        return str(f.get("as_of") or "미확인")
    k = KINDS.get(f.get("kind"))
    d = db.q1(f"SELECT * FROM {k.table} WHERE fact_id=?", (fact_id,)) if k and _colinfo(k.table) else None
    if field:
        if d and field in d and field != "fact_id":
            return _vfmt(d[field]) or ""
        if field in f and field not in ("rid", "ko", "extra"):
            return _vfmt(f[field]) or ""
        raise UnknownField(f"{fact_id}에 칸 {field!r}이 없다")
    if not d:
        return f.get("legacy_text") or render_line(fact_id)
    if f["kind"] == "statement" and d.get("quote") and len(d["quote"]) > 200:
        d = {**d, "quote": d["quote"][:200] + "…"}
    for tpl in VALUE.get(f["kind"], ()):
        if all(_vfmt(d.get(m)) is not None for m in _PH.findall(_SEG.sub("", tpl)) if m != "unit"):
            return _fill(tpl, d)
    return render_line(fact_id)


# ───────────────────────────── 등록(add) ─────────────────────────────

def _parse(row: dict, run_market: str) -> dict:
    """입력 행 → {kind, K, head, detail, links, targets, values}. 문제가 있으면 Reject(이유 전부)."""
    kind = row.get("kind")
    if kind not in KINDS:
        raise Reject(f"모르는 종류 {kind!r} — {', '.join(KINDS)}")
    k = KINDS[kind]
    cols = _dcols(k)
    if not cols:
        raise Reject(f"{kind}: 상세 표 {k.table}가 저장소에 없다(db 요청 대기 — db_requests_facts.md)")
    r = {}
    for key, v in row.items():
        if key == "kind":
            continue
        r[k.alias.get(key, key)] = v
    allowed = set(cols) | HEADER_IN | ({"targets"} if kind == "policy" else set()) | ({"values"} if kind == "disclosure" else set())
    unknown = sorted(x for x in r if x not in allowed)
    errs = []
    if unknown:
        hint = f" (상세 칸 'kind'는 {', '.join(k.alias)}로)" if "kind" in unknown and k.alias else ""
        errs.append(f"모르는 칸 {', '.join(unknown)}{hint} — 받는 상세 칸: {', '.join(c for c in cols if c not in _INTERNAL)}"
                    f" · 머리 칸: as_of url quote tier source market period origin origin_id minor (`facts.py kinds --kind {kind}`)")
    types = _colinfo(k.table)
    detail = {}
    for c in cols:
        if c in r and not _empty(r[c]):
            v = r[c]
            detail[c] = v.strip() if isinstance(v, str) else (_num(v) if types.get(c) == "" else v)
            if types.get(c) == "" and isinstance(detail[c], str):
                detail[c] = _num(detail[c])
    try:
        tier = _tier(r.get("tier")) if r.get("tier") is not None else ("data" if k.tool else None)
    except Reject as e:
        errs.append(str(e))
        tier = None
    load_bearing = bool(r["load_bearing"]) if "load_bearing" in r else True
    if r.get("minor"):
        load_bearing = False
    market = str(r.get("market") or run_market or "").upper() or None
    if "market" in cols and not detail.get("market"):
        detail["market"] = market
    as_of = r.get("as_of") if not _empty(r.get("as_of")) else (detail.get(k.as_of) if k.as_of else None)
    url = str(r.get("url") or "").strip() or None
    quote = str(r.get("quote") or "").strip() or None
    if "url" in cols and url and not detail.get("url"):
        detail["url"] = url
    if "quote" in cols and quote and not detail.get("quote"):
        detail["quote"] = quote
    valid = r.get("valid_class") or k.valid
    if valid not in VALID:
        errs.append(f"valid_class는 {'|'.join(VALID)}: {valid!r}")
    origin = r.get("origin") or ("tool" if k.tool else "research")
    if origin not in ORIGINS:
        errs.append(f"origin은 {'|'.join(ORIGINS)}: {origin!r}")
    # 필수 칸 — quote는 load-bearing일 때만(--minor는 강제에서 빠진다 · srcledger와 같다)
    miss = []
    for c in k.req:
        if c == "as_of":
            if _empty(as_of):
                miss.append("as_of")
        elif c == "quote":
            if load_bearing and not quote and not detail.get("quote"):
                miss.append("quote")
        elif c == "url" and "url" not in cols:
            if not url:
                miss.append("url")
        elif _empty(detail.get(c)):
            miss.append(next((a for a, col in k.alias.items() if col == c), c))
    for g in k.req_any:
        if all(_empty(detail.get(c)) for c in g):
            miss.append("|".join(g))
    # 필수 칸 누락은 '새 사실'일 때만 거부한다 — 열쇠가 같은 기존 사실을 다시 인용하는 행은 열쇠 칸만 있으면 된다(add_fact가 판단)
    if url and not re.match(r"^https?://", url):
        errs.append(f"url은 http(s) 링크여야 한다: {url[:60]!r}")
    if errs:
        raise Reject(f"{kind}: " + " · ".join(errs))
    subj = k.subject(detail) if callable(k.subject) else (
        detail.get(k.subject[0][1:]) if k.subject[0].startswith("@") else k.subject[0], detail.get(k.subject[1]))
    period = detail.get(k.period) if k.period else (str(r["period"]) if not _empty(r.get("period")) else None)
    links = []
    for t in ("axis", "sector", "tickers"):
        vs = r.get(t)
        for v in ([vs] if isinstance(vs, str) else (vs or [])):
            links.append(("ticker" if t == "tickers" else t, str(v), None))
    for ln in r.get("links") or []:
        if isinstance(ln, dict) and ln.get("type") and ln.get("id"):
            links.append((str(ln["type"]), str(ln["id"]), ln.get("role")))
    head = {"kind": kind, "subject_type": subj[0], "subject_id": None if subj[1] is None else str(subj[1]),
            "market": market, "period": None if period is None else str(period),
            "as_of": None if as_of is None else str(as_of).strip(), "valid_class": valid, "quote": quote,
            "load_bearing": 1 if load_bearing else 0, "url": url,
            "source_label": (r.get("source_label") or r.get("source") or None), "tier": tier,
            "retrieved": r.get("retrieved") or _now_min(), "origin": origin,
            "origin_id": None if r.get("origin_id") is None else str(r["origin_id"])}
    return {"kind": kind, "K": k, "head": head, "detail": detail, "links": links,
            "targets": r.get("targets") or [], "values": r.get("values") or [],
            "publisher": r.get("publisher") or head["source_label"], "page_title": r.get("page_title"),
            "raw_path": r.get("raw_path"), "missing": miss,
            "source_version": r.get("source_version"), "raw_sha": (str(r.get("raw_sha") or "").strip().lower() or None),
            "retrieved_given": bool(r.get("retrieved"))}


def _key_match(c, p: dict, cols: list, exclude: str = None) -> list:
    k, d, h = p["K"], p["detail"], p["head"]
    w, a = ["f.kind=?"], [p["kind"]]
    for col in cols:
        if col == "@as_of":
            w.append("substr(f.as_of,1,10) IS ?")
            a.append(None if h["as_of"] is None else h["as_of"][:10])
        elif col == "@period":
            w.append("f.period IS ?")
            a.append(h["period"])
        else:
            w.append(f"d.{col} IS ?")
            a.append(d.get(col))
    if exclude:
        w.append("f.id<>?")
        a.append(exclude)
    return [dict(r) for r in c.execute(
        f"SELECT f.* FROM ev_fact f JOIN {k.table} d ON d.fact_id=f.id WHERE {' AND '.join(w)} ORDER BY f.rid", a).fetchall()]


def _source(c, url, tier, publisher=None, raw_path=None, title=None, retrieved=None):
    """출처 표(ev_source) — 정규화한 URL 하나에 한 행을 보장한다(빈 칸만 채움). 본문은 판(ev_source_version)에 있다 —
    v3부터 raw_path·retrieved_at은 쓰지 않는다(옛 기록 칸 · `raw_path` 인자는 받기만 한다)."""
    if not url:
        return None
    nu = _norm_url(url)
    old = c.execute("SELECT * FROM ev_source WHERE url=?", (nu,)).fetchone()
    t = tier if tier != "data" else None
    if old:
        sets = {k: v for k, v in (("publisher", publisher), ("tier", t), ("title", title)) if v and not old[k]}
        if sets:
            c.execute(f"UPDATE ev_source SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), old["id"]))
        return old["id"]
    return c.execute("INSERT INTO ev_source (url, publisher, tier, title) VALUES (?,?,?,?)", (nu, publisher, t, title)).lastrowid


def _resolve_version(c, source_id, quote, version=None, sha=None) -> tuple:
    """★ 사실 → 본문 판(2026-10-19 · Codex U01) — (판 id | None, 'recorded'|'inferred'|None, 경고).
    ① 행이 판 번호(`source_version`)나 sha 앞자리(`raw_sha`)를 주면 그 판(다른 출처의 판이면 거부) → recorded
    ② 그 출처의 판 가운데 인용 조각(8자 이상)이 전부 든 가장 최근 판 → recorded
    ③ 없으면 가장 최근 판 → inferred + 경고(등록 시각은 관측 시각이 아니다 — 지어내지 않는다) · 판이 없으면 (None, None)."""
    if not source_id:
        return None, None, ""
    if version not in (None, "") or sha:
        if version not in (None, ""):
            v = c.execute("SELECT id, source_id FROM ev_source_version WHERE id=?", (int(version),)).fetchone()
        else:
            v = c.execute("SELECT id, source_id FROM ev_source_version WHERE content_sha LIKE ? ORDER BY last_seen DESC LIMIT 1",
                          (sha + "%",)).fetchone()
        if not v:
            raise Reject(f"본문 판 {version or sha}이 저장소에 없다 — `fetch.py`가 찍은 `[원문] v<판>`을 그대로 준다")
        if int(v["source_id"]) != int(source_id):
            raise Reject(f"본문 판 v{v['id']}은 이 사실의 출처(URL)와 다른 출처의 판이다")
        return int(v["id"]), "recorded", ""
    vs = c.execute("SELECT id, path FROM ev_source_version WHERE source_id=? ORDER BY last_seen DESC, id DESC", (source_id,)).fetchall()
    if not vs:
        return None, None, ""
    segs = db._quote_segments(quote)
    if segs:
        for v in vs:
            f = db.raw_abs(v["path"])
            if f is not None and f.is_file() and all(sg in _nospace(f.read_text(encoding="utf-8", errors="ignore")) for sg in segs):
                return int(v["id"]), "recorded", ""
    return int(vs[0]["id"]), "inferred", (f"본문 판 추정 — 인용이 보관된 판에 없어 최신 판 v{vs[0]['id']}에 이었다"
                                           f" (그 판을 보고 썼으면 source_version=<판>을 준다)" if segs else "")


def _version_has_quote(c, vid, quote):
    """본문 판 `vid`에 인용 조각(8자 이상)이 전부 있나 — True/False · 본문을 못 읽거나 인용이 없으면 None(확인 못 함)."""
    if not vid:
        return None
    segs = db._quote_segments(quote)
    if not segs:
        return None
    v = c.execute("SELECT path FROM ev_source_version WHERE id=?", (int(vid),)).fetchone()
    f = db.raw_abs(v["path"]) if v and v["path"] else None
    if f is None or not f.is_file():
        return None
    return all(sg in _nospace(f.read_text(encoding="utf-8", errors="ignore")) for sg in segs)


def _same_evidence(c, f: dict, old_vid, new_vid) -> tuple:
    """근거 본문 판 교체가 같은 근거인가(W03) — (같음, 사유). 같음 = 사실의 인용이 새 판에 있고, 옛 판이 있었다면 옛 판에도 있다
    (옛 판이 없던 사실에 판을 처음 잇는 것은 새 판에 인용이 있으면 같음). 본문·인용이 없어 확인 못 하면 다름으로 본다."""
    q = f.get("quote")
    new_ok = _version_has_quote(c, new_vid, q)
    if new_ok is not True:
        return False, ("새 판에 사실의 인용이 없다" if new_ok is False else "새 판 본문·인용을 확인하지 못했다")
    if old_vid:
        old_ok = _version_has_quote(c, old_vid, q)
        if old_ok is not True:
            return False, ("옛 판에 사실의 인용이 없다" if old_ok is False else "옛 판 본문을 확인하지 못했다")
    return True, "인용이 두 판 모두에 있다(연결 정정)"


def _run_links(c, rid: str) -> dict:
    """이번 run 재료 링크 {정규화 URL: mat_link.id} + {("path", 쿼리 뺀 주소): mat_link.id}(`_run_link_id`가 쓴다)."""
    out = {}
    for r in c.execute("SELECT l.id, l.url FROM mat_link l LEFT JOIN mat_issue i ON i.id=l.issue_id "
                       "WHERE l.run_id=? OR i.run_id=? OR i.first_run=?", (rid, rid, rid)).fetchall():
        if r["url"]:
            n = _norm_url(r["url"])
            out.setdefault(n, r["id"])
            out.setdefault(("path", n.split("?", 1)[0]), r["id"])
    return out


def _run_link_id(c, rid: str, url):
    """이 URL이 이번 run 재료 링크면 그 mat_link.id. 정규화 주소가 같거나, 이 URL에 쿼리가 없고 쿼리 뺀 주소가 같으면 같은 글이다
    (발행분 링크는 `stream=top` 같은 뉴스레터 꼬리가 붙고 리서치는 기사 주소만 적는다 — 옛 crosscheck는 문자열 포함으로 이걸 잡았다)."""
    if not url:
        return None
    n = _norm_url(url)
    links = _run_links(c, rid)
    if n in links:
        return links[n]
    bare = n.split("?", 1)[0]
    if "?" not in n and urlsplit(bare).path not in ("", "/"):
        return links.get(("path", bare))
    return None


def _next_id(c) -> str:
    n = c.execute("SELECT MAX(CAST(substr(id,3) AS INTEGER)) FROM ev_fact WHERE id LIKE 'F-%'").fetchone()[0] or 0
    return f"F-{n + 1:06d}"


def _attach_run(c, fid: str, rid: str, st: str, mk: str) -> int:
    r = c.execute("SELECT s_no FROM ev_fact_run WHERE fact_id=? AND run_id=?", (fid, rid)).fetchone()
    if r:
        return r[0]
    s = (c.execute("SELECT COALESCE(MAX(s_no),0) FROM ev_fact_run WHERE run_id=?", (rid,)).fetchone()[0] or 0) + 1
    c.execute("INSERT INTO ev_fact_run (fact_id, run_id, s_no, added_at) VALUES (?,?,?,?)", (fid, rid, s, db.now_iso()))
    # 옛 sources.json의 runs 칸("us:261016") — 렌더본(db.sources_rows)이 읽는다
    pid = c.execute("SELECT rid FROM ev_fact WHERE id=?", (fid,)).fetchone()[0]
    tag = f"{mk}:{st}"
    if not c.execute("SELECT 1 FROM ev_fact_run_label WHERE pid=? AND value=?", (pid, tag)).fetchone():
        n = c.execute("SELECT COUNT(*) FROM ev_fact_run_label WHERE pid=?", (pid,)).fetchone()[0]
        c.execute("INSERT INTO ev_fact_run_label (pid, src, ord, value) VALUES (?,?,?,?)", (pid, "runs", n, tag))
    return s


def _research_check(c, targets: list, topic: str, rid: str) -> None:
    ts = db.now_iso()
    for stype, sid in targets:
        if not stype or not sid:
            continue
        for tp in (topic, ""):
            if tp:
                n = c.execute("SELECT COUNT(*) FROM ev_fact WHERE subject_type=? AND subject_id=? AND kind=?",
                              (stype, sid, tp)).fetchone()[0]
            else:
                n = c.execute("SELECT COUNT(*) FROM ev_fact f WHERE (f.subject_type=? AND f.subject_id=?) OR f.id IN "
                              "(SELECT fact_id FROM ev_fact_link WHERE target_type=? AND target_id=?)",
                              (stype, sid, stype, sid)).fetchone()[0]
            c.execute("INSERT INTO ev_research_check (subject_type, subject_id, topic, last_checked, run_id, n_facts) "
                      "VALUES (?,?,?,?,?,?) ON CONFLICT(subject_type, subject_id, topic) DO UPDATE SET "
                      "last_checked=excluded.last_checked, run_id=excluded.run_id, n_facts=excluded.n_facts",
                      (stype, sid, tp, ts, rid, n))


def run_counts(rid: str, c=None) -> dict:
    """이번 run 사실 — 신규(조사로 새로) · 되풀이(이번 run 재료 링크와 같은 URL) · 재사용(앞 run이 세운 것). 도구 사실은 세지 않는다."""
    c = c or db.connect()
    r = c.execute("SELECT SUM(CASE WHEN f.created_run=? AND COALESCE(f.repeat,0)=0 THEN 1 ELSE 0 END),"
                  " SUM(CASE WHEN f.created_run=? AND f.repeat=1 THEN 1 ELSE 0 END),"
                  " SUM(CASE WHEN f.created_run IS NOT ? THEN 1 ELSE 0 END)"
                  " FROM ev_fact_run r JOIN ev_fact f ON f.id=r.fact_id"
                  " WHERE r.run_id=? AND COALESCE(f.origin,'')<>'tool'", (rid, rid, rid, rid)).fetchone()
    return {"facts_new": r[0] or 0, "facts_repeat": r[1] or 0, "facts_reused": r[2] or 0}


def _write_counts(c, rid: str, st: str, mk: str) -> dict:
    cnt = run_counts(rid, c)
    db.run_start(st, mk)
    c.execute("UPDATE ex_run SET facts_new=?, facts_reused=?, facts_repeat=? WHERE run_id=?",
              (cnt["facts_new"], cnt["facts_reused"], cnt["facts_repeat"], rid))
    return cnt


def _snapshot(c, fid: str) -> dict:
    """사실 한 판의 전부 — 머리(ev_fact 모든 칸) + 세부(종류 표 행). 판 기록(ev_fact_version.snapshot)에 그대로 남겨
    `facts.py show --rev`·`correct --from-rev`가 그 판을 되살린다(옛 트리거는 머리 6칸만 남겼다)."""
    f = dict(c.execute("SELECT * FROM ev_fact WHERE id=?", (fid,)).fetchone())
    k = KINDS.get(f["kind"])
    d = c.execute(f"SELECT * FROM {k.table} WHERE fact_id=?", (fid,)).fetchone() if k else None
    return {"kind": f["kind"], "header": f, "detail": dict(d) if d else {}}


def _bump(c, fid: str, hd: dict, dd: dict, old: dict, new: dict, reason: str, verdict_id=None, rid=None,
          value_changed: bool = True, reset_review: bool = True) -> tuple:
    """★ 사실 판을 올리는 유일한 곳(2026-10-19 · Codex R04·S03) — 교정(`correct`)과 재사용 보강(`add_fact`)이 둘 다 부른다.
    ① 교정 행(옛·새 칸 · 사유) ② 판 기록(올리기 전 판의 머리+세부 전부 · 교정 번호) ③ 세부 칸 ④ 머리 칸 + rev+1 ·
    값이 바뀌면 value_rev = 새 판(그 앞 판정은 더 세지 않는다 → REVERIFY)이고 검증 상태·티어·시각·run을 비운다(live·interp 제외 —
    다음 큐가 다시 본다). 표기만 바뀌면(출처 이름·티어 등) rev만 오르고 판정은 그대로 센다. 반환 (교정 id, 새 rev)."""
    f = dict(c.execute("SELECT * FROM ev_fact WHERE id=?", (fid,)).fetchone())
    k = KINDS.get(f["kind"])
    ts = db.now_iso()
    cid = c.execute("INSERT INTO ev_correction (fact_id, run_id, old, new, reason, verdict_id, applied_at) VALUES (?,?,?,?,?,?,?)",
                    (fid, rid, json.dumps(old, ensure_ascii=False, default=str), json.dumps(new, ensure_ascii=False, default=str),
                     reason, None if verdict_id is None else int(verdict_id), ts)).lastrowid
    # 판 기록을 UPDATE 전에 남긴다 — 트리거(tg_fact_version)는 같은 판 행이 있으면 건너뛴다(옛 6칸 기록이 겹쳐 생기지 않게).
    #   시각 형식은 트리거와 같은 UTC 무표기(이전 v2가 판정 시각과 이 시각을 견줘 판정 판을 정했다)
    c.execute("INSERT INTO ev_fact_version (fact_id, version, snapshot, changed_at, correction_id, source_version_id) VALUES (?,?,?,?,?,?)",
              (fid, int(f.get("rev") or 1), json.dumps(_snapshot(c, fid), ensure_ascii=False, default=str),
               datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), cid, f.get("source_version_id")))
    if dd and k:
        c.execute(f"UPDATE {k.table} SET {', '.join(x + '=?' for x in dd)} WHERE fact_id=?", (*dd.values(), fid))
    hd = dict(hd)
    if value_changed and (f.get("valid_class") or "") not in ("live", "interp"):
        hd.update({"verified_status": "none", "verified_at": None, "verified_tier": None, "verified_run": None, "verified_rev": None})
    if reset_review:
        hd["needs_review"] = 0
    hd["updated_at"] = ts
    c.execute(f"UPDATE ev_fact SET {', '.join(x + '=?' for x in hd)}, rev=COALESCE(rev,1)+1,"
              " value_rev=CASE WHEN ? THEN COALESCE(rev,1)+1 ELSE value_rev END WHERE id=?",
              (*hd.values(), 1 if value_changed else 0, fid))
    return cid, c.execute("SELECT rev FROM ev_fact WHERE id=?", (fid,)).fetchone()[0]


def add_fact(row: dict, run) -> dict:
    """사실 한 행을 넣거나 기존 행을 재사용한다. 반환 {id, s_no, status(신규|재사용|되풀이), line, warnings, n_run}.
    거부면 Reject — 아무것도 쓰지 않는다(반쪽 행 금지)."""
    rid, st, mk = _run(run)
    p = _parse(row, mk.upper())
    k, h, d = p["K"], p["head"], p["detail"]
    warns = []
    with db.tx() as c:
        hit = _key_match(c, p, k.key)
        if hit:
            ex = hit[0]
            fid, status = ex["id"], "재사용"
            old = dict(c.execute(f"SELECT * FROM {k.table} WHERE fact_id=?", (fid,)).fetchone())
            fill, clash = {}, []
            for col, v in d.items():
                if _empty(old.get(col)):
                    fill[col] = v
                elif str(_vfmt(old.get(col))) != str(_vfmt(v)) and col not in ("market",):
                    clash.append(f"{col} {_vfmt(old.get(col))} ≠ {_vfmt(v)}")
            hfill = {col: h[col] for col in ("as_of", "url", "quote", "source_label", "tier", "period")
                     if _empty(ex.get(col)) and not _empty(h.get(col))}
            if fill or hfill:
                # ★ 재사용 보강도 값 변경이다(2026-10-19 · Codex S03) — 빈 칸을 채우면 판이 오르고, 값 칸(종류 표 세부칸 — market 제외 —
                #   또는 as_of·quote·url·period)이면 검증 상태가 none으로 돌아가 다음 큐가 다시 본다. 예전에는 조용히 UPDATE만 했다.
                old_v = {**{col: old.get(col) for col in fill}, **{col: ex.get(col) for col in hfill}}
                new_v = {**fill, **hfill}
                if "url" in hfill:
                    hfill["source_id"] = _source(c, h["url"], h["tier"], p["publisher"], p["raw_path"], p["page_title"])
                if not ex.get("source_version_id"):
                    vid, vlink, _w = _resolve_version(c, hfill.get("source_id") or ex.get("source_id"), h["quote"] or ex.get("quote"),
                                                      p["source_version"], p["raw_sha"])
                    if vid:
                        hfill.update({"source_version_id": vid, "source_version_link": vlink})
                vchg = bool(set(fill) - {"market"}) or bool(set(hfill) & VALUE_HEADER)
                _, nrev = _bump(c, fid, hfill, fill, old_v, new_v, "재사용 보강 — 빈 칸 채움", None, rid, vchg, reset_review=False)
                warns.append(f"재사용 보강 — 빈 칸 {', '.join(new_v)}을 채웠다 → rev {nrev}"
                             + (" · 값이 바뀌어 검증 상태 none(다시 검증 큐)" if vchg else " · 표기만(판정은 그대로 센다)"))
            if h["url"] and ex.get("url") and _norm_url(h["url"]) != _norm_url(ex["url"]):
                warns.append(f"다른 출처 URL — 기존 유지({ex['url'][:60]})")
            if clash:
                warns.append("값 다름 — 기존 유지: " + "; ".join(clash) + f" (고치려면 facts.py correct {fid} --set …)")
            if h["tier"] in (1, 2) and h["load_bearing"]:
                q = h["quote"] or ex.get("quote") or ""
                miss = [w for w, ok in (("원문 인용(출처 문장 그대로 20자 이상)", len(q) >= QUOTE_MIN or p["kind"] in QUOTE_EXEMPT),
                                        ("URL", bool(h["url"] or ex.get("url")))) if not ok]
                if miss:
                    warns.append(f"옛 사실 재인용 — {' · '.join(miss)} 없음 (이번에 quote를 주면 채운다)")
        else:
            if p["missing"]:
                raise Reject(f"{p['kind']}: 필수 칸 없음 {', '.join(p['missing'])}")
            if h["tier"] in (1, 2) and h["load_bearing"]:
                miss = [w for w, ok in (("원문 인용(출처 문장 그대로 20자 이상)",
                                         len(h["quote"] or "") >= QUOTE_MIN or p["kind"] in QUOTE_EXEMPT),
                                        ("URL", bool(h["url"]))) if not ok]
                if miss:
                    raise Reject(f"{p['kind']}: T{h['tier']} 사실은 {' · '.join(miss)}이 있어야 한다 "
                                 "(리서치·심층 결과에서 그 사실이 나온 원문 문장을 quote로 · 못 받았으면 노트에 [AI]가 아니라 [확인 안 됨])")
            fid = _next_id(c)
            link_id = _run_link_id(c, rid, h["url"])
            # 되풀이 = 리서치(origin research)가 이번 run 재료 링크를 다시 판 것 — 재료에서 꺼낸 사실(issue·link·ws_event·dart)은
            # 원래 재료가 출처라 되풀이가 아니다(재료 되풀이 비율 지표가 부풀지 않게)
            repeat = 1 if link_id and (h["origin"] or "research") == "research" else 0
            origin_id = h["origin_id"] or (f"mat_link:{link_id}" if link_id else None)
            sid = _source(c, h["url"], h["tier"], p["publisher"], p["raw_path"], p["page_title"])
            vid, vlink, vwarn = _resolve_version(c, sid, h["quote"], p["source_version"], p["raw_sha"])
            if vwarn:
                warns.append(vwarn)
            retrieved = h["retrieved"]
            if vid and not p["retrieved_given"]:
                # retrieved = 그 판을 본 시각(마지막 성공 시도) — 등록 시각이 아니다(v3 · 원문이 다음 조회에 바뀌어도 시점이 남는다)
                seen = c.execute("SELECT MAX(at) FROM ev_fetch WHERE version_id=?", (vid,)).fetchone()[0]
                if seen:
                    retrieved = str(seen)[:16].replace("T", " ")
            ts = db.now_iso()
            ord_ = c.execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM ev_fact").fetchone()[0]
            c.execute("INSERT INTO ev_fact (id, kind, subject_type, subject_id, market, period, as_of, valid_class, quote,"
                      " load_bearing, verified_status, source_id, url, source_label, tier, retrieved, origin, origin_id,"
                      " created_run, repeat, needs_review, ko, ord, rev, created_at, updated_at, source_version_id, source_version_link)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,'none',?,?,?,?,?,?,?,?,?,0,?,?,1,?,?,?,?)",
                      (fid, p["kind"], h["subject_type"], h["subject_id"], h["market"], h["period"], h["as_of"],
                       h["valid_class"], h["quote"], h["load_bearing"], sid, h["url"], h["source_label"], h["tier"],
                       retrieved, h["origin"], origin_id, rid, repeat,
                       # ko에 id가 있어야 db.sources_rows가 이 행을 종류별 사실로 알아본다
                       json.dumps(["id"]), ord_, ts, ts, vid, vlink))
            dd = {"fact_id": fid, **d}
            c.execute(f"INSERT INTO {k.table} ({', '.join(dd)}) VALUES ({', '.join('?' * len(dd))})", tuple(dd.values()))
            for t in p["targets"]:
                if isinstance(t, dict) and t.get("type") and t.get("id"):
                    c.execute("INSERT OR IGNORE INTO ma_policy_target (fact_id, target_type, target_id) VALUES (?,?,?)",
                              (fid, str(t["type"]), str(t["id"])))
            for i, v in enumerate(p["values"]):
                if isinstance(v, dict) and v.get("name"):
                    c.execute("INSERT INTO co_disclosure_value (fact_id, ord, name, value, unit) VALUES (?,?,?,?,?)",
                              (fid, i, v["name"], _num(v.get("value")), v.get("unit")))
            status = "되풀이" if repeat else "신규"
            if h["valid_class"] == "dated" and k.slot and h["as_of"]:
                vals = [h["period"] if s == "@period" else d.get(s) for s in k.slot]
                if all(not _empty(v) for v in vals):
                    for o in _key_match(c, p, k.slot, exclude=fid):
                        if o["verified_status"] == "expired":
                            continue
                        if (o["as_of"] or "") < h["as_of"]:
                            c.execute("UPDATE ev_fact SET verified_status='expired', updated_at=? WHERE id=?", (ts, o["id"]))
                            warns.append(f"시효: {o['id']}(as-of {o['as_of']}) → expired · 새 as-of {h['as_of']}")
                        elif (o["as_of"] or "") > h["as_of"]:
                            c.execute("UPDATE ev_fact SET verified_status='expired' WHERE id=?", (fid,))
                            warns.append(f"시효: 더 새 행 {o['id']}(as-of {o['as_of']})가 있어 이 행은 expired로 넣었다")
        for stype, sid_, role in p["links"]:
            c.execute("INSERT OR IGNORE INTO ev_fact_link (fact_id, target_type, target_id, role) VALUES (?,?,?,?)",
                      (fid, stype, sid_, role))
        s_no = _attach_run(c, fid, rid, st, mk)
        _research_check(c, [(h["subject_type"], h["subject_id"])] + [(a, b) for a, b, _ in p["links"]], p["kind"], rid)
        _write_counts(c, rid, st, mk)
        db.mark_export("sources")      # journal/sources.json 렌더본 — 커밋 뒤 db가 한 번 다시 쓴다
        n_run = c.execute("SELECT COUNT(*) FROM ev_fact_run WHERE run_id=?", (rid,)).fetchone()[0]
        cur = dict(c.execute("SELECT * FROM ev_fact WHERE id=?", (fid,)).fetchone())
        n_runs = c.execute("SELECT COUNT(*) FROM ev_fact_run WHERE fact_id=?", (fid,)).fetchone()[0]
    text = f"{p['kind']} {render_line(fid)}"[:56]
    if status == "재사용":
        line = (f"재사용: {fid} [S{s_no}] {text} — 이미 원장에 있다. as-of {cur['as_of'] or '미확인'} · retrieved "
                f"{cur['retrieved'] or '미기록'}(원래 시점 유지) · 인용 run {n_runs}개\n"
                f"  ※ **다시 조사하지 않아도 된다.** 세 시점 중 새로 찍히는 건 written뿐이다.")
    else:
        line = (f"기록: {fid} [S{s_no}] {text} · as-of {cur['as_of'] or '미확인'} · retrieved {cur['retrieved']} "
                f"(이 run {n_run}행) · " + ("신규" if status == "신규" else "되풀이 — 이번 run 재료 링크와 같은 URL(조사가 재료를 되풀이했다)"))
    return {"id": fid, "s_no": s_no, "status": status, "line": line, "warnings": warns, "n_run": n_run}


def add_rows(rows: list, run, minor: bool = False, err=None, out=None) -> tuple:
    """여러 행 — 묶음 하나를 한 트랜잭션으로 넣는다(거부된 행만 빠지고 나머지는 들어간다). 반환 (rc, 결과 목록)."""
    out, err = out or sys.stdout, err or sys.stderr
    rc, res = 0, []
    # 묶음 하나 = 트랜잭션 하나(렌더본 내보내기도 커밋 뒤 한 번). 거부(Reject)는 그 행의 쓰기 전에 나므로 그 행만 빠진다 —
    # 그 밖의 오류는 묶음 전체를 되돌린다(반쪽 행 금지). 같은 열쇠로 다시 넣으면 재사용되므로 다시 돌려도 안전하다.
    with db.tx():
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                print(f"  ✗ [{i}] 등록 거부 — 객체가 아니다: {str(row)[:60]}", file=err)
                rc = 2
                continue
            if minor and "minor" not in row and "load_bearing" not in row:
                row = {**row, "minor": True}
            try:
                r = add_fact(row, run)
            except Reject as e:
                print(f"  ✗ [{i}] 등록 거부 — {e}", file=err)
                rc = 2
                continue
            print(r["line"], file=out)
            for w in r["warnings"]:
                print(f"  ※ {w}", file=err)
            res.append(r)
    return rc, res


# ───────────────────────────── 재료 언급(mention) · 뉴스 전 ─────────────────────────────

def _expand_rest(rows: list, rid: str, st: str, mk: str, err) -> list:
    """섹터 '*' 행 → 그 발행분(링크·세상공부)에 아직 행이 없는 로스터 섹터 전부로 편다 — 다루지 않은 섹터를 '해당 없음' 한 줄로
    채워 §3 보드 전수(발행분 × 유니버스 섹터)를 맞춘다. 같은 묶음 안 앞 행이 이미 다룬 섹터는 빼고 편다."""
    roster = None
    out = []
    for r in rows:
        if not (isinstance(r, dict) and r.get("subject_type") == "sector" and str(r.get("subject_id") or "").strip() == "*"):
            out.append(r)
            continue
        if roster is None:
            roster = board_roster(st, mk)[0]
            alias = _sector_aliases(roster)
        if not roster:
            print(f"  ✗ 섹터 '*' 행을 펴지 못했다 — 이 run({rid})의 섹터 보드 기록이 없다(1단 capture가 적재한다)", file=err)
            out.append({**r, "_bad": "섹터 보드 기록 없음"})
            continue
        src = ("issue_id", "link_id", "ws_event_id")
        key = tuple(r.get(k) for k in src)
        have = {alias.get(str(x["subject_id"]).strip()) for x in db.q(
            "SELECT subject_id FROM mat_mention WHERE run_id=? AND subject_type='sector' AND issue_id IS ? AND link_id IS ?"
            " AND ws_event_id IS ?", (rid, *key))}
        have |= {alias.get(str(x.get("subject_id")).strip()) for x in out
                 if isinstance(x, dict) and x.get("subject_type") == "sector" and tuple(x.get(k) for k in src) == key}
        for sec in roster:
            if sec["key"] not in have:
                out.append({**{k: v for k, v in r.items() if k != "subject_id"}, "subject_id": sec["key"],
                            "board_verdict": r.get("board_verdict") or "해당 없음"})
    return out


def add_mentions(rows: list, run, out=None, err=None) -> int:
    """재료가 종목·섹터·축을 어떻게 다뤘나 — mat_mention(+mat_mention_fact). url을 주면 이번 run 재료 링크로 잇는다.
    섹터 행의 subject_id는 보드 코드(0013·XLK) 또는 보드 이름 · '*'는 그 발행분에 아직 행이 없는 로스터 섹터 전부('해당 없음')."""
    out, err = out or sys.stdout, err or sys.stderr
    rid, st, mk = _run(run)
    rc = 0
    # 발행분 번호(issue_no)를 먼저 id로 바꾼다 — '*' 펴기가 발행분마다 따로 세려면 id가 있어야 한다
    # (번호인 채로 펴면 모든 행의 발행분이 비어 첫 '*'만 펴지고 나머지 발행분은 빈다)
    ids = None
    for r in rows:
        if isinstance(r, dict) and not r.get("issue_id") and r.get("issue_no"):
            ids = ids if ids is not None else db.material_issues_for_run(rid)
            n = int(r["issue_no"])
            if 1 <= n <= len(ids):
                r["issue_id"] = ids[n - 1]
            else:
                r["_bad"] = f"발행분 번호 {n}가 이 run 재료({len(ids)}통) 밖이다"
    rows = _expand_rest(rows, rid, st, mk, err)
    alias = None
    for i, r in enumerate(rows, 1):
        if r.get("_bad") and r.get("subject_type") and r.get("subject_id"):
            print(f"  ✗ [{i}] 건너뜀 — {r['_bad']}", file=err)
            rc = 2
            continue
        if not r.get("subject_type") or not r.get("subject_id") or r.get("_bad"):
            print(f"  ✗ [{i}] 건너뜀 — subject_type·subject_id 필요: {str(r)[:80]}", file=err)
            rc = 2
            continue
        if r["subject_type"] == "sector":
            if alias is None:
                alias = _sector_aliases(board_roster(st, mk)[0])
            if alias and str(r["subject_id"]).strip() not in alias:
                print(f"  ※ [{i}] 섹터 {r['subject_id']!r}가 이 run 보드 로스터에 없다 — §3 보드 행에 붙지 않는다(코드·보드 이름으로)",
                      file=err)
        try:
            with db.tx() as c:
                link_id = r.get("link_id")
                if not link_id and r.get("url"):
                    link_id = _run_link_id(c, rid, r["url"])
                mid = c.execute("INSERT INTO mat_mention (issue_id, link_id, ws_event_id, run_id, subject_type, subject_id, stance,"
                                " headline, board_verdict, legacy, created_at) VALUES (?,?,?,?,?,?,?,?,?,0,?)",
                                (r.get("issue_id"), link_id, r.get("ws_event_id"), rid, str(r["subject_type"]),
                                 str(r["subject_id"]), r.get("stance"), r.get("headline"), r.get("board_verdict"),
                                 r.get("created_at") or db.now_iso())).lastrowid
                for fid in r.get("fact_ids") or []:
                    if not c.execute("SELECT 1 FROM ev_fact WHERE id=?", (fid,)).fetchone():
                        raise Reject(f"없는 사실 {fid}")
                    c.execute("INSERT OR IGNORE INTO mat_mention_fact (mention_id, fact_id) VALUES (?,?)", (mid, fid))
        except Exception as e:  # noqa: BLE001 — 행 하나의 실패가 나머지를 막지 않는다(그 행은 되돌린다)
            print(f"  ✗ [{i}] 건너뜀 — {e}", file=err)
            rc = 2
            continue
        print(f"M-{mid} {r['subject_type']}:{r['subject_id']} {r.get('stance') or ''} {str(r.get('headline') or '')[:60]}".rstrip(),
              file=out)
    return rc


# ───────────────────────────── 노트 §3 섹터 보드(렌더) ─────────────────────────────
# §3 = 1단 보드 기록(저장소 op_sector_history — 그 세션 전 업종 등락) × 섹터 상태(dim_sector — 등급·갱신·연쇄·상태) × 재료 언급
# (mat_mention — 발행분이 그 섹터를 어떻게 다뤘나). 머리줄·칸·생성줄은 stage.build_board와 글자 그대로 같다(게이트가 센다).
# 다루지 않은 섹터도 행으로 남는다(누락을 침묵이 아니라 행으로).
NO_MENTION = ("해당 없음", "언급 없음", "무관")


def _session_day(stamp: str, market: str) -> str:
    r = db.run_get(stamp, market)
    if r and r.get("session_date"):
        return str(r["session_date"])[:10]
    st = str(stamp)
    return f"20{st[:2]}-{st[2:4]}-{st[4:6]}" if re.fullmatch(r"\d{6}", st) else ""


def board_roster(stamp: str, market: str) -> tuple:
    """이 run의 섹터 로스터 — (행 목록[{key, name, d1, d5, d20, close}], 보드 기록 날짜, 기준지수 등락).
    그 세션 날짜의 보드 기록(1단 `capture`가 적재)이 원본이다. 없으면 ([], None, None) — 지난 날 시세로 대신 채우지 않는다."""
    mk = str(market).lower()
    day = _session_day(stamp, mk)
    rec = None
    for r in db.log_rows("sector_history"):
        if str(r.get("market") or "").lower() == mk and str(r.get("date") or "") == day:
            rec = r                                    # 같은 날 재실행은 db가 한 행으로 맞춘다 — 그래도 마지막 것
    if not rec:
        return [], None, None
    return [s for s in rec.get("sectors") or [] if s.get("key")], rec.get("date"), rec.get("benchmark_pct")


def _sector_aliases(roster: list) -> dict:
    """언급의 subject_id(코드·보드 이름·지도 이름·괄호 뺀 이름) → 섹터 코드."""
    names = {s.get("code"): s.get("name") for s in db.market_map_load().get("sectors") or [] if s.get("code")}
    out = {}
    for r in roster:
        code = str(r["key"])
        for a in (code, r.get("name"), names.get(code), re.sub(r"\s*\([^)]*\)\s*$", "", str(r.get("name") or "")),
                  re.sub(r"\s*\([^)]*\)\s*$", "", str(names.get(code) or ""))):
            if a:
                out.setdefault(str(a).strip(), code)
    return out


def board_mentions(stamp: str, market: str, roster: list = None) -> dict:
    """이 run의 재료 언급을 섹터 코드로 모은다 — {code: [언급 행(+source·day)]}. 종목 언급은 dim_company.sector_code로 그 섹터에 붙는다.
    '해당 없음'류 행(빈 칸 채움)도 들어 있다 — 렌더는 거르고, 전수 검사(crosscheck)는 센다."""
    rid, st, mk = _run((stamp, market))
    roster = roster if roster is not None else board_roster(st, mk)[0]
    alias = _sector_aliases(roster)
    sec_of = {r["ticker"]: r["sector_code"] for r in db.q("SELECT ticker, sector_code FROM dim_company WHERE sector_code IS NOT NULL")}
    out = {}
    for m in db.q("SELECT m.*, i.source, i.sender, i.day, i.id AS eff_issue FROM mat_mention m LEFT JOIN mat_issue i"
                  " ON i.id=COALESCE(m.issue_id, (SELECT l.issue_id FROM mat_link l WHERE l.id=m.link_id))"
                  " WHERE m.run_id=? ORDER BY m.id", (rid,)):
        sid = str(m.get("subject_id") or "").strip()
        code = alias.get(sid) if m["subject_type"] == "sector" else (
            sec_of.get(sid) if m["subject_type"] == "ticker" else None)
        if code is None and m["subject_type"] == "ticker" and sec_of.get(sid) in [r["key"] for r in roster]:
            code = sec_of[sid]
        if code is not None:
            m["fact_ids"] = [r["fact_id"] for r in db.q("SELECT fact_id FROM mat_mention_fact WHERE mention_id=?", (m["id"],))]
            out.setdefault(code, []).append(m)
    return out


def _cellx(s) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ").strip()


def _mention_cell(ms: list) -> str:
    """상태 칸 뒤에 붙는 재료 언급 — ` · 재료 n건: 출처 stance 한 줄(판정) [F-…]; …`(셋까지)."""
    shown = [m for m in ms if not (str(m.get("board_verdict") or "").strip() in NO_MENTION and not m.get("headline"))]
    if not shown:
        return ""
    parts = []
    for m in shown[:3]:
        who = m.get("source") or m.get("sender") or ("링크" if m.get("link_id") else "세상공부" if m.get("ws_event_id") else "재료")
        txt = f"{who} {m.get('stance') or ''} {str(m.get('headline') or '')[:70]}".replace("  ", " ").strip()
        if m.get("board_verdict"):
            txt += f"({m['board_verdict']})"
        if m.get("fact_ids"):
            txt += " " + ",".join(m["fact_ids"][:3])
        parts.append(_cellx(txt))
    more = f" 외 {len(shown) - 3}건" if len(shown) > 3 else ""
    return f" · 재료 {len(shown)}건: " + "; ".join(parts) + more


def render_board(stamp: str, market: str):
    """노트 §3 섹터 보드(마크다운) — stage.build_board와 같은 머리줄·칸·생성줄(+ 재료 언급 요약 줄). 보드 기록이 없으면 None
    (지난 날 시세로 채우지 않는다 — 호출자는 §3을 그대로 둔다)."""
    import stage as _stage                                # 생성줄 문구의 원본은 stage.py 한 곳(GEN_NOTICE·gen_coverage_line)
    rid, st, mk = _run((stamp, market))
    roster, day, bench = board_roster(st, mk)
    if not roster:
        return None
    state = {s["code"]: s for s in db.market_map_load().get("sectors") or [] if s.get("code")}
    ments = board_mentions(st, mk, roster)

    def pct(v):
        return "—" if v is None else f"{v:+.2f}%"
    md = ["| 섹터 | 오늘 | 대비 | 5일 | 20일 | 등급 | 갱신 | 연쇄 | 상태 |",
          "|---|---|---|---|---|---|---|---|---|"]
    unstudied, studied = [], 0
    for r in roster:
        s = state.get(r["key"], {})
        grade = s.get("grade") or "—"
        upd = s.get("updated") or "—"
        nchain = len(s.get("chains") or [])
        status = s.get("status") or "**미조사**"
        if grade == "—":
            unstudied.append(r["name"])
        else:
            studied += 1
        exc = r["d1"] - (bench or 0) if r.get("d1") is not None else None
        md.append("| " + " | ".join([str(r["name"]), pct(r.get("d1")), pct(exc), pct(r.get("d5")), pct(r.get("d20")),
                                     grade, upd, str(nchain or "—"), status + _mention_cell(ments.get(r["key"], []))]) + " |")
    md.append("")
    md.append(_stage.GEN_NOTICE)
    md.append(_stage.gen_coverage_line(studied, len(roster), unstudied))
    issues = db.material_issues_for_run(rid)
    hit = [k for k, ms in ments.items() if _mention_cell(ms)]
    n_rows = sum(len(ms) for ms in ments.values())
    md.append(f"재료 언급 — 발행분 {len(issues)}통 · 언급 {n_rows}행 · 다룬 섹터 {len(hit)}/{len(roster)} (보드 기록 {day} · `facts.py mention`)")
    return "\n".join(md)


def before_news(stamp: str, market: str) -> dict:
    """이번 run 조사가 재료보다 먼저 찾은 사실 수(그 주체의 첫 재료 언급이 더 늦거나 없다) · 재료 지연(발행 → 처음 읽은 run) → ex_run."""
    rid, st, mk = _run((stamp, market))
    facts = db.q("SELECT id, subject_type, subject_id, created_at FROM ev_fact WHERE created_run=? AND origin='research'"
                 " AND COALESCE(repeat,0)=0", (rid,))
    early = []
    for f in facts:
        r = db.q1("SELECT MIN(created_at) AS t FROM mat_mention WHERE subject_type=? AND subject_id=?",
                  (f["subject_type"], f["subject_id"]))
        first, made = _dt(r["t"] if r else None), _dt(f["created_at"])
        if first is None or (made is not None and first > made):
            early.append(f["id"])
    run = db.q1("SELECT capture_at, started FROM ex_run WHERE run_id=?", (rid,)) or {}
    t_run = _dt(run.get("capture_at") or run.get("started"))
    hours = []
    for i in db.q("SELECT received_at, day FROM mat_issue WHERE first_run=? OR (first_run IS NULL AND run_id=?)", (rid, rid)):
        t_iss = _dt(i["received_at"] or i["day"])
        if t_iss and t_run:
            hours.append((t_run - t_iss).total_seconds() / 3600)
    delay = round(statistics.mean(hours), 1) if hours else None
    with db.tx() as c:
        cnt = _write_counts(c, rid, st, mk)
        c.execute("UPDATE ex_run SET before_news_n=?, material_delay_h=? WHERE run_id=?", (len(early), delay, rid))
    return {"before_news_n": len(early), "facts": early, "research_facts": len(facts), "material_delay_h": delay,
            "issues": len(hours), **cnt}


# ───────────────────────────── 이미 아는 사실(known) · 재사용 후보 ─────────────────────────────

def _status_label(f: dict) -> str:
    if f.get("valid_class") == "live":
        return "도구 관측(검증 대상 아님)"
    if f.get("valid_class") == "interp":
        return "해석([추정] 표시 대상)"
    s = f.get("verified_status") or "none"
    if s == "verified":
        return f"검증됨({_tlabel(f.get('verified_tier'))} · {f.get('verified_run') or '?'})"
    return {"none": "미검증", "expired": "시효 지남(다시 확인)", "disputed": "판정 불일치(재검토)"}.get(s, s)


def _since_days(s) -> float:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([dh]?)\s*", str(s or "14d"))
    if not m:
        raise SystemExit(f"--since는 14d · 48h 꼴: {s!r}")
    n = float(m.group(1))
    return n / 24 if m.group(2) == "h" else n


def known(ticker=None, sector=None, axis=None, indicator=None, market=None, since="14d", stamp=None, limit=5) -> str:
    """리서치 전에 여는 목록 — 이 주체에 대해 이미 아는 사실(종류별 최신 · as-of · 검증 상태), 마지막 조사 시점,
    재료가 어떻게 다뤘나, 이전된 옛 문장(1,833행) 검색 결과."""
    tgt = [("ticker", ticker), ("sector", sector), ("axis", axis), ("indicator", indicator)]
    tgt = [(t, v) for t, v in tgt if v]
    if len(tgt) != 1:
        raise SystemExit("--ticker · --sector · --axis · --indicator 중 하나만")
    stype, sid = tgt[0]
    days = _since_days(since)
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    subjects = [(stype, sid)]
    if stype == "sector":
        subjects += [("ticker", r["ticker"]) for r in db.q("SELECT ticker FROM dim_company WHERE sector_code=? AND in_watchlist=1", (sid,))]
    w, a = [], []
    for t, v in subjects:
        w.append("(f.subject_type=? AND f.subject_id=?)")
        a += [t, v]
    w.append("f.id IN (SELECT fact_id FROM ev_fact_link WHERE target_type=? AND target_id=?)")
    a += [stype, sid]
    sql = (f"SELECT f.* FROM ev_fact f WHERE f.kind<>'legacy' AND ({' OR '.join(w)})"
           " AND (f.valid_class='static' OR substr(COALESCE(f.as_of, f.created_at),1,10)>=? OR substr(f.created_at,1,10)>=?)")
    a += [cutoff, cutoff]
    if market:
        sql += " AND (f.market IS NULL OR UPPER(f.market)=?)"
        a.append(market.upper())
    rows = db.q(sql + " ORDER BY f.kind, f.as_of DESC, f.rid DESC", tuple(a))
    out = io.StringIO()
    print(f"■ 이미 아는 사실 — {stype} {sid} (최근 {since} · 기준 {datetime.now(KST):%Y-%m-%d %H:%M} KST"
          + (f" · 시장 {market.upper()}" if market else "") + f") — {len(rows)}건", file=out)
    by = {}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)
    if not rows:
        print("  (저장소에 이 주체의 종류별 사실이 없다 — 아래 옛 문장 검색을 먼저 본다)", file=out)
    for kind, rs in by.items():
        print(f"  [{kind}] {len(rs)}건", file=out)
        for r in rs[:limit]:
            n_runs = db.q1("SELECT COUNT(*) AS n FROM ev_fact_run WHERE fact_id=?", (r["id"],))["n"]
            print(f"    - {r['id']} {render_line(r['id'])} · as-of {r['as_of'] or '미확인'} · {_status_label(r)}"
                  f" · {_tlabel(r['tier'])} · 인용 run {n_runs}개", file=out)
        if len(rs) > limit:
            print(f"    … 그 밖 {len(rs) - limit}건(--limit로 더)", file=out)
    chk = db.q("SELECT topic, last_checked, run_id, n_facts FROM ev_research_check WHERE subject_type=? AND subject_id=?"
               " ORDER BY topic", (stype, sid))
    if chk:
        al = next((c for c in chk if c["topic"] == ""), None)
        tops = " · ".join(f"{c['topic']} {str(c['last_checked'])[:16]}" for c in chk if c["topic"])
        print("  마지막 조사: " + (f"{str(al['last_checked'])[:16]} ({al['run_id']} · 사실 {al['n_facts']}건)" if al else "—")
              + (f" · 종류별 {tops}" if tops else ""), file=out)
    else:
        print("  마지막 조사: 기록 없음", file=out)
    if stamp and market:
        rid = db.run_id(stamp, market)
        ms = db.q("SELECT m.*, i.sender, i.day FROM mat_mention m LEFT JOIN mat_issue i ON i.id=m.issue_id"
                  " WHERE m.subject_type=? AND m.subject_id=? AND m.run_id=? ORDER BY m.id DESC LIMIT 10", (stype, sid, rid))
        where = f"이번 run({rid})"
    else:
        ms = db.q("SELECT m.*, i.sender, i.day FROM mat_mention m LEFT JOIN mat_issue i ON i.id=m.issue_id"
                  " WHERE m.subject_type=? AND m.subject_id=? AND substr(COALESCE(m.created_at,''),1,10)>=?"
                  " ORDER BY m.id DESC LIMIT 10", (stype, sid, cutoff))
        where = f"최근 {since}"
    print(f"  재료 언급({where}): {len(ms)}건", file=out)
    for m in ms:
        print(f"    - {m['run_id']} {m.get('sender') or ''} {m.get('day') or ''} · {m.get('stance') or '—'} · "
              f"{str(m.get('headline') or '')[:90]}", file=out)
    terms = [sid]
    if stype == "ticker":
        for r in db.q("SELECT name, rowid AS rid FROM dim_company WHERE ticker=?", (sid,)):
            if r["name"]:
                terms.append(r["name"])
            terms += [x["value"] for x in db.q("SELECT value FROM dim_company_list WHERE pid=? AND src='aliases'", (r["rid"],))]
    hits, seen = [], set()
    for t in dict.fromkeys(x for x in terms if x):
        rx = re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", re.I) if re.fullmatch(r"[\x00-\x7f]+", t) else None
        # 옛 문장은 종류와 무관하게 본다 — 이전 때 종류만 붙고 주체 칸을 못 뽑은 행(1,259행)이 종류별 조회에 안 걸린다
        for r in db.q("SELECT id, as_of, legacy_text FROM ev_fact WHERE COALESCE(legacy_text,'')<>'' AND legacy_text LIKE ?"
                      " AND NOT (COALESCE(subject_type,'')=? AND COALESCE(subject_id,'')=?)"
                      " ORDER BY as_of DESC LIMIT 300", (f"%{t}%", stype, sid)):
            if r["id"] in seen or (rx and not rx.search(r["legacy_text"] or "")):
                continue
            seen.add(r["id"])
            hits.append(r)
    hits.sort(key=lambda r: str(r["as_of"] or ""), reverse=True)
    print(f"  옛 문장(이전된 sources.json) 검색 {' · '.join(repr(t) for t in dict.fromkeys(terms))}: {len(hits)}건", file=out)
    for r in hits[:10]:
        print(f"    - {r['id']} as-of {r['as_of'] or '미확인'} | {str(r['legacy_text'] or '')[:110]}", file=out)
    print("  **이미 검증된 static 사실은 다시 조사·검증하지 않는다.** 바뀐 것·빈 것만 조사해 `facts.py add`로 넣는다"
          " — 같은 열쇠면 기존 행이 재사용된다.", file=out)
    return out.getvalue()


def reuse(stamp: str, market: str, days: int = 7, brief: bool = False) -> str:
    """다른 run이 이미 세운 사실(옛 srcledger reuse) — 조사 전에 여는 목록."""
    rid, st, mk = _run((stamp, market))
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%y%m%d")
    mine = {r["fact_id"] for r in db.q("SELECT fact_id FROM ev_fact_run WHERE run_id=?", (rid,))}
    runs = {}
    for r in db.q("SELECT fact_id, run_id FROM ev_fact_run"):
        runs.setdefault(r["fact_id"], set()).add(r["run_id"])
    for r in db.q("SELECT f.id, l.value FROM ev_fact_run_label l JOIN ev_fact f ON f.rid=l.pid"):
        v = str(r["value"])
        if ":" in v:
            m_, s_ = v.split(":", 1)
            runs.setdefault(r["id"], set()).add(db.run_id(s_, m_))
    tag = f"{mk}:{st}"
    ids = [fid for fid, rs in runs.items() if fid not in mine and rid not in rs and any(x[:6] >= cutoff for x in rs)]
    rows = []
    for fid in ids:
        f = db.q1("SELECT * FROM ev_fact WHERE id=?", (fid,))
        if f:
            f["_runs"] = sorted(runs[fid])
            rows.append(f)
    rows.sort(key=lambda r: (not r.get("load_bearing", 1), str(r.get("as_of") or "")))
    out = io.StringIO()
    print(f"■ 재사용 후보 — 최근 {days}일 다른 run이 세운 사실 {len(rows)}건 (이번 run {tag} 기준)\n", file=out)
    if not rows:
        print("  없음.", file=out)
        return out.getvalue()
    for r in rows:
        flag = "" if r.get("load_bearing", 1) else " *(참고)*"
        text = render_line(r["id"])
        if brief:
            print(f"  {r['id']} {_tlabel(r.get('tier'))} as-of {r.get('as_of') or '?'} | {text[:90]}{flag} · {_status_label(r)}",
                  file=out)
            continue
        print(f"  {r['id']}  [{' · '.join(r['_runs'])}]{flag}", file=out)
        print(f"     {text[:104]}", file=out)
        print(f"     as-of {r.get('as_of') or '미확인'} · retrieved {r.get('retrieved') or '미기록'} · "
              f"{r.get('source_label') or '—'} · {_status_label(r)}", file=out)
    print("\n  **겹치는 사실은 다시 조사하지 않는다.** 같은 열쇠로 `facts.py add`하면 기존 행이 재사용되고 "
          "as-of·retrieved는 원래 시점 그대로다 — 새로 찍히는 것은 written뿐이다.", file=out)
    return out.getvalue()


# ───────────────────────────── 검증 큐(queue) ─────────────────────────────

def add_ref(fact_id: str, run, doc_type: str, doc_id: str, slot: str, role: str = None, c=None) -> None:
    """문서의 한 자리가 사실을 가리킨다(ev_claim_ref) — 노트 {{F-id}} · 시그널 · 클레임 목록 번호."""
    rid, _, _ = _run(run)
    (c or db.connect()).execute(
        "INSERT OR IGNORE INTO ev_claim_ref (fact_id, run_id, doc_type, doc_id, slot, role, at, fact_rev)"
        " VALUES (?,?,?,?,?,?,?,(SELECT rev FROM ev_fact WHERE id=?))",
        (fact_id, rid, doc_type, doc_id, str(slot), role, db.now_iso(), fact_id))


def order_basis(rid: str, c=None) -> dict:
    """주문·보유·배분 판단이 기대는 사실 {fact_id: [자리 라벨]} — 옛 fc_track1.collect의 셋(제안 근거 · 보유 판단 · 배분 사실).
    규율 매도(source=discipline)와 BUY·SELL이 아닌 제안은 뺀다."""
    c = c or db.connect()
    out, k_of = {}, {}
    # 지금 시그널 판의 제안만(v_proposal · v10 J03 — 제안은 판별로 남는다)
    for r in c.execute("SELECT p.idx, p.ticker, p.action, p.body, pf.fact_id FROM v_proposal_fact pf JOIN v_proposal p"
                       " ON p.run_id=pf.run_id AND p.idx=pf.idx WHERE pf.run_id=? ORDER BY p.idx, pf.ord_", (rid,)).fetchall():
        try:
            body = json.loads(r["body"]) if r["body"] else {}
        except ValueError:
            body = {}
        if r["action"] not in ("BUY", "SELL") or body.get("source") == "discipline":
            continue
        k_of[r["idx"]] = k_of.get(r["idx"], 0) + 1
        out.setdefault(r["fact_id"], []).append(f"{r['action']} {r['ticker']} E{k_of[r['idx']]}")
    for r in c.execute("SELECT ticker, fact_id FROM ex_hold_fact WHERE run_id=? ORDER BY rowid", (rid,)).fetchall():
        out.setdefault(r["fact_id"], []).append(f"HOLD {r['ticker']}")
    st, mk = rid.split("_", 1)
    # 배분 근거는 db.import_run_artifacts가 시그널 allocation(regime_refs·facts)에서 옮긴다 — 배분 id = AL-<stamp>-<시장>
    for r in c.execute("SELECT allocation_id, fact_id FROM jd_allocation_fact WHERE allocation_id=? ORDER BY rowid",
                       (f"AL-{st}-{mk}",)).fetchall():
        out.setdefault(r["fact_id"], []).append(f"ALLOC {r['allocation_id']}")
    return out


def _fixed(c, rid: str, batch: str) -> list:
    return [(r[0], int(r[1])) for r in c.execute(
        "SELECT fact_id, slot FROM ev_claim_ref WHERE run_id=? AND doc_type='claims' AND doc_id=? ORDER BY CAST(slot AS INTEGER)",
        (rid, batch)).fetchall()]


def _queue_row(c, rid: str, fid: str, n, batch, basis: dict) -> dict:
    f = dict(c.execute("SELECT * FROM ev_fact WHERE id=?", (fid,)).fetchone())
    uses = basis.get(fid, []) + [f"{r[0]}:{r[1]}:{r[2]}" for r in c.execute(
        "SELECT doc_type, doc_id, slot FROM ev_claim_ref WHERE fact_id=? AND run_id=? AND doc_type<>'claims'",
        (fid, rid)).fetchall()]
    # fact_rev = 이 행이 보여 주는 내용의 판 — 판정 행은 이 값을 그대로 옮긴다(W03 · 응답이 요청 판을 가리킨다)
    return {"n": n, "batch": batch, "fact_id": fid, "fact_rev": f["rev"], "source_version_id": f.get("source_version_id"),
            "kind": f["kind"], "line": render_line(fid), "as_of": f["as_of"],
            "valid_class": f["valid_class"], "verified_status": f["verified_status"], "tier": f["tier"], "url": f["url"],
            "quote": f["quote"], "order_basis": fid in basis, "uses": uses, "subject": f"{f['subject_type']}:{f['subject_id']}"}



def _part_a(doc_type: str, slot) -> bool:
    """묶음 A 자리인가 — 노트 §2까지(머리말·§0·§1-A·§1-B·§2). 노트 밖 문서와 §3 이후는 B."""
    if doc_type != "note":
        return False
    m = re.match(r"§(\d+)", str(slot or ""))
    return not m or int(m.group(1)) <= 2


def queue(stamp: str, market: str, batch: str = None, append: bool = False) -> tuple:
    """검증 큐 — 이번 run 문서가 가리킨 사실(ev_claim_ref — 노트는 render-note가 단다) 중 검증 상태 none·expired + 주문 근거 사실 전부
    (static·verified여도). 이 run에 등록만 되고 어느 문서도 안 쓴 사실은 클레임이 아니다(큐에 없다).
    A = 노트 §2까지(머리말·§0·§1·§2) · B = 그 밖(노트 §3~ · 논지·시나리오 등 노트 밖 문서) 중 A에 없는 것.
    live·interp는 뺀다(도구 출력·해석은 검증 대상이 아니다). 반환 (행 목록, 요약 문자열).
    --batch를 주면 번호를 고정한다(ev_claim_ref doc_type='claims' — 클레임 목록 번호 = 판정 기준). A·B는 번호를 잇고 B는 A에 없는 것만,
    track1은 주문 근거만 따로 번호를 매긴다. 고정된 묶음은 다시 뽑지 않는다(--append면 새로 생긴 것만 다음 번호로)."""
    rid, st, mk = _run((stamp, market))
    c = db.connect()
    basis = order_basis(rid, c)
    skip = {"verified_static": 0, "live_interp": 0, "other_batch": 0}

    def ok_class(f):
        return (f["valid_class"] or "") not in ("live", "interp")
    if batch == "track1":
        cand = []
        for fid in basis:
            f = c.execute("SELECT * FROM ev_fact WHERE id=?", (fid,)).fetchone()
            if f is None:
                continue
            if not ok_class(f):
                skip["live_interp"] += 1
                continue
            cand.append(fid)
    else:
        refs = c.execute(
            "SELECT r.fact_id, r.doc_type, r.slot FROM ev_claim_ref r LEFT JOIN ev_fact_run fr ON fr.fact_id=r.fact_id"
            " AND fr.run_id=r.run_id WHERE r.run_id=? AND r.doc_type<>'claims' ORDER BY COALESCE(fr.s_no, 1000000000), r.id",
            (rid,)).fetchall()
        ids = [r["fact_id"] for r in refs if batch != "A" or _part_a(r["doc_type"], r["slot"])]
        skip["uncited"] = c.execute("SELECT COUNT(*) FROM ev_fact_run WHERE run_id=? AND fact_id NOT IN"
                                    " (SELECT fact_id FROM ev_claim_ref WHERE run_id=? AND doc_type<>'claims')",
                                    (rid, rid)).fetchone()[0]
        cand = []
        for fid in dict.fromkeys(ids):
            f = c.execute("SELECT * FROM ev_fact WHERE id=?", (fid,)).fetchone()
            if f is None:
                continue
            if not ok_class(f):
                skip["live_interp"] += 1
                continue
            # 검증된 사실은 뺀다 — 단 검증 뒤 값이 바뀌었으면(verified_rev < value_rev · v3 · S03) 다시 넣는다(v_verify_queue와 같은 규칙)
            if f["verified_status"] not in ("none", "expired") and int(f["verified_rev"] or 0) >= int(f["value_rev"] or 1):
                skip["verified_static"] += 1          # 주문 근거여도 A·B에서는 뺀다 — 주문 근거 묶음(track1)이 다시 본다
                continue
            cand.append(fid)
    if batch is None:
        rows = [_queue_row(c, rid, fid, None, None, basis) for fid in cand]
        return rows, (f"큐(미고정) {len(rows)}건 — 제외: 검증된 사실 {skip['verified_static']} · live/interp {skip['live_interp']}"
                      f" · 문서가 안 쓴 사실 {skip.get('uncited', 0)}")
    if batch not in BATCHES:
        raise SystemExit(f"--batch는 {'|'.join(BATCHES)}")
    with db.tx(c):
        fixed = _fixed(c, rid, batch)
        new = []
        if not fixed or append:
            have = {f for f, _ in fixed}
            if batch in ("A", "B"):
                others = {r[0] for r in c.execute("SELECT fact_id FROM ev_claim_ref WHERE run_id=? AND doc_type='claims'"
                                                  " AND doc_id IN ('A','B') AND doc_id<>?", (rid, batch)).fetchall()}
                start = c.execute("SELECT COALESCE(MAX(CAST(slot AS INTEGER)),0) FROM ev_claim_ref WHERE run_id=?"
                                  " AND doc_type='claims' AND doc_id IN ('A','B')", (rid,)).fetchone()[0]
            else:
                others = set()
                start = max((n for _, n in fixed), default=0)
            for fid in cand:
                if fid in have:
                    continue
                if fid in others:
                    skip["other_batch"] += 1
                    continue
                start += 1
                add_ref(fid, rid, "claims", batch, str(start), role="order_basis" if batch == "track1" else "verify", c=c)
                new.append(fid)
        fixed = _fixed(c, rid, batch)
    rows = [_queue_row(c, rid, fid, n, batch, basis) for fid, n in fixed]
    summ = (f"큐 {batch}: {len(rows)}건(이번에 고정 {len(new)}) — 제외: 검증된 사실 {skip['verified_static']} · "
            f"live/interp {skip['live_interp']} · 다른 묶음 {skip['other_batch']}"
            + (f" · 문서가 안 쓴 사실 {skip['uncited']}" if "uncited" in skip else ""))
    return rows, summ


# ───────────────────────────── 판정(verdict) ─────────────────────────────

def _verdict_status(rows: list):
    """한 사실의 이번 판정 행들 → 새 검증 상태(None이면 그대로). 행 순서와 무관하다(가장 나쁜 판정이 이긴다)."""
    vs = {r["verdict"] for r in rows}
    if "MISMATCH" in vs:
        return "disputed"
    if "STALE" in vs:
        return "expired"
    if vs == {"MATCH"} and any(r.get("tier") in (1, 2) for r in rows):
        return "verified"
    return None


def add_verdicts(rows: list, run=None, label: str = None, round_: int = None, src_file: str = None,
                 out=None, err=None) -> int:
    """판정 행(추가만) + 사실 검증 상태. MATCH(T1/T2) → verified · MISMATCH → disputed(트리거가 needs_review) ·
    STALE → expired · UNVERIFIABLE·T3 MATCH → 그대로. 같은 사실을 다시 판정하면 round가 올라가고 렌더·결정은 마지막 round만 본다."""
    out, err = out or sys.stdout, err or sys.stderr
    vc = _vcols()
    rc = 0
    prepared = []
    for i, r in enumerate(rows, 1):
        try:
            if r.get("run_id"):
                rid = r["run_id"]
            elif r.get("stamp") and r.get("market"):
                rid = db.run_id(r["stamp"], r["market"])
            elif run:
                rid = _run(run)[0]
            else:
                raise Reject("run(stamp·market)이 없다")
            batch = r.get("batch") or label
            if batch not in BATCHES:
                raise Reject(f"batch는 {'|'.join(BATCHES)}: {batch!r}")
            fid = r.get("fact_id")
            req_rev = None                            # 검증 요청(클레임 번호)이 고정될 때의 사실 판(W03)
            if r.get("claim") is not None:
                x = db.q1("SELECT fact_id, fact_rev FROM ev_claim_ref WHERE run_id=? AND doc_type='claims' AND doc_id=? AND slot=?",
                          (rid, batch, str(r["claim"])))
                if not x:
                    raise Reject(f"클레임 {r['claim']}이 {rid} 묶음 {batch} 목록에 없다")
                if fid and fid != x["fact_id"]:
                    raise Reject(f"클레임 {r['claim']}은 {x['fact_id']}의 요청인데 행은 {fid}를 가리킨다 — 요청과 응답이 어긋났다")
                fid, req_rev = x["fact_id"], x["fact_rev"]
            if not fid or not db.q1("SELECT 1 AS o FROM ev_fact WHERE id=?", (fid,)):
                raise Reject(f"없는 사실 {fid!r}")
            if req_rev is None:
                # 사실 번호로 온 행 — 이번 묶음의 클레임 목록에 그 사실이 있으면 그 요청이 고정될 때의 판(지금 판이 아니다)
                x = db.q1("SELECT fact_rev FROM ev_claim_ref WHERE run_id=? AND doc_type='claims' AND doc_id=? AND fact_id=?"
                          " ORDER BY id LIMIT 1", (rid, batch, fid))
                req_rev = x["fact_rev"] if x else None
            v = str(r.get("verdict") or "").strip().upper()
            v = next((x for x in VERDICTS if v.startswith(x)), None)
            if not v:
                raise Reject(f"판정은 4값(MATCH/MISMATCH/STALE/UNVERIFIABLE): {r.get('verdict')!r}")
            # ★ 판정 판(2026-10-19 · Codex R04 · W03) — 이 판정이 본 사실의 판. 행의 fact_rev(큐 행이 준 판을 그대로 옮긴다) →
            #   없으면 그 클레임 번호가 고정될 때의 판(요청 판) → 둘 다 없으면 **거부**한다. 넣는 순간의 지금 판으로 추정하지 않는다
            #   (검증 도중 값이 고쳐지면 옛 값을 본 판정이 새 값을 승인했다 — W03). 지금 판보다 높을 수 없다.
            #   값 판(value_rev)보다 낮으면 행은 남지만 세지 않는다(REVERIFY).
            cur = db.q1("SELECT rev, value_rev FROM ev_fact WHERE id=?", (fid,))
            fr = r.get("fact_rev")
            try:
                fr = int(fr) if fr not in (None, "") else None
            except (TypeError, ValueError):
                raise Reject(f"fact_rev는 정수: {r.get('fact_rev')!r}")
            if fr is None and req_rev not in (None, ""):
                # 요청 판으로 받는 것은 요청 뒤 값이 안 바뀌었을 때만 — 바뀌었으면 이 판정이 어느 값을 봤는지 모른다(옛 값일 수 있다)
                if int(req_rev) < int(cur["value_rev"] or 1):
                    raise Reject(f"{fid}: 검증 요청(클레임 고정) 판 rev {req_rev} 뒤에 값이 rev {cur['value_rev']}에서 바뀌었다 — "
                                 f"이 판정이 본 값을 알 수 없다. 지금 값을 다시 보고 fact_rev={cur['rev']}를 적어 넣는다")
                fr = int(req_rev)
            if fr is None:
                raise Reject(f"{fid}: 판정이 본 사실 판을 알 수 없다 — 큐 행의 fact_rev를 그대로 적거나 클레임 번호(claim)로 넣는다"
                             "(지금 판으로 추정하지 않는다)")
            if fr < 1 or fr > int(cur["rev"] or 1):
                raise Reject(f"fact_rev {fr}는 {fid}의 판 범위(1~{cur['rev']}) 밖이다")
            prepared.append({**r, "_rid": rid, "_batch": batch, "_fid": fid, "_v": v, "_tier": _tier(r.get("tier")), "_fr": fr})
        except Reject as e:
            print(f"  ✗ [{i}] 판정 거부 — {e}", file=err)
            rc = 2
    groups = {}
    for p in prepared:
        groups.setdefault((p["_rid"], p["_batch"], p["_fid"]), []).append(p)
    with db.tx() as c:
        for (rid, batch, fid), ps in groups.items():
            rnd = round_ if round_ is not None else None
            if rnd is None and any(x.get("round") is None for x in ps):   # 한 행이라도 round가 비면 한 번만 정한다
                rnd = (c.execute("SELECT COALESCE(MAX(round),0) FROM ev_verdict WHERE run_id=? AND batch=? AND fact_id=?",
                                 (rid, batch, fid)).fetchone()[0] or 0) + 1
            done = []
            for p in ps:
                note = str(p.get("note") or "").strip()
                t1 = str(p.get("t1") or "").strip()
                if t1 and "t1" not in vc:
                    note = f"T1 시도: {t1} ¦ {note}" if note else f"T1 시도: {t1}"
                sup = str(p.get("support") or "").strip()
                sup = re.sub(r"^받치는\s*것\s*[:：]\s*", "", sup)
                row = {"fact_id": fid, "run_id": rid, "batch": batch, "round": p.get("round") if p.get("round") is not None else rnd,
                       "verdict": p["_v"], "tier": p["_tier"], "lens": p.get("lens"), "note": note or None,
                       "support": sup or None, "auditor": p.get("auditor"), "at": p.get("at") or db.now_iso(),
                       "src_file": p.get("src_file") or src_file, "row_label": p.get("sub") or p.get("row_label"),
                       "fact_rev": p["_fr"]}
                for extra in ("quote", "url", "t1"):
                    if extra in vc and p.get(extra):
                        row[extra] = str(p[extra]).strip()
                # 이 판정이 대조한 본문 판(v3) — 행의 URL이 사실과 다르면 그 URL의 최신 판, 아니면 사실이 본 판
                fr_ = c.execute("SELECT url, source_version_id FROM ev_fact WHERE id=?", (fid,)).fetchone()
                svid = fr_["source_version_id"] if fr_ else None
                if p.get("url") and fr_ and _norm_url(p["url"]) != _norm_url(fr_["url"] or ""):
                    vv = c.execute("SELECT v.id FROM ev_source_version v JOIN ev_source s ON s.id=v.source_id WHERE s.url=?"
                                   " ORDER BY v.last_seen DESC, v.id DESC LIMIT 1", (_norm_url(p["url"]),)).fetchone()
                    svid = vv[0] if vv else None
                if "source_version_id" in vc:
                    row["source_version_id"] = svid
                if any(p.get(x) for x in ("quote", "url")) and not {"quote", "url"} <= vc:
                    print(f"  ※ {fid}: 판정 행의 원문 인용·URL 칸이 아직 없다(db 요청 대기) — 사실의 값을 쓴다. "
                          f"다르면 `facts.py correct {fid} --set quote=…`로 사실을 고쳐라", file=err)
                vid = c.execute(f"INSERT INTO ev_verdict ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                                tuple(row.values())).lastrowid
                done.append({**row, "id": vid})
            vrev = int(c.execute("SELECT value_rev FROM ev_fact WHERE id=?", (fid,)).fetchone()[0] or 1)
            counting = [r for r in done if int(r["fact_rev"]) >= vrev]
            if done and not counting:
                print(f"  ※ {fid}: 판정이 옛 판(rev {max(r['fact_rev'] for r in done)}) 기준인데 값은 rev {vrev}에서 바뀌었다 — "
                      f"행은 남기고 세지 않는다(재판정: 지금 값을 다시 보고 fact_rev={vrev} 이상 지금 판으로 넣는다 · `facts.py queue`가 지금 판을 준다)",
                      file=err)
            new = _verdict_status(counting)
            if new == "verified":
                tier = min(r["tier"] for r in counting if r["tier"] in (1, 2))
                c.execute("UPDATE ev_fact SET verified_status='verified', verified_at=?, verified_tier=?, verified_run=?, updated_at=?,"
                          " verified_rev=? WHERE id=?",
                          (db.now_iso(), tier, rid, db.now_iso(), max(int(r["fact_rev"]) for r in counting), fid))
            elif new:
                c.execute("UPDATE ev_fact SET verified_status=?, updated_at=? WHERE id=?", (new, db.now_iso(), fid))
            if new:
                db.mark_export("sources")
            cnt = {v: sum(1 for r in done if r["verdict"] == v) for v in VERDICTS}
            st_now = c.execute("SELECT verified_status FROM ev_fact WHERE id=?", (fid,)).fetchone()[0]
            print(f"{fid} → {st_now} (판정 {len(done)}행 · round {done[0]['round']} · "
                  + " · ".join(f"{v} {n}" for v, n in cnt.items() if n) + f" · #{','.join(str(r['id']) for r in done)})", file=out)
    return rc


# ───────────────────────────── 판정 행 읽기 · 교정 연결 ─────────────────────────────

def _split_t1(note: str) -> tuple:
    """옛 저장(칸 없음): 'T1 시도: … ¦ 비고' → (t1, 비고)."""
    note = note or ""
    m = re.match(r"^T1 시도:\s*(.*?)(?:\s*¦\s*(.*))?$", note, re.S)
    return (m.group(1).strip(), (m.group(2) or "").strip()) if m else ("", note)


def _vrows(c, rid: str, batch: str) -> list:
    """(run, 묶음)의 판정 행 — 사실마다 마지막 round만(재채점이 앞 round를 대신한다)."""
    rows = [dict(r) for r in c.execute(
        "SELECT v.*, f.url AS f_url, f.quote AS f_quote, f.kind AS f_kind, f.valid_class AS f_valid, f.value_rev AS f_value_rev"
        " FROM ev_verdict v"
        " JOIN ev_fact f ON f.id=v.fact_id WHERE v.run_id=? AND v.batch=? AND COALESCE(v.round,0)="
        "(SELECT MAX(COALESCE(v2.round,0)) FROM ev_verdict v2 WHERE v2.run_id=v.run_id AND v2.batch=v.batch AND v2.fact_id=v.fact_id)"
        " ORDER BY v.id", (rid, batch)).fetchall()]
    for r in rows:
        t1, note = (r.get("t1") or "", r.get("note") or "") if r.get("t1") else _split_t1(r.get("note"))
        r["_t1"], r["_note"] = t1, note
        r["_url"] = r.get("url") or r.get("f_url") or ""
        r["_quote"] = r.get("quote") or r.get("f_quote") or ""
        r["_stale"] = int(r.get("fact_rev") or 0) < int(r.get("f_value_rev") or 1)   # 값이 바뀌기 전 판을 본 판정 — 세지 않는다
    return rows


def _corr_links(c, rows: list) -> tuple:
    """판정 행 → 교정. 교정의 verdict_id가 그 행이면 직접, 아니면 같은 사실에 판정 뒤 적용된 교정(MISMATCH·STALE 행만).
    반환 ({verdict_id: [교정 id]}, {교정 id: 교정 행+심각도})."""
    ids = {r["id"] for r in rows}
    facts = {r["fact_id"] for r in rows}
    corr = {}
    for fid in facts:
        for x in c.execute("SELECT * FROM ev_correction WHERE fact_id=? ORDER BY id", (fid,)).fetchall():
            corr[x["id"]] = dict(x)
    links = {r["id"]: [] for r in rows}
    for cid, x in corr.items():
        if x["verdict_id"] in ids:
            links[x["verdict_id"]].append(cid)
    for r in rows:
        if r["verdict"] in ("MISMATCH", "STALE") and not links[r["id"]]:
            links[r["id"]] = [cid for cid, x in corr.items() if x["fact_id"] == r["fact_id"] and x["verdict_id"] not in ids
                              and str(x["applied_at"] or "") >= str(r["at"] or "")]
    sev_rank = {"MISMATCH": 0, "STALE": 1, "UNVERIFIABLE": 2, "MATCH": 3}
    used = {}
    for r in rows:
        for cid in links[r["id"]]:
            used.setdefault(cid, []).append(r)
    out = {}
    for cid, rs in used.items():
        x = corr[cid]
        own = next((r for r in rows if r["id"] == x["verdict_id"]), None)
        if own is None and x["verdict_id"]:
            vv = c.execute("SELECT verdict, note FROM ev_verdict WHERE id=?", (x["verdict_id"],)).fetchone()
            own = dict(vv) if vv else None
        base = own or min(rs, key=lambda r: sev_rank.get(r["verdict"], 9))
        v = base["verdict"]
        sev = ("🔴" if "비가역" in str(base.get("note") or "") else "🟡") if v == "MISMATCH" else \
            "⏳" if v == "STALE" else "🔵" if v == "UNVERIFIABLE" else "⚪"
        out[cid] = {**x, "sev": sev}
    return links, out


# ───────────────────────────── 판정 기계 검사(lint) ─────────────────────────────
# lint_verdict.py의 검사를 표 기준으로 옮겼다 — 집계·원 문구·범위((a)(b)(c))는 표가 원본이라 어긋날 수 없고,
# 남는 것은 행의 기록 품질이다: (d)(e)(f)(i)(k)(l)(o)(p)(s)(z)(ⓙ)(x) + 클레임마다 판정 행이 있는가.
SAME_RE = re.compile(r"같은\s*뜻\s*[:：]")
DIFF_RE = re.compile(r"원문은|원문엔|원문에는|원 문구는|원문과 달리|노트는[^.;|]{0,40}(?:생략|뺐|빠뜨|빠졌|좁|넓)"
                     r"|(?:한정어|단서|조건|범위|기준 축|주어)\s*(?:차이|탈락|생략|누락)")
COND_RE = re.compile(r"(?:한정어|조건|단서|예외|전제|갈래)[^.;|]{0,60}?(?:탈락|빠짐|빠져|빠졌|누락|생략|뺌|뺐)")
KEEP_RE = re.compile(r"(단서|삭제)\s*[:：]\s*['‘\"“]([^'’\"”]{2,80})['’\"”]")
ABSENT_RE = re.compile(
    r"(?:리포트|보고서|공시|발표|공지|기사|의견|자료|안내|일정|언급|보도|가이던스|컨센서스|데이터|업데이트|정정)\s*(?:은|는|이|가|도)?\s*"
    r"(?:없[다음었고어]|미발견|찾지\s*못|못\s*찾|확인되지\s*않)"
    r"|(?:발행|공표|공시|발표|제공|게시)(?:하지|되지)\s*않"
    r"|\bno\s+(?:newer\s+|new\s+|such\s+|later\s+)?(?:report|filing|announcement|mention|update|guidance|article|data|disclosure|statement)s?\b"
    r"|\b(?:not|never)\s+(?:found|announced|published|disclosed|reported)\b|\bnone\s+(?:found|published)\b", re.I)
SEARCH_RE = re.compile(r"(?:검색\s*[:：]|검색어|WebSearch|web\s*search)[^|]{3,}?\d{1,2}:\d{2}", re.I)
SUPPORT_RE = re.compile(r"^\s*(?:받치는\s*것\s*[:：]\s*)?\**\s*(?:논지|크기|배분|보유|매도)[^|]{10,}")
IGNORE_RE = re.compile(r"무시\s*[:：]")
IGNORE_PTR = re.compile(r"https?://|\bL\d+\b|클레임\s*\d+")
FRONT_RE = re.compile(r"첫\s*화면|메인\s*페이지|홈\s*화면|homepage|front page", re.I)
UNOPENED_RE = re.compile(r"(원문|본문|기사|페이지|문서|보고서|자료)\s*(을|를|은|는)?\s*(미열람|열지 못|못 열|안 열|미개방)")


def _raw_for_row(r: dict) -> tuple:
    """판정 행이 대조할 본문 — (본문 | None, 판 id | None, 비고). ① 판정이 대조한 판 ② 사실이 본 판 ③ 그 URL의 최신 판(옛 사실 ·
    비고에 적는다) ④ 옛 보관(`_raw_text` — v3 전 접속 기록). 예전에는 URL로만 찾아 다음 조회가 덮은 본문과 대조했다(U01)."""
    vid = r.get("source_version_id")
    note = ""
    if not vid:
        f = db.q1("SELECT source_version_id, source_version_link FROM ev_fact WHERE id=?", (r.get("fact_id"),)) or {}
        vid = f.get("source_version_id")
        if vid and f.get("source_version_link") == "inferred":
            note = "추정 판"
    if not vid and r.get("_url"):
        v = db.q1("SELECT v.id FROM ev_source_version v JOIN ev_source s ON s.id=v.source_id WHERE s.url=?"
                  " ORDER BY v.last_seen DESC, v.id DESC LIMIT 1", (_norm_url(r["_url"]),))
        vid, note = (v or {}).get("id"), "URL 최신 판(사실에 판 없음)"
    if vid:
        t = db.raw_text(vid)
        prov = (db.version_get(vid) or {}).get("provenance")
        if prov == "migrated":
            note = (note + " · " if note else "") + "이전 전 보관본 — 당시 본문이 보존되지 않았을 수 있다"
        return t, vid, note
    return _raw_text(r.get("_url") or ""), None, "옛 보관"


def _raw_text(url: str):
    """그 URL의 옛 보관 원문(v3 전 · ev_source.raw_path → 옛 접속 기록 op_fetch 순) — 판이 없는 옛 사실만 쓴다."""
    if not url:
        return None
    paths = []
    r = db.q1("SELECT raw_path FROM ev_source WHERE url=?", (_norm_url(url),))
    if r and r.get("raw_path"):
        paths.append(r["raw_path"])
    try:
        for x in db.q("SELECT body FROM op_fetch WHERE json_extract(body,'$.url') IN (?,?) ORDER BY seq DESC LIMIT 3",
                      (url, url.rstrip("/"))):
            rp = (json.loads(x["body"]) or {}).get("raw_path")
            if rp:
                paths.append(rp)
    except Exception:  # noqa: BLE001
        pass
    for p in paths:
        pp = db.raw_abs(p)
        if pp is not None and pp.is_file():
            return pp.read_text(encoding="utf-8", errors="ignore")
    return None


def _nospace(s: str) -> str:
    s = (s or "").replace("**", "").replace("`", "")
    for a, b in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), ("\u00a0", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s)


def lint(stamp: str, market: str, batch: str) -> tuple:
    """반환 (위반, 경고, 판정 집계 Counter-like dict, 행 수)."""
    rid, st, mk = _run((stamp, market))
    c = db.connect()
    rows = _vrows(c, rid, batch)
    fixed = _fixed(c, rid, batch)
    n_of = {f: n for f, n in fixed}
    errs, warns = [], []
    cnt = {v: 0 for v in VERDICTS}
    if not fixed:
        errs.append(f"묶음 {batch}의 고정된 클레임이 없다 — `facts.py queue --batch {batch}`가 먼저다")
    have = {r["fact_id"] for r in rows}
    for fid, n in fixed:
        if fid not in have:
            errs.append(f"클레임 {n}({fid})에 판정 행이 없다 — 판정을 넣어라(`facts.py verdict`)")
    links, corr = _corr_links(c, rows)
    by_url = {}
    for r in rows:
        tag = f"클레임 {n_of.get(r['fact_id'], '?')}({r['fact_id']} · 판정 #{r['id']})"
        v = r["verdict"]
        if v not in VERDICTS:
            errs.append(f"{tag} 판정이 4값이 아니다: {v!r}")
            continue
        cnt[v] += 1
        if r["fact_id"] not in n_of:
            errs.append(f"{tag} 이 묶음의 클레임 목록에 없는 사실을 판정했다")
        note, t1, tier = r["_note"], r["_t1"], r["tier"]
        if r.get("_stale") and v == "MATCH":
            warns.append(f"{tag} MATCH는 rev {r.get('fact_rev')} 기준인데 값이 rev {r.get('f_value_rev')}에서 바뀌었다 — 세지 않는다"
                         " (REVERIFY · 지금 값을 다시 보고 `facts.py verdict`로 round+1)")
        blob = " ".join(str(x or "") for x in (note, t1, r.get("support"), r.get("row_label"), r["_quote"], r["_url"]))
        # (d) 기록
        if v == "UNVERIFIABLE" and not re.search(r"\d{1,2}:\d{2}", note):
            errs.append(f"{tag} UNVERIFIABLE인데 검색 시각이 없다(비고에 검색어·시각)")
        if not t1 or t1 in ("-", "—"):
            errs.append(f"{tag} 'T1 시도'가 비었다(URL·상태, 'T1 자체', 또는 '미시도 — 사유')")
        # (e) 교정 연결
        if v in ("MISMATCH", "STALE"):
            if not links.get(r["id"]):
                errs.append(f"{tag} {v}인데 교정이 없다 — `facts.py correct {r['fact_id']} --set … --reason … --verdict {r['id']}`")
            for cid in links.get(r["id"], []):
                ok = "🟡🔴" if v == "MISMATCH" else "⏳"
                if corr[cid]["sev"] not in ok:
                    errs.append(f"{tag} 교정 #{cid}의 심각도 {corr[cid]['sev']}가 {v} 행과 맞지 않는다 — 교정마다 --verdict로 그 판정을 잇는다")
        # (i) URL
        url = r["_url"]
        if tier != "data":
            if not re.match(r"^https?://", url):
                errs.append(f"{tag} URL이 없다(http 링크)")
            elif v == "MATCH" and re.match(r"^https?://[^/?#\s]+/?$", url) and not FRONT_RE.search(note + " " + t1):
                errs.append(f"{tag} MATCH인데 출처가 도메인 루트 주소뿐이다({url[:60]}) — 그 사실이 보이는 하위 페이지 주소로")
        if tier in (1, 2, 3) and url:
            by_url.setdefault(_norm_url(url), []).append((tag, tier, "티어 차이" in note))
        # (k) 열지 않은 출처
        if v == "MATCH" and UNOPENED_RE.search(note):
            errs.append(f"{tag} MATCH인데 비고에 원문을 열지 못했다고 적혀 있다 — 열지 않은 출처로 MATCH를 주지 않는다(UNVERIFIABLE)")
        # (l) 단서 탈락 MISMATCH — 교정 새 값이 그 낱말을 담는가(단서) · 빼는가(삭제)
        if v == "MISMATCH" and COND_RE.search(note):
            keeps = KEEP_RE.findall(note)
            if not keeps:
                errs.append(f"{tag} 단서 탈락 MISMATCH인데 `단서: '<교정문에 들어갈 낱말>'`(또는 `삭제: '…'`) 표기가 없다")
            news = [str(corr[cid].get("new") or "") for cid in links.get(r["id"], [])]
            for kind_, w in keeps:
                if not news:
                    break
                hit = [x for x in news if _nospace(w) in _nospace(x)]
                if kind_ == "단서" and not hit:
                    errs.append(f"{tag} 단서 '{w[:40]}'가 연결된 교정의 새 값에 없다 — 교정이 단서를 다시 떨어뜨렸다")
                if kind_ == "삭제" and len(hit) == len(news):
                    errs.append(f"{tag} 삭제할 '{w[:40]}'가 교정의 새 값에 남아 있다")
        # (o)(p) MATCH 행
        if v == "MATCH":
            if re.search(r"재확인\s*목록", blob):
                errs.append(f"{tag} MATCH 행이 '재확인 목록'으로 보낸다 — 이용 전에 다시 볼 것이 있으면 STALE(⏳ 교정)로")
            d = DIFF_RE.search(note)
            if d and not SAME_RE.search(note):
                errs.append(f"{tag} 원문과의 차이({d.group(0)[:20]!r})를 적은 MATCH인데 `같은 뜻: <이유>`가 없다")
        # (s) 부재 단정
        a = ABSENT_RE.search(note + " " + t1)
        if a and not SEARCH_RE.search(blob):
            errs.append(f"{tag} 부재 단정({a.group(0)[:24]!r})인데 `검색: <검색어(변형 포함)> · <어디> · <HH:MM>`이 없다")
        # (z) 주문 근거 — 받치는 것
        if batch == "track1" and not SUPPORT_RE.search(str(r.get("support") or "")):
            errs.append(f"{tag} 주문 근거 행인데 `받치는 것: <논지|크기|배분|보유|매도> — <떠받치는 시그널 문장 요지>`(10자 이상)가 없다")
        # (ⓙ) 무시
        for ln in blob.split("\n"):
            if IGNORE_RE.search(ln) and not IGNORE_PTR.search(ln):
                errs.append(f"{tag} `무시:`인데 반증(http 링크)도 행 지목(L<n> · 클레임 N)도 없다")
        # (x) 원문 대조 — 보관 원문이 있으면 위반 · 없으면 경고
        if v == "MATCH" and r["_quote"]:
            segs = [x for x in (_nospace(sg).strip("\"'") for sg in re.split(r"…|\.\.\.", r["_quote"])) if len(x) >= 8]
            raw, rvid, rnote = _raw_for_row({**r, "_url": url}) if segs else (None, None, "")
            vtag = f"v{rvid}" if rvid else "옛 보관"
            if segs and raw is None:
                warns.append(f"{tag} 보관 원문이 없어 인용을 대조하지 못했다 — 원문은 `fetch.py`로 열어야 보관된다")
            elif segs:
                rawn = _nospace(raw)
                miss = [sg for sg in segs if sg not in rawn]
                if miss:
                    other = ""
                    sid = (db.q1("SELECT source_id FROM ev_fact WHERE id=?", (r["fact_id"],)) or {}).get("source_id")
                    for v in (db.versions_for_source(sid) if sid else []):
                        if v["id"] != rvid and (db.raw_text(v["id"]) or "") and all(sg in _nospace(db.raw_text(v["id"])) for sg in segs):
                            other = f" · 같은 출처의 다른 판 v{v['id']}에는 있다(`facts.py correct {r['fact_id']} --set source_version={v['id']}`)"
                            break
                    errs.append(f"{tag} MATCH인데 원문 인용 조각이 보관 원문({vtag}{' · ' + rnote if rnote else ''})에 없다"
                                f"({miss[0][:40]!r}) — 원문을 다시 열어 그대로 옮겨라{other}")
    # (f) 티어 일관성 — 티어는 출처의 성질이다
    for u, lst in by_url.items():
        if len({t for _, t, _ in lst}) > 1 and not any(x for _, _, x in lst):
            errs.append("같은 URL이 행마다 다른 티어(" + " / ".join(f"T{t} {tag}" for tag, t, _ in lst[:4])
                        + f") — 맞추거나 다르게 둔 행의 비고에 '티어 차이: <이유>': {u[:80]}")
    return errs, warns, cnt, sum(cnt.values())


def lint_line(cnt: dict, total: int, ok: bool) -> str:
    return ("LINT %s (facts.py lint %s · 행 %d · MATCH %d · MISMATCH %d · STALE %d · UNVERIFIABLE %d)"
            % ("OK" if ok else "FAIL", _now_min(), total, cnt["MATCH"], cnt["MISMATCH"], cnt["STALE"], cnt["UNVERIFIABLE"]))


# ───────────────────────────── 참조 검사(check-refs) ─────────────────────────────

def check_refs(stamp: str, market: str) -> tuple:
    """이번 run의 참조(ev_claim_ref · ex_proposal_fact · ex_hold_fact)가 전부 있는 사실을 가리키고, 그 사실에 url·quote·as_of가 있는가.
    도구 사실(live·data)·해석(interp)은 url·quote를 묻지 않는다 · --minor 사실은 quote를 묻지 않는다(주문 근거는 묻는다).
    반환 (ok, 줄, 문제 목록)."""
    rid, _, _ = _run((stamp, market))
    c = db.connect()
    refs = [(f"{r['doc_type']}:{r['doc_id']}:{r['slot']}", r["fact_id"], False) for r in c.execute(
        "SELECT doc_type, doc_id, slot, fact_id FROM ev_claim_ref WHERE run_id=? ORDER BY id", (rid,)).fetchall()]
    refs += [(f"proposal:{r['idx']}", r["fact_id"], True) for r in c.execute(
        "SELECT idx, fact_id FROM v_proposal_fact WHERE run_id=? ORDER BY idx, ord_", (rid,)).fetchall()]
    refs += [(f"hold:{r['ticker']}", r["fact_id"], True) for r in c.execute(
        "SELECT ticker, fact_id FROM ex_hold_fact WHERE run_id=? ORDER BY rowid", (rid,)).fetchall()]
    probs, seen = [], {}
    facts = set()
    for where, fid, basis in refs:
        f = c.execute("SELECT * FROM ev_fact WHERE id=?", (fid,)).fetchone()
        if f is None:
            probs.append(f"{where} → {fid}: 그런 사실이 없다")
            continue
        facts.add(fid)
        miss = []
        if _empty(f["as_of"]):
            miss.append("as_of")
        tool = f["valid_class"] == "live" or f["tier"] == "data" or f["origin"] == "tool"
        if not tool and f["valid_class"] != "interp":
            if not re.match(r"^https?://", f["url"] or ""):
                miss.append("url")
            q = f["quote"] or ""
            if (f["load_bearing"] or basis) and not q:
                miss.append("quote")
            elif q and f["tier"] in (1, 2) and f["load_bearing"] and len(q) < QUOTE_MIN:
                miss.append(f"quote {QUOTE_MIN}자 이상")
        if miss:
            k = (fid, tuple(miss))
            if k not in seen:
                seen[k] = where
                probs.append(f"{where} → {fid}: {' · '.join(miss)} 없음")
    ok = not probs
    line = (f"REFS OK (facts.py check-refs {_now_min()} · 참조 {len(refs)} · 사실 {len(facts)})" if ok else
            f"REFS FAIL — 문제 {len(probs)}건 (facts.py check-refs {_now_min()} · 참조 {len(refs)} · 사실 {len(facts)})")
    return ok, line, probs


# ───────────────────────────── 교정(correct) ─────────────────────────────

def correct(fact_id: str, sets: dict, reason: str, verdict_id=None, run=None, out=None) -> int:
    """사실을 고친다 — `_bump` 한 번(교정 행 · 옛 판 전부를 판 기록에 · 상세·머리 · rev+1 · 값이 바뀌면 value_rev도) ·
    그 사실을 쓴 열린 run에 rerender=1. 전파 없음. 값이 바뀌면 검증 상태를 none으로 되돌리고 그 앞 판정은 더 세지 않는다
    (REVERIFY — 다음 큐가 한 번 다시 본다)."""
    out = out or sys.stdout
    f = db.q1("SELECT * FROM ev_fact WHERE id=?", (fact_id,))
    if not f:
        raise Reject(f"없는 사실 {fact_id}")
    if not str(reason or "").strip():
        raise Reject("--reason이 필요하다(교정 사유는 기록으로 남는다)")
    k = KINDS.get(f["kind"])
    dtypes = {c: t for c, t in _colinfo(k.table).items() if c != "fact_id"} if k else {}
    alias = k.alias if k else {}
    hd, dd, old, new, asked = {}, {}, {}, {}, {}
    cur_d = db.q1(f"SELECT * FROM {k.table} WHERE fact_id=?", (fact_id,)) if k else {}
    cur_d = cur_d or {}
    for col, raw in sets.items():
        col = alias.get(col, "source_label" if col == "source" else col)
        if col == "source_version":
            # 이 사실이 본 본문 판을 바로잡는다(v3) — 같은 출처의 판만 · 표기 편집(rev만 오름 · 판정은 그대로 센다)
            vid, _l, _w = _resolve_version(db.connect(), f.get("source_id"), None, raw)
            if f.get("source_version_id") != vid:
                hd["source_version_id"], hd["source_version_link"] = vid, "recorded"
                old["source_version"], new["source_version"] = f.get("source_version_id"), vid
            asked[col] = vid
            continue
        if col in ("id", "fact_id", "rev", "created_at", "updated_at", "kind", "verified_status") and col not in dtypes:
            raise Reject(f"{col}은 correct로 고치지 않는다(검증 상태는 verdict가 바꾼다)")
        if col not in dtypes and col not in HEADER_EDIT:
            raise Reject(f"{f['kind']}에 없는 칸 {col} — 고칠 수 있는 칸: {', '.join(sorted(set(dtypes) | HEADER_EDIT))}")
        val = None if raw is None or str(raw).strip().lower() in ("", "null", "none") else raw
        if col == "tier":
            val = _tier(val)
        elif col == "load_bearing":
            val = None if val is None else (1 if str(val).strip().lower() in ("1", "true", "yes", "예") else 0)
        elif col == "valid_class" and val not in VALID:
            raise Reject(f"valid_class는 {'|'.join(VALID)}")
        elif col in dtypes and dtypes[col] == "" and isinstance(val, str):
            val = _num(val)
        elif isinstance(val, str):
            val = val.strip()
        asked[col] = val
        if col in dtypes:
            if cur_d.get(col) != val:
                dd[col] = val
                old[col], new[col] = cur_d.get(col), val
        if col in HEADER_EDIT or col in ("market", "period"):
            if col in f and f.get(col) != val:
                hd[col] = val
                old.setdefault(col, f.get(col))
                new[col] = val
    if not dd and not hd:
        # 다른 묶음이 같은 교정을 먼저 넣었다 — 값은 이미 이 판정이 말하는 값이다. 판정에 교정을 연결만 한다(값 변경 없음 · 기록은 남긴다).
        # 연결이 없으면 이 판정 행은 'MISMATCH인데 교정 없음'으로 lint가 영영 못 통과한다(2026-10-17 예행 D12).
        if verdict_id is not None and db.q1("SELECT 1 AS o FROM ev_verdict WHERE id=? AND fact_id=? AND verdict IN ('MISMATCH','STALE')",
                                            (int(verdict_id), fact_id)):
            prior = db.q1("SELECT id FROM ev_correction WHERE fact_id=? ORDER BY id DESC LIMIT 1", (fact_id,))
            ref = f"교정 #{prior['id']}" if prior else ""
            rid = _run(run)[0] if run else None
            snap = json.dumps(asked, ensure_ascii=False, default=str)
            with db.tx() as c:
                cid = c.execute("INSERT INTO ev_correction (fact_id, run_id, old, new, reason, verdict_id, applied_at)"
                                " VALUES (?,?,?,?,?,?,?)",
                                (fact_id, rid, snap, snap, f"이미 반영됨{' — ' + ref if ref else ''} · " + reason.strip(),
                                 int(verdict_id), db.now_iso())).lastrowid
            print(f"교정 #{cid} {fact_id} · 판정 #{verdict_id} — 값이 이미 교정 뒤 값이다{'(' + ref + ')' if ref else ''}"
                  " · 판정에 연결만 했다", file=out)
            return 0
        print(f"{fact_id}: 바뀐 것 없음", file=out)
        return 0
    # 같은 칸을 다른 판정의 교정이 이미 다른 값으로 고쳤으면 알린다 — 마지막 교정이 이기므로 두 판정의 원문을 대조해 정본을 정한다(D14)
    for x in db.q("SELECT id, new, verdict_id FROM ev_correction WHERE fact_id=? ORDER BY id", (fact_id,)):
        try:
            pn = json.loads(x["new"] or "{}")
        except ValueError:
            pn = {}
        for col in new:
            if col in pn and str(pn[col]) == str(old.get(col)) and str(pn[col]) != str(new[col]) and x["verdict_id"] != verdict_id:
                print(f"  ⚠ {fact_id}.{col}: 교정 #{x['id']}(판정 #{x['verdict_id']})이 {pn[col]}로 고친 값을 {new[col]}로 다시 바꾼다 — "
                      "두 판정의 원문을 대조해 정본을 정하고 사유(--reason)에 적어라", file=out)
    # 상세 칸이 머리 칸을 겸하는 것(statement.quote · disclosure.url · 기간·시장 · 기준일 칸)도 같이 맞춘다
    if k:
        for col in ("quote", "url", "market"):
            if col in dd and col in f:
                hd.setdefault(col, dd[col])
        if k.period and k.period in dd:
            hd.setdefault("period", None if dd[k.period] is None else str(dd[k.period]))
        if k.as_of and k.as_of in dd:
            hd.setdefault("as_of", None if dd[k.as_of] is None else str(dd[k.as_of]))
        for col in ("quote", "url", "market"):
            if col in hd and col in dtypes and col not in dd:
                dd[col] = hd[col]
    if verdict_id is None:
        v = db.q1("SELECT id FROM ev_verdict WHERE fact_id=? AND verdict IN ('MISMATCH','STALE') ORDER BY id DESC LIMIT 1",
                  (fact_id,))
        verdict_id = v["id"] if v else None
    elif not db.q1("SELECT 1 AS o FROM ev_verdict WHERE id=? AND fact_id=?", (int(verdict_id), fact_id)):
        raise Reject(f"판정 #{verdict_id}은 {fact_id}의 판정이 아니다")
    rid = _run(run)[0] if run else None
    value_changed = bool(dd) or bool(set(hd) & VALUE_HEADER)
    if not value_changed and "source_version_id" in hd:
        # ★ 근거 본문 판을 바꿨다(W03) — 사실의 인용이 옛 판과 새 판 **모두에** 그대로 있을 때만 연결 정정(표기)이다. 확인하지 못하면
        #   근거 변경으로 보고 값 판을 올린다(검증 상태를 되돌리고 그 앞 MATCH는 세지 않는다 · 재검증).
        same, why_ = _same_evidence(db.connect(), f, f.get("source_version_id"), hd["source_version_id"])
        if not same:
            value_changed = True
            print(f"  ※ {fact_id}: 근거 본문 판 v{f.get('source_version_id')} → v{hd['source_version_id']} — {why_} · 값 변경으로 보고 재검증한다",
                  file=out)
    with db.tx() as c:
        if "url" in hd and hd["url"]:
            hd["source_id"] = _source(c, hd["url"], hd.get("tier", f["tier"]))
            vid, vlink, _w = _resolve_version(c, hd["source_id"], hd.get("quote", f.get("quote")))
            hd.update({"source_version_id": vid, "source_version_link": vlink})
        cid, rev = _bump(c, fact_id, hd, dd, old, new, reason.strip(), verdict_id, rid, value_changed, reset_review=True)
        runs = {r[0] for r in c.execute(
            "SELECT run_id FROM ev_fact_run WHERE fact_id=? UNION SELECT run_id FROM ev_claim_ref WHERE fact_id=?"
            " UNION SELECT run_id FROM ex_proposal_fact WHERE fact_id=?", (fact_id, fact_id, fact_id)).fetchall() if r[0]}
        for r in runs:
            c.execute("UPDATE ex_run SET rerender=1 WHERE run_id=? AND closed_at IS NULL", (r,))
        if run:
            _, st, mk = _run(run)
            db.run_start(st, mk)
            c.execute("UPDATE ex_run SET rerender=1 WHERE run_id=?", (rid,))
        if value_changed:
            # 값이 고쳐지면 그 사실을 가리킨 논지 근거의 재검토 표시도 푼다(2026-10-19 — 사실 쪽만 풀리고 논지 표시는 남았다)
            db.thesis_review_clear(fact_id=fact_id)
        db.mark_export("sources")
    ch = " · ".join(f"{x}: {_vfmt(old.get(x))} → {_vfmt(new.get(x))}" for x in new)
    print(f"교정 #{cid} {fact_id} rev {rev - 1}→{rev}{f' · 판정 #{verdict_id}' if verdict_id else ''} — {ch}", file=out)
    print(f"  → {render_line(fact_id)}", file=out)
    return 0


# ───────────────────────────── 판 이력(2026-10-19 · Codex R04) ─────────────────────────────

_SNAP_SKIP = {"rid", "id", "kind", "rev", "value_rev", "created_at", "updated_at", "ko", "extra", "ord", "verified_status",
              "verified_at", "verified_tier", "verified_run", "needs_review", "source_id", "fact_id"}


def _version_snap(fact_id: str, rev: int):
    """판 rev의 전부 — 지금 판이면 현재 행, 지난 판이면 판 기록. 옛 형식(머리 6칸)이면 detail=None."""
    c = db.connect()
    f = c.execute("SELECT rev FROM ev_fact WHERE id=?", (fact_id,)).fetchone()
    if not f:
        raise Reject(f"없는 사실 {fact_id}")
    if int(rev) == int(f["rev"] or 1):
        return _snapshot(c, fact_id)
    r = c.execute("SELECT snapshot FROM ev_fact_version WHERE fact_id=? AND version=? ORDER BY id LIMIT 1", (fact_id, int(rev))).fetchone()
    if not r:
        raise Reject(f"{fact_id}에 rev {rev} 판 기록이 없다(판 1~{f['rev']})")
    snap = json.loads(r["snapshot"] or "{}")
    if "header" in snap:
        return snap
    return {"kind": None, "header": snap, "detail": None}        # 옛 트리거 기록 — 머리 6칸뿐


def history(fact_id: str) -> str:
    """판마다 한 줄 — 판 · 바뀐 시각 · 교정 번호·사유 · 바뀐 칸(옛·새)."""
    f = db.q1("SELECT rev, value_rev FROM ev_fact WHERE id=?", (fact_id,))
    if not f:
        raise Reject(f"없는 사실 {fact_id}")
    L = [f"{fact_id} — 지금 rev {f['rev']} · 값 판 {f['value_rev']} (값 판보다 앞 판을 본 판정은 세지 않는다)"]
    for v in db.q("SELECT version, changed_at, correction_id, snapshot FROM ev_fact_version WHERE fact_id=? ORDER BY version, id", (fact_id,)):
        cor = db.q1("SELECT id, old, new, reason, verdict_id FROM ev_correction WHERE id=?", (v["correction_id"],)) if v["correction_id"] else None
        if cor is None:
            cor = db.q1("SELECT id, old, new, reason, verdict_id FROM ev_correction WHERE fact_id=? AND reason NOT LIKE '이미 반영됨%'"
                        " ORDER BY id LIMIT 1 OFFSET ?", (fact_id, int(v["version"]) - 1))
        fmt = "전부" if "header" in json.loads(v["snapshot"] or "{}") else "머리 6칸(옛 기록)"
        ch = ""
        if cor:
            try:
                o, n = json.loads(cor["old"] or "{}"), json.loads(cor["new"] or "{}")
            except ValueError:
                o, n = {}, {}
            ch = " · ".join(f"{k}: {_vfmt(o.get(k))} → {_vfmt(n.get(k))}" for k in n)
            ch = f" · 교정 #{cor['id']}{(' (판정 #' + str(cor['verdict_id']) + ')') if cor['verdict_id'] else ''} — {cor['reason']} · {ch}"
        L.append(f"  rev {v['version']} → {int(v['version']) + 1} · {v['changed_at']} · 판 기록 {fmt}{ch}")
    return "\n".join(L)


def show_rev(fact_id: str, rev: int) -> str:
    snap = _version_snap(fact_id, rev)
    head = {k: v for k, v in (snap.get("header") or {}).items() if k not in ("ko", "extra")}
    out = [f"{fact_id} rev {rev}" + ("" if snap.get("detail") is not None else " — 옛 판 기록(머리 6칸뿐 · 세부 값 없음)")]
    out.append("머리: " + json.dumps(head, ensure_ascii=False, default=str))
    if snap.get("detail") is not None:
        out.append("세부: " + json.dumps({k: v for k, v in snap["detail"].items() if k != "fact_id"}, ensure_ascii=False, default=str))
    return "\n".join(out)


def from_rev_sets(fact_id: str, rev: int) -> dict:
    """`correct --from-rev N` — 판 N의 값 칸(세부 + 고칠 수 있는 머리 칸) 중 지금과 다른 것. 이력은 고쳐 쓰지 않고 새 교정으로 되돌린다."""
    snap = _version_snap(fact_id, rev)
    if snap.get("detail") is None:
        raise Reject(f"{fact_id} rev {rev}는 옛 형식 판 기록이라 세부 값이 없다 — `--set`으로 직접 고쳐라")
    cur = _snapshot(db.connect(), fact_id)
    sets = {}
    for k, v in snap["detail"].items():
        if k not in _SNAP_SKIP and cur["detail"].get(k) != v:
            sets[k] = v
    for k in HEADER_EDIT:
        if k in snap["header"] and k not in sets and cur["header"].get(k) != snap["header"].get(k):
            sets[k] = snap["header"][k]
    return sets


# ───────────────────────────── 렌더: §12 · 세 시점 대조(srcledger 형식 그대로) ─────────────────────────────

def _run_rows(rid: str) -> list:
    """이번 run의 사실(S 번호 순) — 옛 srcledger load()의 rows 모양(fact·quote·as_of·retrieved·source·url·tier·load_bearing·id)."""
    out = []
    for r in db.q("SELECT f.*, r.s_no FROM ev_fact_run r JOIN ev_fact f ON f.id=r.fact_id WHERE r.run_id=? ORDER BY r.s_no", (rid,)):
        out.append({"fact": (r["legacy_text"] if r["kind"] == "legacy" else render_line(r["id"])) or r["kind"],
                    "quote": r["quote"], "as_of": r["as_of"], "retrieved": r["retrieved"], "source": r["source_label"],
                    "url": r["url"], "tier": r["tier"], "load_bearing": bool(r["load_bearing"]) if r["load_bearing"] is not None else True,
                    "id": r["id"], "s_no": r["s_no"]})
    return out


def _s12_written(prev: str) -> dict:
    """앞서 렌더한 §12 → {S번호: (written 칸을 뺀 행, written)} — 다시 렌더할 때 안 바뀐 행의 작성 시각을 지킨다."""
    out = {}
    for ln in (prev or "").splitlines():
        if not ln.startswith("| S") or not ln.rstrip().endswith(" |"):
            continue
        # 칸: | S | 사실 | 인용 | as-of | retrieved | written | 출처 | — written은 뒤에서 둘째 칸(사실·인용 안의 '|'는 \|)
        parts = re.split(r"(?<!\\) \| ", ln.strip()[2:-2])
        if len(parts) != 7:
            continue
        out[parts[0]] = (" | ".join(parts[:5] + parts[6:]), parts[5])
    return out


def render_s12(stamp: str, market: str, out_path: str = "", prev: str = "") -> str:
    """노트 말미 §12 — srcledger render와 같은 머리줄·칸. 사실 칸에는 종류별 한 줄. `written`은 이 시각이다.
    prev(앞서 렌더한 §12)를 주면 내용이 안 바뀐 행은 그때 written을 지키고, 행이 하나도 안 바뀌었으면 끝 스탬프 시각도 지킨다
    — 같은 저장소 상태로 다시 렌더하면 글자 그대로 같다(stage.py render-note의 재렌더 멱등)."""
    rid, _, _ = _run((stamp, market))
    rows = _run_rows(rid)
    written = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    old_rows = _s12_written(prev)
    m_old = re.search(r"<!-- written §12 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) -->", prev or "")
    kept = 0
    buf = io.StringIO()
    print("## §12 출처 원장 — 세 시점\n", file=buf)
    print("> 각 사실이 **어느 시점의 정보인지**(as-of), **언제 가져왔는지**(retrieved),", file=buf)
    print("> **언제 이 노트에 썼는지**(written)를 분리해 적는다. 다음에 이 노트를 볼 때", file=buf)
    print("> 어느 숫자가 낡았는지 판단할 수 있어야 하기 때문이다.\n", file=buf)
    if not rows:
        print("*(이번 run에 기록된 외부 출처 없음 — 캡처 파일의 시세만 사용했다면 그렇게 적는다)*", file=buf)
    else:
        print("| S | 사실 | 원문 인용 | as-of | retrieved | written | 출처 |", file=buf)
        print("|---|---|---|---|---|---|---|", file=buf)
        for r in rows:
            src = r.get("source") or "—"
            if r.get("url"):
                src = f"[{src}]({r['url']})"
            if r.get("tier"):
                src += f" ({'data' if r['tier'] == 'data' else 'T' + str(r['tier'])})"
            flag = "" if r.get("load_bearing", True) else " *(참고)*"
            if r.get("id"):
                src += f" · {r['id']}"
            q = str(r.get("quote") or "").replace("|", "\\|").replace("\n", " ")
            q = (f'"{q[:160]}…"' if len(q) > 160 else f'"{q}"') if q else "—"
            fact = str(r["fact"]).replace("|", "\\|").replace("\n", " ")
            sk = f"S{r['s_no']}"
            body = f"{sk} | {fact}{flag} | {q} | {r.get('as_of') or '미확인'} | {r.get('retrieved') or '미기록'} | {src}"
            w = written
            if old_rows.get(sk, (None,))[0] == body:
                w = old_rows[sk][1]
                kept += 1
            print(f"| S{r['s_no']} | {fact}{flag} | {q} | {r.get('as_of') or '미확인'} "
                  f"| {r.get('retrieved') or '미기록'} | {w} | {src} |", file=buf)
    tail = written
    if m_old and kept == len(rows) == len(old_rows):
        tail = m_old.group(1)
    print(f"\n<!-- ✓ §12 -->\n<!-- written §12 {tail} -->", file=buf)
    text = buf.getvalue()
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
    return text


def note_path(stamp: str, market: str, given: str = "") -> Path:
    if given:
        return Path(given)
    c = sorted(ANALYSIS.glob(f"분석노트_{stamp}_{market}_v*.md"))
    return c[-1] if c else ANALYSIS / f"분석노트_{stamp}_{market}_없음.md"


def check(stamp: str, market: str, note: str = "", out_path: str = "") -> tuple:
    """게이트용 세 시점 대조 — srcledger check와 같은 검사 셋·출력 형식(요약 줄 `[세시점] 검사 3건 · 실패 N건`).
    ① 노트가 인용한 링크가 이번 run 사실에 전부 있는가 ② load-bearing 사실에 as-of가 있는가 ③ 완료 스탬프마다 작성 시각이 있는가.
    반환 (종료코드, 출력)."""
    rid, st, mk = _run((stamp, market))
    np_ = note_path(st, mk, note)
    if not np_.exists():
        return 2, f"분석노트가 없다: {np_}"
    text = np_.read_text(encoding="utf-8", errors="ignore")
    rows = _run_rows(rid)
    note_urls = {u.rstrip(".,);") for u in RX_URL.findall(text)}
    led_urls = {r["url"].rstrip(".,);") for r in rows if r.get("url")}
    led_norm = {_norm_url(u) for u in led_urls}
    missing = sorted(u for u in note_urls - led_urls if _norm_url(u) not in led_norm)
    no_asof = [str(r["fact"])[:60] for r in rows if r.get("load_bearing", True) and (r.get("as_of") in ("", "미확인", None))]
    done = {m.group(1) for m in RX_DONE.finditer(text)}
    timed = {m.group(1) for m in RX_WRITTEN.finditer(text)}
    stamps = sorted(done)
    timeless = sorted(done - timed)
    checks = [
        (f"원장 {len(rows)}행 · 노트 링크 {len(note_urls)}개", not missing,
         "링크 전건이 원장에 있다" if not missing else
         f"**원장에 없는 링크 {len(missing)}개**: {', '.join(u[:60] for u in missing[:4])}" + (" …" if len(missing) > 4 else "")),
        (f"load-bearing as-of ({len(rows) - len(no_asof)}/{len(rows)})", not no_asof,
         "전건에 as-of가 있다" if not no_asof else f"**as-of 미확인 {len(no_asof)}건**: {'; '.join(no_asof[:3])}"),
        (f"스탬프 시각 ({len(stamps) - len(timeless)}/{len(stamps)})", bool(stamps) and not timeless,
         "전 스탬프에 written 시각이 있다" if stamps and not timeless else
         (f"**시각 없는 스탬프 {len(timeless)}개**: {', '.join(timeless[:6])}" if timeless else "스탬프가 하나도 없다")),
    ]
    n_fail = sum(1 for _, ok, _ in checks if not ok)
    buf = io.StringIO()
    print(f"# 세 시점 대조 — {st} · {mk.upper()}\n", file=buf)
    try:
        import sessions as _ss
        sess = _ss.session_iso(st, mk)
    except Exception:  # noqa: BLE001 — 세션 날짜를 못 구해도 검사는 한다
        sess = "?"
    print(f"> 대상 `{np_.name}` · 실행 {sess} 세션 · 기록 {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST\n", file=buf)
    print(f"[세시점] 검사 {len(checks)}건 · 실패 {n_fail}건\n", file=buf)
    print("| 검사 | 결과 | 근거 |\n|---|---|---|", file=buf)
    for name, ok, why in checks:
        print(f"| {name} | {'✓' if ok else '**✗**'} | {why} |", file=buf)
    if n_fail:
        print("\n**세 시점 중 하나라도 비면 다음에 이 노트를 볼 때 어느 숫자가 낡았는지 판단할 수 없다.** `facts.py add`로 채우고, "
              "완료 스탬프는 `stage.py stamp`로 두 줄(`<!-- ✓ §N -->` + `<!-- written §N YYYY-MM-DD HH:MM -->`)을 찍는다 — "
              "✓ 줄 안에 시각을 끼우지 마라(게이트가 그 줄을 글자 그대로 찾는다).", file=buf)
    text_out = buf.getvalue()
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text_out, encoding="utf-8")
    return (1 if n_fail else 0), text_out


# ───────────────────────────── 렌더: 클레임 목록 · 판정표 ─────────────────────────────

def _batch_file(stamp: str, market: str, batch: str, verdict: bool = False) -> Path:
    tail = "주문근거" if batch == "track1" else batch
    return ANALYSIS / f"AI클레임_{stamp}_{market}_{tail}{'_판정' if verdict else ''}.md"


def _claim_line(c, rid, fid, n, batch, basis) -> str:
    f = c.execute("SELECT url FROM ev_fact WHERE id=?", (fid,)).fetchone()
    lab = " · ".join(basis.get(fid, [])) + " · " if batch == "track1" and basis.get(fid) else ""
    url = f" — 출처: {f['url']}" if f and f["url"] else ""
    return f"{n}. [{lab}{fid}] {render_line(fid)}{url}"


def render_claims(stamp: str, market: str, batch: str, out_path: str = "", append: bool = False, force: bool = False) -> tuple:
    """클레임 목록(판정 기준 원 문구) — 큐를 고정하고 번호 줄을 쓴다. 이미 있으면 덮어쓰지 않는다(옛 extract와 같다 — 원 문구를 잃는다).
    반환 (종료코드, 메시지)."""
    rid, st, mk = _run((stamp, market))
    out = Path(out_path) if out_path else _batch_file(st, mk, batch)
    if out.exists() and not (append or force):
        return 3, (f"이미 고정된 클레임 목록이 있다: {out} — 덮어쓰지 않는다. 새로 생긴 것만 더하려면 --append"
                   f"(원 문구를 버리고 다시 쓰려면 --force)")
    rows, summ = queue(st, mk, batch, append=append)
    c = db.connect()
    basis = order_basis(rid, c)
    now = datetime.now(KST)
    if out.exists() and append:
        txt = out.read_text(encoding="utf-8")
        have = {int(m.group(1)) for m in re.finditer(r"^(\d+)\.\s", txt, re.M)}
        add = [r for r in rows if r["n"] not in have]
        if not add:
            return 0, f"새 클레임 0건 — 고정본 그대로: {out}"
        lines = ["", f"## 덧붙임 {now:%Y-%m-%d %H:%M} KST (`facts.py render-claims --append`) — 앞 번호는 그대로 판정 기준으로 남는다", ""]
        for r in add:
            lines.append(_claim_line(c, rid, r["fact_id"], r["n"], batch, basis))
            lines += _claim_sub(r)
        txt = re.sub(r"총 \d+건", f"총 {len(rows)}건", txt, count=1)
        out.write_text(txt.rstrip("\n") + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return 0, f"클레임 +{len(add)}건 덧붙임(총 {len(rows)}) → {out}"
    if batch == "track1":
        head = [f"# AI 클레임 — 주문·보유 판단 근거 {st} {mk.upper()} (갈래 ①)", "",
                f"출처: 저장소 ex_proposal_fact·ex_hold_fact·jd_allocation_fact(run {rid}) · 총 {len(rows)}건",
                f"고정: {now:%Y-%m-%d %H:%M} KST · 이 목록이 주문 근거 fact-check의 판정 기준(원 문구)이다 — 사실을 고친 뒤 다시 만들지 않는다",
                "판정이 MATCH(T1/T2)가 아닌 근거는 주문에 쓰지 못한다 · 감사는 주문 뒤(최대 5회) · 감사가 결정을 뒤집으면 소급 매도", ""]
    else:
        start = min((r["n"] for r in rows), default=1)
        head = [f"# AI 클레임 — 사실 큐 {st} {mk.upper()} · 묶음 {batch}", "",
                f"출처: 저장소 ev_fact(run {rid}이 가리킨 사실 중 검증 상태 none·expired) · 총 {len(rows)}건"
                + (f" · 번호 {start}부터(묶음 A 다음)" if batch == "B" and start > 1 else ""),
                f"고정: {now:%Y-%m-%d %H:%M} KST · 이 목록이 fact-check의 판정 기준(원 문구)이다 — 사실을 고친 뒤 다시 만들지 않는다", ""]
    body = []
    for r in rows:
        body.append(_claim_line(c, rid, r["fact_id"], r["n"], batch, basis))
        body += _claim_sub(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(head + body) + "\n", encoding="utf-8")
    return 0, f"클레임 {len(rows)}건 → {out} ({summ})"


def _claim_sub(r: dict) -> list:
    """번호 줄 아래 대조 줄 — 판정 기준(번호 줄)과 섞이지 않게 들여 붙인다(옛 extract의 ↳ 줄)."""
    q = str(r.get("quote") or "").replace("\n", " ")
    return [f"   ↳ 대조 {r['fact_id']} · as-of {r.get('as_of') or '미확인'} · {_tlabel(r.get('tier'))} · {r.get('verified_status')}"
            + (f' · 원문 "{q[:200]}"' if q else " · 원문 인용 없음")]


def _claims_text(path: Path) -> dict:
    """고정된 클레임 목록 → {번호: 원 문구}(대괄호 머리표·출처 꼬리를 뗀 본문)."""
    out = {}
    if not path.exists():
        return out
    for m in re.finditer(r"^(\d+)\.\s+(?:\[[^\]]*\]\s*)?(.*?)(?:\s+—\s+출처:\s+\S+)?\s*$", path.read_text(encoding="utf-8"), re.M):
        out[int(m.group(1))] = m.group(2)
    return out


def _cell(s) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ").strip()


def render_verdict(stamp: str, market: str, batch: str, out_path: str = "", claims_path: str = "") -> tuple:
    """판정표 렌더 — ev_verdict(추가만)가 원본이고 이 파일은 그 렌더본이다. 형식은 옛 판정표 그대로(머리 집계 · `LINT OK` 줄 ·
    단계 표 · 판정 표 8칸 · 교정 지시서 · 심각도 분류 · track1이면 TRACK1 줄) · 끝 줄 `REFS OK`. 반환 (종료코드, 경로)."""
    rid, st, mk = _run((stamp, market))
    c = db.connect()
    out = Path(out_path) if out_path else _batch_file(st, mk, batch, verdict=True)
    cpath = Path(claims_path) if claims_path else (out.with_name(out.name.replace("_판정.md", ".md")) if out_path
                                                   else _batch_file(st, mk, batch))
    errs, warns, cnt, total = lint(st, mk, batch)
    refs_ok, refs_line, refs_probs = check_refs(st, mk)
    rows = _vrows(c, rid, batch)
    fixed = _fixed(c, rid, batch)
    n_of = {f: n for f, n in fixed}
    rows.sort(key=lambda r: (n_of.get(r["fact_id"], 10 ** 6), r["id"]))
    ctext = _claims_text(cpath)
    links, corr = _corr_links(c, rows)
    local = {cid: i for i, cid in enumerate(sorted(corr), 1)}
    sev = {s: 0 for s in "🔴🟡⏳🔵⚪"}
    for x in corr.values():
        sev[x["sev"]] += 1
    blue_extra = sum(1 for r in rows if r["verdict"] == "UNVERIFIABLE" and not links.get(r["id"]))
    sev_line = (f"🔴 {sev['🔴']} / 🟡 {sev['🟡']} / ⏳ {sev['⏳']} / 🔵 {sev['🔵'] + blue_extra} / ⚪ {sev['⚪']}"
                + (f" (🔵 = 교정표 {sev['🔵']}행 + 교정 없는 UNVERIFIABLE {blue_extra}행)" if blue_extra and corr else ""))
    L = []
    if batch == "track1":
        L.append(f"# AI 클레임 판정표 — 주문 근거 {st} {mk.upper()} (갈래 ①)")
    else:
        L.append(f"# AI 클레임 판정표 — {st} {mk.upper()} · 묶음 {batch}")
    L += ["", f"대상: 고정본 `{_rel(cpath)}`(클레임 {len(fixed)}건) — 판정은 각 행 `(클레임 N)` 번호의 원 문구 기준이다. "
              f"판정 원본은 저장소 ev_verdict(추가만 · 사실마다 마지막 round)이고 이 파일은 `facts.py render-verdict`의 렌더본이다 — "
              f"고칠 것은 이 파일이 아니라 `facts.py verdict`·`facts.py correct`로 넣는다.", "",
          f"**MISMATCH {cnt['MISMATCH']}건 · STALE {cnt['STALE']}건 · 🔴(비가역) {sev['🔴']}건** · UNVERIFIABLE {cnt['UNVERIFIABLE']}건 · "
          f"MATCH {cnt['MATCH']}건 (하위 클레임 {total}건)", ""]
    if not errs:
        L += [lint_line(cnt, total, True), ""]
    else:
        L += [f"LINT FAIL — 위반 {len(errs)}건 (facts.py lint {_now_min()})"] + [f"  - {e}" for e in errs[:40]] + [""]
    L += ["| 단계 | 수행 | 증빙 |", "|---|---|---|",
          f"| 3. 내용 일치 | ✓ | MATCH {cnt['MATCH']} · MISMATCH {cnt['MISMATCH']} · STALE {cnt['STALE']} · UNVERIFIABLE {cnt['UNVERIFIABLE']} |",
          f"| 4. 심각도 분류 | ✓ | {sev_line} |", "", "## 판정", "",
          "| 원 문구(위치) | 하위 클레임 | 판정 | 티어 | 원문 인용 | URL | T1 시도 | 비고 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        n = n_of.get(r["fact_id"])
        orig = ctext.get(n) or render_line(r["fact_id"])
        q = _cell(r["_quote"])
        notes = [x for x in (_cell(r["_note"]),) if x]
        if r.get("support"):
            notes.append(f"받치는 것: {_cell(r['support'])}")
        notes += [f"교정 {local[cid]}" for cid in links.get(r["id"], [])]
        notes.append(f"{r['fact_id']} · 판정 #{r['id']} · round {r.get('round') or 0}")
        qcell = f'"{q}"' if q else "—"
        ncell = n if n is not None else "?"
        L.append(f'| "{_cell(orig)}"(클레임 {ncell} · {r["fact_id"]}) | {_cell(r.get("row_label")) or "값·출처"} '
                 f'| {r["verdict"]} | {_tlabel(r["tier"])} | {qcell} | {_cell(r["_url"]) or "—"} '
                 f'| {_cell(r["_t1"]) or "—"} | {" · ".join(notes)} |')
    L += ["", "## 판정 범위 밖", "",
          "(없음 — 클레임 하나 = 저장소 사실 하나라 원 문구 전체를 판정 행이 덮는다. 사실 밖 서술은 노트의 판단 문장이다)", "",
          "## 교정 지시서", ""]
    if corr:
        L += ["| # | 심각도 | 위치 | 현재 | 조치 | 근거 |", "|---|---|---|---|---|---|"]
        for cid in sorted(corr):
            x = corr[cid]
            n = n_of.get(x["fact_id"], "?")
            try:
                o, nw = json.loads(x["old"] or "{}"), json.loads(x["new"] or "{}")
            except ValueError:
                o, nw = {}, {}
            cur = " · ".join(f"{k}: {_vfmt(v)}" for k, v in o.items()) or "—"
            act = " · ".join(f"{k}: {_vfmt(v)}" for k, v in nw.items())
            furl = (db.q1("SELECT url FROM ev_fact WHERE id=?", (x["fact_id"],)) or {}).get("url") or ""
            L.append(f"| {local[cid]} | {x['sev']} | 클레임 {n} — {x['fact_id']} · 저장소 교정 #{cid} · 적용 {str(x['applied_at'])[:16]} "
                     f"| {_cell(cur)} | UPDATE {x['fact_id']} — {_cell(act)} · 사유: {_cell(x['reason'])} | {_cell(furl)} · 클레임 {n} |")
    else:
        L.append("(교정 없음)")
    L += ["", "## 심각도 분류", "", sev_line, ""]
    if batch == "track1":
        sig = db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (rid,))
        try:
            t1 = json.loads(sig["track1"]) if sig and sig.get("track1") else {}
        except ValueError:
            t1 = {}
        if t1.get("line"):
            L.append(t1["line"])
        if (t1.get("post") or {}).get("line"):
            L.append(t1["post"]["line"])
        if t1.get("line"):
            L.append("")
    if warns:
        L += ["## 검사 경고", ""] + [f"- {w}" for w in warns[:60]] + [""]
    if not refs_ok:
        L += [f"- {p}" for p in refs_probs[:60]] + [""]
    L.append(refs_line)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(L) + "\n"
    # 렌더 시각(분)만 다른 재렌더는 파일을 다시 쓰지 않는다(W06) — 감사는 판정표 파일의 해시(본 판)로 지금 판과 잇는다.
    #   내용이 같은데 시각 줄만 바뀌면 이미 받은 감사가 '다른 판'이 되어 감사를 헛되이 다시 받아야 했다.
    if not (out.exists() and _NOW_RX.sub("", out.read_text(encoding="utf-8")) == _NOW_RX.sub("", text)):
        out.write_text(text, encoding="utf-8")
    return (0 if (not errs and refs_ok) else 1), str(out)


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(HERE))
    except ValueError:
        return str(p)


# ───────────────────────────── 주문 근거 결정(decide) — fc_track1.decide 이식 ─────────────────────────────

def _market_open(mk: str) -> tuple:
    try:
        from kis_client import market_session
        sess = market_session(mk.upper())
        return bool(sess.get("is_open")), str(sess.get("why") or "")
    except Exception as e:  # noqa: BLE001
        return False, f"장 상태를 못 읽었다({type(e).__name__}) — 닫힌 것으로 본다"


def _tnum(v):
    s_ = str(v if v is not None else "").strip().upper().lstrip("T")
    return int(s_) if s_ in ("1", "2", "3") else None


def grade_rows(rows: list, value_rev=1) -> dict:
    """★ 판정 등급의 유일한 규칙(2026-10-19 · Codex R04) — 한 사실·한 묶음의 마지막 round 행 → {ok, worst, verdict, tier, rows, stale_rev}.
    값 판(`value_rev`)보다 낮은 판을 본 행은 세지 않는다. 행이 있는데 셀 행이 없으면 REVERIFY — 매수는 MISMATCH처럼 막고
    (`risk_guard`·`decide --mode pre`), 이미 체결된 매수는 팔지 않고 그 run이 재확인·재판정으로 끝낸다(`decide --mode post`).
    셀 행: 전부 MATCH이고 T1/T2가 하나 이상이면 ok · 전부 MATCH인데 T1/T2가 없으면 UNVERIFIED_T1."""
    vr = int(value_rev or 1)
    ids = [f"ev_verdict:{r.get('id')}" for r in rows]
    counting = [r for r in rows if int(r.get("fact_rev") or 0) >= vr]
    if rows and not counting:
        svs = [str(r.get("verdict") or "").strip().upper() for r in rows]
        # stale_rev.worst = 옛 판 행 가운데 가장 나쁜 판정 — MISMATCH였으면 '틀린 값을 고친' 경우라 그 값에 기댄 결정은 무너졌다
        #   (감사 소급 매도 그대로) · MATCH였으면 값이 뒤에 바뀌었을 뿐 틀렸다는 증거는 없다(재확인·재판정)
        return {"ok": False, "worst": "REVERIFY", "verdict": "REVERIFY", "tier": None, "rows": ids,
                "stale_rev": {"verdict_rev": max(int(r.get("fact_rev") or 0) for r in rows), "value_rev": vr,
                              "worst": next((v for v in ("MISMATCH", "UNVERIFIABLE", "STALE") if v in svs), "MATCH")}}
    tiers = [_tnum(r.get("tier")) for r in counting]
    vs = [str(r.get("verdict") or "").strip().upper() for r in counting]
    ok = bool(counting) and all(v == "MATCH" for v in vs) and any(t in (1, 2) for t in tiers)
    worst = next((v for v in ("MISMATCH", "UNVERIFIABLE", "STALE") if v in vs), "MATCH")
    return {"ok": ok, "worst": worst, "verdict": worst if (ok or worst != "MATCH") else "UNVERIFIED_T1",
            "tier": next((t for t in tiers if t in (1, 2)), tiers[0] if tiers else None), "rows": ids, "stale_rev": None}


def grade_verdicts(c, rid: str, fact_ids=None, batch: str = "track1") -> dict:
    """(run, 묶음)의 마지막 round 판정 → {fact_id: grade_rows(...)} — `decide`와 `risk_guard.fact_states`가 같은 규칙을 쓴다."""
    want = None if fact_ids is None else {str(x) for x in fact_ids}
    by = {}
    for r in _vrows(c, rid, batch):
        if want is None or r["fact_id"] in want:
            by.setdefault(r["fact_id"], []).append(r)
    return {fid: grade_rows(rs, rs[0].get("f_value_rev")) for fid, rs in by.items()}


def _grade_facts(c, rid: str) -> dict:
    """주문 근거 묶음(track1)의 마지막 round 판정 → {fact_id: {ok, worst, verdict, tier, rows, stale_rev}}."""
    return grade_verdicts(c, rid, None, "track1")


def _grade(c, rid: str) -> tuple:
    g = _grade_facts(c, rid)
    none = {"ok": False, "verdict": "UNVERIFIED_T1", "tier": None, "rows": []}
    passed, dropped, detail = set(), set(), {}
    for p in c.execute("SELECT * FROM v_proposal WHERE run_id=? ORDER BY idx", (rid,)).fetchall():
        try:
            body = json.loads(p["body"]) if p["body"] else {}
        except ValueError:
            body = {}
        if p["action"] not in ("BUY", "SELL") or body.get("source") == "discipline":
            continue
        rows_pf = c.execute("SELECT fact_id, role FROM v_proposal_fact WHERE run_id=? AND idx=? ORDER BY ord_",
                            (rid, p["idx"])).fetchall()
        evs = [(r[0], g.get(r[0], none)) for r in rows_pf]
        role = {r[0]: (r[1] or "support") for r in rows_pf}          # 역할이 비면 support(risk_guard와 같다)
        detail[(p["action"], p["ticker"])] = {"idx": p["idx"], "name": body.get("name", ""), "thesis_id": p["thesis_id"] or "",
                                              "evidence": evs, "roles": role}
        if p["action"] == "BUY":
            # 통과 = 받치는 사실(support) 중 하나 이상이 주문 근거 묶음에서 MATCH(T1/T2) · 받치는 사실에 미검증·MISMATCH 없음 ·
            #   배경 사실에 MISMATCH 없음. 반대 사실(against)은 통과·제외를 바꾸지 않는다 — 배경·반대 사실 하나가 MATCH라고
            #   매수가 서지 않는다(2026-10-17 · risk_guard 이중 검사와 같은 규칙).
            sup = [e for fid, e in evs if role[fid] == "support"]
            bg = [e for fid, e in evs if role[fid] == "background"]
            # REVERIFY(값이 판정 뒤에 바뀜)는 매수를 막지만 '틀렸다'는 증거는 아니다 — 체결 뒤(post)에는 소급 매도가 아니라 재확인·재판정
            reverify = [fid for fid, e in evs if role[fid] != "against" and e["verdict"] == "REVERIFY"]

            def _was_wrong(e):        # 옛 판 판정이 MISMATCH였다 = 틀린 값을 고쳤다 → 그 값에 기댄 결정은 무너졌다(소급 매도 그대로)
                return e["verdict"] == "REVERIFY" and (e.get("stale_rev") or {}).get("worst") == "MISMATCH"
            hard = (any(e["verdict"] in ("UNVERIFIED_T1", "MISMATCH") or _was_wrong(e) for e in sup)
                    or any(e["verdict"] == "MISMATCH" or _was_wrong(e) for e in bg)
                    or not any((e["ok"] and e["verdict"] == "MATCH" and e["tier"] in (1, 2)) or e["verdict"] == "REVERIFY" for e in sup))
            detail[(p["action"], p["ticker"])]["reverify"] = reverify
            if not any(e["ok"] and e["verdict"] == "MATCH" and e["tier"] in (1, 2) for e in sup) \
                    or any(e["verdict"] in ("UNVERIFIED_T1", "MISMATCH", "REVERIFY") for e in sup) \
                    or any(e["verdict"] in ("MISMATCH", "REVERIFY") for e in bg):
                dropped.add(p["ticker"])
                detail[(p["action"], p["ticker"])]["soft_drop"] = not hard
            else:
                passed.add(p["ticker"])
                # 통과했지만 받치는 사실 가운데 낡았거나(STALE) 확인 못 한(UNVERIFIABLE) 것 — post가 stale_support로 내고
                # 그 run이 재확인 또는 보유 재판정으로 끝낸다(FB-261017-9 · 근거가 무너진 매수를 하루 더 들지 않는다)
                detail[(p["action"], p["ticker"])]["stale"] = [fid for fid, e in evs if role[fid] == "support"
                                                               and e["verdict"] in ("STALE", "UNVERIFIABLE")]
    holds_bad = []
    hf = {}
    for r in c.execute("SELECT ticker, fact_id FROM ex_hold_fact WHERE run_id=? ORDER BY rowid", (rid,)).fetchall():
        hf.setdefault(r["ticker"], []).append(r["fact_id"])
    for t, fs in hf.items():
        if not all(g.get(f, none)["ok"] for f in fs):
            holds_bad.append(t)
    st, mk = rid.split("_", 1)
    alloc_bad = [r[0] for r in c.execute(
        "SELECT fact_id FROM jd_allocation_fact WHERE allocation_id=? ORDER BY rowid", (f"AL-{st}-{mk}",)).fetchall()
        if not g.get(r[0], none)["ok"]]
    return passed, dropped, holds_bad, alloc_bad, detail


def audit_rounds(stamp: str, market: str, ledger=None) -> list:
    """주문 근거 판정표의 감사 기록(시간순) [(ts, status)] — 게이트 엔진 원장을 op_audit로 옮겨(db.audit_mirror) 거기서 센다.
    대체된 감사(superseded)는 세지 않는다(fc_track1.audit_rounds와 같다)."""
    rid, st, mk = _run((stamp, market))
    if ledger:
        if Path(ledger).exists():
            db.audit_mirror(st, mk, Path(ledger))
    else:
        db.audit_mirror(st, mk)          # 원장 경로는 db 규칙(AITB_STEPGATE_LEDGER · 시험 저장소에는 실제 엔진 원장을 안 옮긴다)
    out = []
    # 읽기는 db.audit_rows 하나(v4 · Codex U03) — 다른 폴더(예행)의 감사(foreign)는 세지 않는다
    for r in db.audit_rows(rid, "fact-check"):
        b = r.get("body_obj") or {}
        series = str(b.get("run") or r.get("deliverable") or "").lower()
        if b.get("type") != "finding" or b.get("superseded") or "주문근거" not in series:
            continue
        out.append((r["at"] or "", r["status"]))
    return out


def audit_current(stamp: str, market: str, ledger=None) -> tuple:
    """주문 근거 판정표의 **지금 판** 감사 결과(W06) — (PASS|FAIL|None, 사유). 회차 이력(`audit_rounds`)과 달리 본 판정표 판이 지금 파일과
    같은 감사만 본다(`db.audit_rows` current_ok) — 같은 이름·같은 폴더라도 해시가 다르면 지금 판의 PASS가 아니다. 범위 불명(unknown)은 이력일 뿐이다."""
    rid, st, mk = _run((stamp, market))
    if ledger:
        if Path(ledger).exists():
            db.audit_mirror(st, mk, Path(ledger))
    else:
        db.audit_mirror(st, mk)
    rows = []
    for r in db.audit_rows(rid, "fact-check"):
        b = r.get("body_obj") or {}
        series = str(b.get("run") or r.get("deliverable") or "").lower()
        if b.get("type") != "finding" or b.get("superseded") or "주문근거" not in series:
            continue
        rows.append(r)
    if not rows:
        return None, "감사 기록 없음"
    cur = [r for r in rows if r.get("current_ok")]
    if not cur:
        last = rows[-1]
        seen = {"mismatch": "다른 판(해시 불일치)", "no_hash_stale": "감사 뒤 파일이 바뀐 판", "no_file": "없는 파일",
                "match": "범위 불명인 판", "no_hash_fresh": "범위 불명인 판"}.get(last.get("content_state"), str(last.get("content_state")))
        return None, f"지금 판정표에 대한 감사가 없다 — 마지막 감사({last.get('status')} · {last.get('at')})는 {seen}을 봤다"
    return cur[-1].get("status"), ""


def filled_buys(stamp: str, market: str, refs: bool = False) -> dict:
    """이 run의 승인으로 체결된 매수 {종목: 체결 수량} — 본 승인(approved_<st>_<mk>.json)과 재투자 승인
    (approved_<st>_<mk>_refill_<tag>.json) 둘 다(재투자로 산 것도 같은 근거 규칙이다 · FB-261017-9).
    `refs=True`면 {종목: {"qty": 수량, "refs": [승인 파일 이름…]}}."""
    rid, st, mk = _run((stamp, market))
    out = {}
    # 주문마다 마지막 체결 관측(v_fill_latest · v7 R11) — 누적 관측 행을 더하면 PARTIAL 1 → FILLED 2가 3주가 된다
    for r in db.q("SELECT o.ticker, o.qty, o.approved_ref, f.filled_qty, f.verdict FROM ex_order o JOIN v_fill_latest f ON f.order_id=o.id"
                  " WHERE o.run_id=? AND UPPER(o.side)='BUY' AND COALESCE(o.status,'')<>'FAILED'"
                  " AND f.verdict IN ('FILLED','PARTIAL') AND (o.approved_ref LIKE ? OR o.approved_ref LIKE ?)",
                  (rid, f"%approved_{st}_{mk}.json", f"%approved_{st}_{mk}_refill_%.json")):
        q = int(r["filled_qty"] or (r["qty"] if r["verdict"] == "FILLED" else 0) or 0)
        if q <= 0:
            continue
        if refs:
            d = out.setdefault(r["ticker"], {"qty": 0, "refs": []})
            d["qty"] += q
            ref = Path(str(r["approved_ref"] or "")).name
            if ref and ref not in d["refs"]:
                d["refs"].append(ref)
        else:
            out[r["ticker"]] = out.get(r["ticker"], 0) + q
    return out


def _reversal(st: str, mk: str, ticker: str, qty: int, why: str, **kw) -> tuple:
    """되돌림 매도 행(db.reversal_append) — 같은 (스탬프, 시장, 종목, 종류)의 살아 있는 행이 있으면 새로 쓰지 않는다(멱등).
    소급(buy_unsupported) 행은 `decide --mode post`만 낸다 — 승인 파일(buy_ref)과 판정 행(fc_rows)이 없으면 거부한다(FB-261017-9)."""
    if kw.get("kind", "buy_unsupported") == "buy_unsupported" and not (kw.get("buy_ref") and kw.get("fc_rows")):
        raise ValueError("buy_unsupported 행은 buy_ref·fc_rows가 있어야 한다 — decide --mode post만 낸다(FB-261017-9)")
    MK = mk.upper()
    rows = db.reversals_all()
    for r in rows:
        if (r.get("stamp") == st and str(r.get("market", "")).upper() == MK and r.get("ticker") == ticker
                and r.get("kind") == kw.get("kind", "buy_unsupported") and r.get("status") in ("pending", "filled")):
            return r, False
    n = 1 + sum(1 for r in rows if r.get("stamp") == st and str(r.get("market", "")).upper() == MK)
    row = {"id": f"REV-{st}-{mk.lower()}-{n}", "stamp": st, "market": MK, "ticker": ticker, "name": kw.get("name", ""),
           "kind": kw.get("kind", "buy_unsupported"), "qty": int(qty), "filled_qty": int(qty), "why": why,
           "round": kw.get("round_", 0), "fc_rows": list(kw.get("fc_rows") or []), "buy_ref": kw.get("buy_ref", ""),
           "thesis_id": kw.get("thesis_id", ""), "status": "pending"}
    db.reversal_append(row)
    return row, True


def _max_rounds() -> int:
    try:
        lim = json.loads((HERE / "config" / "limits.json").read_text(encoding="utf-8"))
        return int(lim.get("fc_track1_max_rounds") or MAX_ROUNDS_DEFAULT)
    except (OSError, ValueError):
        return MAX_ROUNDS_DEFAULT


def decide(stamp: str, market: str, mode: str = "pre", max_rounds: int = None, ledger=None, signal_path: str = "",
           out=None, err=None) -> int:
    """pre(5단 · 감사 없이 판정 기준) → ex_signal.track1(result·pass·drop · audit=PENDING) · `TRACK1 RESULT:` 줄.
    post(6단 · 감사 기록이 생길 때마다) → 마지막 round 판정으로 다시 계산 · 체결된 매수가 이제 '제외'면 되돌림 매도(db.reversal_append) ·
    `TRACK1 POST:` 줄. 판정 검사(lint)가 통과해야 결정한다(옛 'LINT OK' 줄 요구와 같다 · 교정 전파 줄은 설계상 없다)."""
    out, err = out or sys.stdout, err or sys.stderr
    rid, st, mk = _run((stamp, market))
    max_rounds = max_rounds or _max_rounds()
    c = db.connect()
    if not _fixed(c, rid, "track1"):
        print("아직 결정할 수 없다 — 주문 근거 큐(`facts.py queue --batch track1`)와 판정이 먼저다", file=err)
        return 2
    errs, _, _, _ = lint(st, mk, "track1")
    if errs:
        print(f"아직 결정할 수 없다 — 주문 근거 판정 검사 LINT FAIL {len(errs)}건(`facts.py lint --batch track1`): {errs[0]}", file=err)
        return 1
    rounds = audit_rounds(st, mk, ledger)
    n_fail = sum(1 for _, s in rounds if s == "FAIL")
    # 지금 판정표 판의 감사 결과(W06) — 회차 이력의 마지막 줄이 아니라 지금 파일과 같은 판을 본 감사만 PASS가 된다
    last, cur_why = audit_current(st, mk, ledger)
    if mode == "post" and not rounds:
        print("아직 감사 기록이 없다 — 감사자가 audit-judge --record로 남긴 뒤에 post를 돌린다", file=err)
        return 1
    now = datetime.now(KST)
    sig = db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (rid,))
    try:
        t1 = json.loads(sig["track1"]) if sig and sig.get("track1") else {}
    except ValueError:
        t1 = {}
    passed, dropped, holds_bad, alloc_bad, detail = _grade(c, rid)
    ref = f"analysis/{_batch_file(st, mk, 'track1', verdict=True).name}"
    if mode == "pre":
        line = (f"TRACK1 RESULT: audit=PENDING(집행 뒤 · 최대 {max_rounds}회) pass={','.join(sorted(passed)) or '-'} "
                f"drop={','.join(sorted(dropped)) or '-'} holds_unverified={','.join(map(str, holds_bad)) or '-'} "
                f"allocation_unverified={len(alloc_bad)} ({now:%Y-%m-%d %H:%M})")
        t1 = {"result": "PASS" if not dropped else ("DROP" if not passed else "PARTIAL"), "audit": "PENDING", "rounds": 0,
              "pass": sorted(passed), "drop": sorted(dropped), "holds_unverified": holds_bad, "allocation_unverified": alloc_bad,
              "ref": ref, "at": now.isoformat(timespec="seconds"), "line": line}
        _save_track1(rid, st, mk, t1, signal_path)
        print(line, file=out)
        if dropped:
            print(f"  제외 — {', '.join(sorted(dropped))}: 판정에서 근거가 서지 않았다(논지는 armed 유지 · 다음 run이 다시 본다)", file=out)
        return 0
    pre_pass = set(t1.get("pass") or [])
    filled_r = filled_buys(st, mk, refs=True)
    filled = {t: d["qty"] for t, d in filled_r.items()}
    audit = "PASS" if last == "PASS" else (f"FAIL×{n_fail}" if (last == "FAIL" or n_fail) else "STALE")
    if audit == "STALE":
        print(f"  ※ 감사 STALE — {cur_why} · 지금 판정표로 감사를 다시 받는다(audit-judge가 그 판 해시를 남긴다)", file=out)
    over = n_fail > max_rounds
    rev_ids = list((t1.get("post") or {}).get("reversals") or [])
    # 값이 판정 뒤에 바뀌어서만 빠진 매수(REVERIFY · soft_drop)는 팔지 않는다 — 아래 낡은 근거로 넘겨 이 run이 재확인·재판정으로 끝낸다
    targets = sorted(t for t in pre_pass & dropped if filled.get(t, 0) > 0
                     and not (detail.get(("BUY", t)) or {}).get("soft_drop"))
    for tic in targets:
        d = detail.get(("BUY", tic), {})
        bad = [(fid, e) for fid, e in d.get("evidence", []) if (e["verdict"] != "MATCH" or not e["ok"])
               and (d.get("roles") or {}).get(fid, "support") != "against"]
        why = (f"감사 {len(rounds)}회차({audit}) 뒤 재채점 — 근거 "
               + "; ".join(f"{fid} {render_line(fid)[:60]} → {e['verdict']}" for fid, e in bad[:3]))
        row, new = _reversal(st, mk, tic, filled[tic], why, kind="buy_unsupported", name=d.get("name", ""), round_=len(rounds),
                             fc_rows=([x for _, e in bad for x in e["rows"]] or [f"ev_fact:{fid}" for fid, _ in bad]
                                      or [f"ev_fact:{fid}" for fid, _ in d.get("evidence", [])] or [f"ex_proposal:{rid}:{d.get('idx')}"]),
                             buy_ref=";".join(filled_r[tic]["refs"]) or f"approved_{st}_{mk}.json",
                             thesis_id=d.get("thesis_id", ""))
        if row["id"] not in rev_ids:
            rev_ids.append(row["id"])
        print(f"  ★ 감사 소급 {row['id']} {tic} {filled[tic]}주 — {'새로 기록' if new else '이미 기록됨'} · {why[:120]}", file=out)
    # 낡은 근거 — 통과했고 체결됐지만 받치는 사실에 STALE·UNVERIFIABLE이 남은 매수. 팔지 않는 대신 이 run이 끝낸다:
    #   ① 사실 재확인(correct → verdict → post 다시) ② 보유 재판정(set-status held --note / reverse) — 미결이면 close가 닫지 않는다
    stale_support = [{"ticker": t, "facts": detail[("BUY", t)]["stale"]} for t in sorted(passed)
                     if filled.get(t, 0) > 0 and (detail.get(("BUY", t)) or {}).get("stale")]
    stale_support += [{"ticker": t, "facts": (detail[("BUY", t)].get("reverify") or [])
                       + [f for f in (detail[("BUY", t)].get("stale") or []) if f not in (detail[("BUY", t)].get("reverify") or [])]}
                      for t in sorted(pre_pass & dropped)
                      if filled.get(t, 0) > 0 and (detail.get(("BUY", t)) or {}).get("soft_drop")]
    stale_txt = ",".join(f"{x['ticker']}({'|'.join(x['facts'])})" for x in stale_support) or "-"
    line = (f"TRACK1 POST: audit={audit} rounds={len(rounds)}/{max_rounds} pass={','.join(sorted(passed)) or '-'} "
            f"drop={','.join(sorted(dropped)) or '-'} reversals={','.join(rev_ids) or '-'} stale_support={stale_txt} "
            f"holds_unverified={','.join(map(str, holds_bad)) or '-'} allocation_unverified={len(alloc_bad)} ({now:%Y-%m-%d %H:%M})")
    t1 = {**t1, "audit": audit, "rounds": len(rounds),
          "post": {"audit": audit, "rounds": len(rounds), "pass": sorted(passed), "drop": sorted(dropped), "reversals": rev_ids,
                   "stale_support": stale_support,
                   "holds_unverified": holds_bad, "allocation_unverified": alloc_bad, "over_limit": over,
                   "at": now.isoformat(timespec="seconds"), "line": line}}
    _save_track1(rid, st, mk, t1, signal_path)
    print(line, file=out)
    if over:
        print(f"  ※ 감사 FAIL {n_fail}회 — 한도 {max_rounds}회를 넘겼다. 더 고치지 않는다(기록만 · 토요일 승격)", file=out)
    if targets:
        is_open, why_s = _market_open(mk)
        if is_open:
            print(f"  → 장중이다 — 지금 `python3 stops.py --market {mk} --send`로 되돌림 매도를 보낸다(같은 run · 사람 확인 없음)", file=out)
        else:
            print(f"  → 장이 닫혔다({why_s}) — 대기로 남긴다. 다음 {mk.upper()} run의 risk_guard가 자동으로 판다", file=out)
    for x in stale_support:
        print(f"  ※ 낡은 근거 {x['ticker']} — {', '.join(x['facts'])}: 팔지 않는다(다른 받치는 사실 MATCH(T1/T2)) · 이 run에서 끝낸다 — "
              f"① 사실 재확인(`facts.py correct` → `facts.py verdict` → `decide --mode post`) 또는 ② 보유 재판정"
              f"(`theses.py set-status --id <논지> --status held --note \"[보유 재판정] …\"` / `facts.py reverse`) · "
              f"미결이면 `stage.py close`가 닫지 않는다", file=out)
    for h in holds_bad:
        print(f"  ※ 보유 판단 재판정 필요: {h} — 근거가 무너졌으면 `facts.py reverse --stamp {st} --market {mk} --ticker {h} "
              f"--qty N --why …`로 판다", file=out)
    return 0


def reverse(stamp: str, market: str, ticker: str, qty: int, why: str, kind: str = "hold_overturned", out=None, err=None) -> int:
    """보유 판단 재판정 매도(모델이 판단) — 되돌림 매도 행(db.reversal_append · 멱등). 다음 risk_guard·stops가 보낸다.
    종류는 hold_overturned뿐이다 — 소급(buy_unsupported)은 `decide --mode post`만 낸다(판단으로 되팔지 않는다 · FB-261017-9).
    대상 = 이 run TRACK1의 holds_unverified · 이 run post의 stale_support(같은 run 보유 재판정) · 직전 같은 시장 run post의
    stale_support·holds_unverified(늦게 온 감사). 그 밖의 이 run 체결 매수와 대상 밖 종목은 거부한다(rc 3)."""
    out, err = out or sys.stdout, err or sys.stderr
    rid, st, mk = _run((stamp, market))
    if not ticker or int(qty or 0) <= 0 or not str(why or "").strip():
        print("reverse는 --ticker · --qty(>0) · --why가 필요하다", file=err)
        return 2
    if kind != "hold_overturned":
        print("--kind는 hold_overturned뿐이다 — 소급(buy_unsupported)은 decide --mode post가 낸다", file=err)
        return 2

    def _t1(run_id):
        r = db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (run_id,))
        try:
            return json.loads(r["track1"]) if r and r.get("track1") else {}
        except ValueError:
            return {}
    cur = _t1(rid)
    cur_post = cur.get("post") or {}
    holds_now = set((cur_post or cur).get("holds_unverified") or [])
    stale_now = {x.get("ticker") for x in cur_post.get("stale_support") or []}
    prev = db.q1("SELECT r.run_id FROM ex_run r JOIN ex_signal s ON s.run_id=r.run_id WHERE LOWER(r.market)=? AND r.stamp<?"
                 " AND s.track1 IS NOT NULL ORDER BY r.stamp DESC LIMIT 1", (mk, st))
    pp = (_t1(prev["run_id"]).get("post") or {}) if prev else {}
    stale_prev = {x.get("ticker") for x in pp.get("stale_support") or []}
    holds_prev = set(pp.get("holds_unverified") or [])
    if ticker in filled_buys(st, mk) and ticker not in stale_now:
        print(f"{ticker}는 이 run의 체결 매수다 — 소급은 `facts.py decide --mode post`가 낸다(판단으로 되팔지 않는다 · FB-261017-9)", file=err)
        return 3
    if ticker not in holds_now | stale_now | stale_prev | holds_prev:
        print(f"{ticker}는 보유 재판정 대상이 아니다 — 이 run TRACK1 holds_unverified={','.join(sorted(holds_now)) or '-'} · "
              f"stale_support={','.join(sorted(stale_now)) or '-'} · 직전 run 낡은 근거={','.join(sorted(stale_prev)) or '-'} · "
              f"직전 run holds_unverified={','.join(sorted(holds_prev)) or '-'}", file=err)
        return 3
    row, new = _reversal(st, mk, ticker, int(qty), str(why).strip(), kind=kind)
    print(f"{'기록' if new else '이미 있음'}: {row['id']} {ticker} {row['qty']}주 — {str(why)[:100]}", file=out)
    return 0


def unreflected(market: str, before: str, lookback: int = 3, ledger=None) -> list:
    """같은 시장 직전 run(최대 lookback개) 가운데 **감사 기록 수가 `decide --mode post`가 반영한 회차보다 많은** 스탬프
    — [(스탬프, 감사 수, 반영 수)]. 배경 감사가 run을 닫은 뒤에 도착하면(미수신으로 닫음) 다음 run 이어받기가 이것으로 받아
    `facts.py decide --mode post`를 돌린다(옛 fc_track1.unreflected · 2026-10-14 안전망). op_audit는 close 때 복사되므로
    늦게 온 감사는 없다 — 먼저 그 run들에 db.audit_mirror(멱등 · 늦게 온 줄만 더 들어간다)를 부른 뒤 센다.
    반영 회차 = ex_signal.track1의 post 회차(없으면 0) · 주문 근거 결정(track1)이 없는 run은 보지 않는다."""
    mk = str(market).lower()
    runs = db.q("SELECT r.stamp, s.track1 FROM ex_run r JOIN ex_signal s ON s.run_id=r.run_id"
                " WHERE LOWER(r.market)=? AND r.stamp<? AND s.track1 IS NOT NULL ORDER BY r.stamp", (mk, str(before)))
    out = []
    for r in runs[-lookback:] if lookback else runs:
        n = len(audit_rounds(r["stamp"], mk, ledger))
        if not n:
            continue
        try:
            t1 = json.loads(r["track1"]) or {}
        except ValueError:
            t1 = {}
        done = int((t1.get("post") or {}).get("rounds") or 0)
        if done < n:
            out.append((r["stamp"], n, done))
    return out


def carry_note(market: str, before: str, **kw) -> str:
    """이어받기 `## 감사 소급` 절 뒤에 붙는 줄 — 반영 안 된 주문 근거 감사가 있으면 이 run에서 먼저 돌린다(옛 fc_track1.carry_note)."""
    rows = unreflected(market, before, **kw)
    mk = str(market).lower()
    txt = ""
    if rows:
        txt = ("\n**① 감사 결과 미반영** — 직전 run이 닫힌 뒤 도착한 주문 근거 감사가 있다. 이 run에서 먼저 돌린다"
               "(재채점이 체결 매수의 근거를 뒤집으면 소급 매도 · 사람 확인 없음):\n"
               + "\n".join(f"- `python3 facts.py decide --mode post --stamp {s} --market {mk}` — 감사 {n}회 · 반영 {d}회"
                           for s, n, d in rows) + "\n")
    # 낡은 근거 — 정상 경로에서는 그 run이 끝낸다(close가 미결을 막는다). 여기 남은 것은 run이 닫힌 뒤 도착한 감사가 만든 것뿐이다.
    prev = db.q1("SELECT r.stamp, s.track1 FROM ex_run r JOIN ex_signal s ON s.run_id=r.run_id WHERE LOWER(r.market)=? AND r.stamp<?"
                 " AND s.track1 IS NOT NULL ORDER BY r.stamp DESC LIMIT 1", (mk, str(before)))
    try:
        pp = (json.loads(prev["track1"]) or {}).get("post") or {} if prev else {}
    except ValueError:
        pp = {}
    items = pp.get("stale_support") or []
    if items:
        txt += (f"\n### 낡은 근거(stale_support) — 보유 재판정 (직전 run {prev['stamp']} TRACK1 POST)\n\n"
                "직전 run이 닫힌 뒤 도착한 감사가 남긴 것이다 — 팔린 것이 아니다. 이 run이 끝낸다:\n\n"
                "| 종목 | 낡은 사실 | 할 일 |\n|---|---|---|\n"
                + "\n".join(f"| {x.get('ticker')} | {', '.join(x.get('facts') or [])} | §6 `[보유 재판정] {x.get('ticker')} — "
                            f"낡은 사실 다시 확인 → 유지 / 정리(facts.py reverse)` |" for x in items) + "\n")
    return txt


def _save_track1(rid: str, st: str, mk: str, t1: dict, signal_path: str = "") -> None:
    with db.tx() as c:
        db.run_start(st, mk)
        c.execute("INSERT INTO ex_signal (run_id, track1) VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET track1=excluded.track1",
                  (rid, json.dumps(t1, ensure_ascii=False)))
    if signal_path:
        # 이행 중 — risk_guard가 아직 시그널 파일의 track1 블록을 읽는 동안만 쓴다(원본은 ex_signal.track1)
        p = Path(signal_path)
        s = json.loads(p.read_text(encoding="utf-8"))
        s["track1"] = {k: v for k, v in t1.items() if k != "line"}
        p.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ───────────────────────────── CLI ─────────────────────────────

def _kv(items) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"칸=값 꼴이어야 한다: {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def run_args(s, need=True):
        s.add_argument("--stamp", required=need, default="")
        s.add_argument("--market", required=need, default="", type=str.lower, choices=["kr", "us"] if need else None)

    s = sp.add_parser("add", help="사실 등록(같은 열쇠면 재사용)")
    run_args(s)
    s.add_argument("--batch", default="", help="JSONL: 행마다 {kind, <종류별 칸>, as_of, url, quote, tier, source, minor?}")
    s.add_argument("--kind", default="", help="한 건 등록 — 종류(" + "|".join(KINDS) + ")")
    s.add_argument("--col", action="append", default=[], help="한 건 등록 — 종류별 칸 k=v (여러 번)")
    s.add_argument("--as-of", dest="as_of", default="")
    s.add_argument("--source", default="")
    s.add_argument("--url", default="")
    s.add_argument("--quote", default="", help="원문 인용 — 출처 문장 그대로 20자 이상(T1·T2 load-bearing 새 사실은 필수)")
    s.add_argument("--tier", default=None, help="1 공식·1차 / 2 언론·집계 / 3 UGC·요약 / data = 도구·브로커 값")
    s.add_argument("--minor", action="store_true", help="load-bearing이 아니면(참고용) 인용·as-of 강제에서 빠진다")
    s.add_argument("--fact", default="", help=argparse.SUPPRESS)

    s = sp.add_parser("mention", help="재료 언급 등록")
    run_args(s)
    s.add_argument("--batch", required=True)
    s = sp.add_parser("before-news", help="뉴스 전 사실·재료 지연 → ex_run")
    run_args(s)
    s = sp.add_parser("known", help="이미 아는 사실 — 리서치 전에 연다")
    for t in ("ticker", "sector", "axis", "indicator"):
        s.add_argument(f"--{t}", default="")
    s.add_argument("--market", default="")
    s.add_argument("--since", default="14d")
    s.add_argument("--stamp", default="")
    s.add_argument("--limit", type=int, default=5)
    s = sp.add_parser("queue", help="검증 큐(JSONL)")
    run_args(s)
    s.add_argument("--batch", choices=BATCHES, default=None)
    s.add_argument("--append", action="store_true")
    s.add_argument("--out", default="")
    s = sp.add_parser("verdict", help="판정 행 등록 + 검증 상태")
    s.add_argument("--batch", required=True, help="판정 JSONL: 행마다 {fact_id|claim, verdict, tier, sub, t1, note, support, …}")
    run_args(s, need=False)
    s.add_argument("--label", choices=BATCHES, default=None, help="행에 batch가 없을 때의 묶음")
    s.add_argument("--round", type=int, default=None)
    s = sp.add_parser("lint", help="판정 기계 검사")
    run_args(s)
    s.add_argument("--batch", choices=BATCHES, required=True)
    s = sp.add_parser("history", help="사실 판 이력(판·시각·교정·바뀐 칸)")
    s.add_argument("fact_id")
    s = sp.add_parser("show", help="사실 한 판의 전부(--rev N · 없으면 지금 판)")
    s.add_argument("fact_id")
    s.add_argument("--rev", type=int, default=None)
    s = sp.add_parser("correct", help="사실 교정(UPDATE 한 번)")
    s.add_argument("fact_id")
    s.add_argument("--set", dest="sets", action="append", default=[])
    s.add_argument("--from-rev", dest="from_rev", type=int, default=None, help="판 N의 값으로 되돌린다(새 교정 · 이력은 그대로)")
    s.add_argument("--reason", required=True)
    s.add_argument("--verdict", default=None)
    run_args(s, need=False)
    s = sp.add_parser("check-refs", help="참조 검사")
    run_args(s)
    s = sp.add_parser("check", help="세 시점 대조(srcledger check와 같은 형식)")
    run_args(s)
    s.add_argument("--note", default="")
    s.add_argument("--out", default="")
    s = sp.add_parser("render-s12", help="노트 §12 표(srcledger render와 같은 형식)")
    run_args(s)
    s.add_argument("--out", default="")
    for name in ("render-claims", "render-verdict"):
        s = sp.add_parser(name)
        run_args(s)
        s.add_argument("--batch", choices=BATCHES, required=True)
        s.add_argument("--out", default="")
        if name == "render-claims":
            s.add_argument("--append", action="store_true")
            s.add_argument("--force", action="store_true")
        else:
            s.add_argument("--claims", default="", help="고정된 클레임 목록 경로(기본: 같은 이름 규칙)")
    s = sp.add_parser("decide", help="주문 근거 결정(fc_track1.decide 이식)")
    run_args(s)
    s.add_argument("--mode", choices=["pre", "post"], default="pre")
    s.add_argument("--signal", default="", help="이행 중 — 시그널 JSON에도 track1 블록을 쓴다")
    s = sp.add_parser("reuse", help="다른 run이 세운 사실")
    run_args(s)
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--brief", action="store_true")
    s = sp.add_parser("line", help="사실 한 줄")
    s.add_argument("fact_id")
    s = sp.add_parser("kinds", help="종류별 받는 칸(필수·하나 이상·선택·별칭)")
    s.add_argument("--kind", default="")
    s = sp.add_parser("render-board", help="노트 §3 섹터 보드(보드 기록 + 섹터 상태 + 재료 언급)")
    run_args(s)
    s.add_argument("--out", default="")
    s = sp.add_parser("issues", help="이번 run 재료의 발행분 번호(언급 행의 issue_no) · 링크 수")
    s.add_argument("--stamp", required=True)
    s.add_argument("--market", required=True)
    s = sp.add_parser("pending", help="감사 소급 대기 행(되돌림 매도) — 장이 열려 있으면 stops.py --send가 판다")
    s.add_argument("--market", required=True)
    s = sp.add_parser("reverse", help="보유 판단 재판정 매도(되돌림 매도 행 · 다음 risk_guard·stops가 보낸다)")
    run_args(s)
    s.add_argument("--ticker", required=True)
    s.add_argument("--qty", type=int, required=True)
    s.add_argument("--why", required=True)
    s.add_argument("--kind", choices=["hold_overturned"], default="hold_overturned")
    a = ap.parse_args(argv)

    if a.cmd == "add":
        if a.fact:
            print("자유 문장(--fact)은 받지 않는다 — 종류(--kind)와 칸(--col k=v)으로 넣는다(문장 한 칸 금지 · Phase 3)", file=sys.stderr)
            return 2
        if a.batch:
            try:
                rows = _read_rows(a.batch)
            except (OSError, ValueError) as e:
                print(f"batch를 읽지 못했다: {e}", file=sys.stderr)
                return 2
            rc, res = add_rows(rows, (a.stamp, a.market), minor=a.minor)
            nn = {s_: sum(1 for r in res if r["status"] == s_) for s_ in ("신규", "재사용", "되풀이")}
            print(f"batch 기록 {len(rows)}건 ← {a.batch} (신규 {nn['신규']} · 재사용 {nn['재사용']} · 되풀이 {nn['되풀이']} · "
                  f"거부 {len(rows) - len(res)})")
            return rc
        if not a.kind:
            ap.error("add에는 --batch <jsonl> 또는 --kind K --col k=v …가 필요하다")
        row = {"kind": a.kind, **_kv(a.col)}
        for k, v in (("as_of", a.as_of), ("source", a.source), ("url", a.url), ("quote", a.quote), ("tier", a.tier)):
            if v not in (None, ""):
                row[k] = v
        if a.minor:
            row["minor"] = True
        rc, _ = add_rows([row], (a.stamp, a.market))
        return rc
    if a.cmd == "mention":
        return add_mentions(_read_rows(a.batch), (a.stamp, a.market))
    if a.cmd == "before-news":
        r = before_news(a.stamp, a.market)
        print(f"뉴스 전 사실 {r['before_news_n']}건 / 이번 run 조사 사실 {r['research_facts']}건 · 재료 지연 평균 "
              f"{r['material_delay_h'] if r['material_delay_h'] is not None else '—'}시간(발행분 {r['issues']}개) · "
              f"신규 {r['facts_new']} · 재사용 {r['facts_reused']} · 되풀이 {r['facts_repeat']} → ex_run")
        return 0
    if a.cmd == "known":
        print(known(a.ticker or None, a.sector or None, a.axis or None, a.indicator or None, a.market or None, a.since,
                    a.stamp or None, a.limit), end="")
        return 0
    if a.cmd == "queue":
        rows, summ = queue(a.stamp, a.market, a.batch, append=a.append)
        text = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(text, encoding="utf-8")
            print(f"→ {a.out}", file=sys.stderr)
        else:
            sys.stdout.write(text)
        print(summ, file=sys.stderr)
        return 0
    if a.cmd == "verdict":
        run = (a.stamp, a.market) if a.stamp and a.market else None
        return add_verdicts(_read_rows(a.batch), run, a.label, a.round, src_file=a.batch)
    if a.cmd == "lint":
        errs, warns, cnt, total = lint(a.stamp, a.market, a.batch)
        for w in warns:
            print("  ※ " + w)
        if errs:
            print(f"LINT FAIL — 위반 {len(errs)}건 ({db.run_id(a.stamp, a.market)} · 묶음 {a.batch})")
            for e in errs:
                print("  - " + e)
            return 1
        print(lint_line(cnt, total, True))
        return 0
    if a.cmd in ("history", "show"):
        try:
            if a.cmd == "history":
                print(history(a.fact_id))
            else:
                f = db.q1("SELECT rev FROM ev_fact WHERE id=?", (a.fact_id,))
                print(show_rev(a.fact_id, a.rev if a.rev is not None else int((f or {}).get("rev") or 1)))
        except Reject as e:
            print(f"거부 — {e}", file=sys.stderr)
            return 2
        return 0
    if a.cmd == "correct":
        run = (a.stamp, a.market) if a.stamp and a.market else None
        try:
            if (a.from_rev is None) == (not a.sets):
                raise Reject("--set 또는 --from-rev 하나가 필요하다(둘 다는 안 된다)")
            sets = from_rev_sets(a.fact_id, a.from_rev) if a.from_rev is not None else _kv(a.sets)
            if a.from_rev is not None and not sets:
                print(f"{a.fact_id}: rev {a.from_rev}와 지금 값이 같다 — 바뀐 것 없음")
                return 0
            reason = a.reason if a.from_rev is None else f"rev {a.from_rev}의 값으로 되돌림 — {a.reason}"
            return correct(a.fact_id, sets, reason, a.verdict, run)
        except Reject as e:
            print(f"교정 거부 — {e}", file=sys.stderr)
            return 2
    if a.cmd == "check-refs":
        ok, line, probs = check_refs(a.stamp, a.market)
        for p in probs:
            print("  - " + p)
        print(line)
        return 0 if ok else 1
    if a.cmd == "check":
        rc, text = check(a.stamp, a.market, a.note, a.out)
        if rc == 2:
            print(text, file=sys.stderr)
            return 2
        print(text)
        if a.out:
            print(f"→ {a.out}")
        return rc
    if a.cmd == "render-s12":
        text = render_s12(a.stamp, a.market, a.out)
        print(text)
        if a.out:
            print(f"→ {a.out}")
        return 0
    if a.cmd == "render-claims":
        rc, msg = render_claims(a.stamp, a.market, a.batch, a.out, a.append, a.force)
        print(msg, file=sys.stderr if rc else sys.stdout)
        return rc
    if a.cmd == "render-verdict":
        rc, path = render_verdict(a.stamp, a.market, a.batch, a.out, a.claims)
        print(f"판정표 → {path} ({'LINT OK · REFS OK' if rc == 0 else 'LINT 또는 REFS 실패 — 파일 안 목록을 본다'})")
        return rc
    if a.cmd == "decide":
        return decide(a.stamp, a.market, a.mode, signal_path=a.signal)
    if a.cmd == "reuse":
        print(reuse(a.stamp, a.market, a.days, a.brief), end="")
        return 0
    if a.cmd == "line":
        print(render_line(a.fact_id) or f"{a.fact_id}: 없음")
        return 0
    if a.cmd == "kinds":
        print(kinds_text(a.kind), end="")
        return 0
    if a.cmd == "render-board":
        text = render_board(a.stamp, a.market)
        if text is None:
            print(f"섹터 보드 기록이 없다 — {db.run_id(a.stamp, a.market)} 세션의 보드는 1단 capture가 적재한다(--no-record면 없다)",
                  file=sys.stderr)
            return 2
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(text + "\n", encoding="utf-8")
            print(f"→ {a.out}")
        else:
            print(text)
        return 0
    if a.cmd == "reverse":
        return reverse(a.stamp, a.market, a.ticker, a.qty, a.why, a.kind)
    if a.cmd == "pending":
        print(pending_block(a.market))
        return 0
    if a.cmd == "issues":
        print(issues_block(a.stamp, a.market))
        return 0
    return 2


def issues_block(stamp: str, market: str) -> str:
    """이번 run 재료의 발행분 목록 — 번호는 재료 문서 〈뉴스레터 원문〉의 블록 순서(언급 행에 `issue_no`로 쓴다)."""
    rid = db.run_id(stamp, market)
    ids = db.material_issues_for_run(rid)
    if not ids:
        return f"(이번 run {rid} 재료에 발행분이 없다 — stage.py capture를 먼저 돌린다)"
    out = [f"| issue_no | 출처 | 주제 | 발행일 | 제목 | 링크 |", "|---|---|---|---|---|---|"]
    for n, iid in enumerate(ids, 1):
        r = db.q1("SELECT source, topic, day, subject FROM mat_issue WHERE id=?", (iid,)) or {}
        nl = db.q1("SELECT COUNT(*) AS n FROM mat_link WHERE issue_id=?", (iid,))["n"]
        out.append(f"| {n} | {r.get('source') or ''} | {r.get('topic') or ''} | {r.get('day') or ''} | "
                   f"{str(r.get('subject') or '')[:60]} | {nl} |")
    return "\n".join(out)


def pending_block(market: str) -> str:
    """감사 소급 대기 행(옛 `fc_track1.py pending` 자리) — 행이 있고 장이 열려 있으면 `stops.py --send`가 판다.
    risk_guard.reversals_pending을 거친다 — 도구 출처 없는 소급 행은 거기서 void로 닫힌다(FB-261017-9)."""
    import risk_guard as _rg
    rows = _rg.reversals_pending(market)
    head = "## 감사 소급\n\n"
    if not rows:
        return head + "(대기 중인 되돌림 매도 없음)"
    lines = [head + "직전 run의 주문 뒤 감사가 매수 근거를 뒤집었다 — **이 run의 risk_guard가 아래를 규율 매도로 자동 전송한다**"
             "(사람 확인 없음 · 보유 0이면 void로 닫힌다).\n",
             "| id | 종목 | 남은 수량 | 사유 | 판정표 행 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r.get('ticker')} {r.get('name') or ''} | {r.get('qty')} | {str(r.get('why') or '')[:120]} "
                     f"| {', '.join(r.get('fc_rows') or []) or '—'} |")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
