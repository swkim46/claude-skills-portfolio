#!/usr/bin/env python3
"""시장 지도와 일정표 — **매 run이 여기서 시작한다.**

왜 있는가: 지금까지 매 run이 지도 없이 시작했다. 축을 매번 새로 세우고, 재료에 있던 일정은
아무 데도 안 남았다. 2026-09-08 재료의 머니레터에는 9/7~9/13 경제일정 캘린더가 통째로 들어
있었는데 CPI 하나만 뽑아 쓰고 버렸다.

그리고 이 시스템은 **속도로는 못 이긴다**(§0-b). 뉴스레터로 종목 소식을 듣는 시점엔 이미
늦다. 이길 수 있는 자리는 하나뿐이다 —
  **시장 → 축 → 대표 종목 → 핵심 지표를 미리 파악해두고, 그 위에 시나리오를 미리 짜두는 것.**
그러면 뉴스는 *알려주는* 것이 아니라 *확인해주는* 것이 되고, 지연이 문제가 되지 않는다.

두 파일을 관리한다.
  `journal/market_map.json`  축 — 각 축의 핵심 지표(무엇이 이 축을 움직이나)와 대표 종목
                             (그 축에 어느 방향으로 걸리나)
  `journal/calendar.json`    날짜가 정해진 이벤트 — 지표 발표·정책 회의·실적·행사

지도는 논지(`theses.py`)의 상류다. 일정표의 이벤트마다 **양쪽 시나리오의 논지가 걸려 있는지**
점검하는 것이 `gaps` 명령의 일이다 — 다가오는 이벤트에 논지가 없으면 그날 또 즉흥으로
판단하게 된다.

사용:
    python3 market_map.py show
    python3 market_map.py calendar [--days 21]
    python3 market_map.py gaps            # 논지가 없는 임박 이벤트
    python3 market_map.py add-axis --file x.json
    python3 market_map.py add-event --file y.json
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
JOURNAL = HERE / "journal"
MAP = JOURNAL / "market_map.json"
CAL = JOURNAL / "calendar.json"

# 일정 행의 `kind` 어휘 — `add-event`가 검증한다. 이벤트 계수(`limits.position_sizing`)는 지표·정책만 본다.
EVENT_KINDS = ("지표", "정책", "실적", "제품", "지수", "휴장", "메모", "기타")
EVENT_KINDS_HELP = ("지표=CPI·PPI·고용 등 공식 통계 발표 / 정책=FOMC·ECB·정부 정책 회의·발표 / 실적=실적 발표 / "
                    "제품=제품·행사 / 지수=편입·리밸런싱 / 휴장=거래소 휴장 / "
                    "메모=뉴스레터 휴간 등 시장 사건이 아닌 안내(계수 없음) / 기타")
THESES = JOURNAL / "theses.json"
SECTOR_HISTORY = JOURNAL / "sector_history.jsonl"
BRIDGE = HERE / "config" / "sector_bridge.json"
BRIDGE_LOG = JOURNAL / "bridge_log.jsonl"
SECTOR_WATCH = JOURNAL / "sector_watch.jsonl"
KST = timezone(timedelta(hours=9))

# 섹터 등급. **"—"(미조사)가 등급인 것이 핵심이다** — 안 본 것을 빈칸이 아니라 등급으로
# 남겨야 노트가 자기가 뭘 안 봤는지 안다. 2026-09-08 노트는 14개 섹터 중 2개만 다루고도
# 그 사실을 몰랐는데, 안 본 섹터를 적을 자리가 없었기 때문이다.
GRADES = ("A", "B", "C", "D", "—")
GRADE_DESC = {
    "A": "주목 — 노트 §4에 심층 서술",
    "B": "관찰 — 논지 대기",
    "C": "보류 — resolves_when 필수",
    "D": "제외 — 왜 아닌지 적을 것",
    "—": "미조사 — 순환 슬롯 대기열",
}
CARRY_LIMIT = 3     # 같은 줄이 이만큼 연속 이월되면 등급을 한 칸 내린다(복붙 유령 방지)

# 다른 시장 회고에서 **입력으로 칠 만큼 움직였다**고 볼 최소 초과수익(%p).
# 지수를 따라 움직인 것은 그 섹터 고유의 신호가 아니라 시장 전체이므로 접는다.
BRIDGE_MIN_EXCESS_PP = 0.5
# 20거래일을 확보하려면 달력으로 이만큼이 필요하다(주말·공휴일 여유 포함).
BOARD_HISTORY_DAYS = 45


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise SystemExit(f"{path.name}을 읽을 수 없다 ({e})")


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 고리의 종료 상태. **"미확인"은 종료가 아니다** — 조사를 안 한 것과 구분이 안 되므로
# 루프를 계속 돌린다. 종료는 셋 중 하나다: 확인 · 반증 · 조사불가(사유와 해소조건을 적은 것).
STATUS_MARK = {"확인": "✓", "반증": "✗", "조사불가": "⊘", "미확인": "?", "진행중": "…"}
OPEN_STATES = ("미확인", "진행중")          # 아직 끝나지 않은 것
CLOSED_STATES = ("확인", "반증", "조사불가")  # 종료된 것


def weakest_link(a: dict):
    """연쇄에서 가장 약한 고리. **다음에 무엇을 조사할지가 여기서 정해진다.**

    연쇄는 가장 약한 고리만큼만 강하다. 앞 고리가 확인됐어도 뒤가 미확인이면 그 논지는
    아직 종목까지 내려오지 못한 것이고, 거기가 리서치 대상이다.
    """
    for link in a.get("chain") or []:
        if link.get("status") in OPEN_STATES:
            return link
    return None


def show_chain(a: dict) -> None:
    """거시 사건에서 종목까지의 인과 사슬. 각 고리가 확인됐는지 표시한다."""
    print(f"   연쇄: {a.get('origin', '')}")
    for link in a.get("chain") or []:
        mark = STATUS_MARK.get(link.get("status", "미확인"), "?")
        print(f"     {mark} {link.get('step', '?')}. {link['claim']}")
        if link.get("current"):
            print(f"        현재: {link['current']}")
        if link.get("indicator"):
            print(f"        지표: {link['indicator']}")
    w = weakest_link(a)
    if w:
        print(f"   ★ 가장 약한 고리 — {w.get('step')}. {w['claim']}")
        print(f"      여기가 다음 리서치 대상이다. 이 고리가 확인돼야 종목까지 내려온다.")
    for link in a.get("chain") or []:
        if link.get("status") == "조사불가" and not link.get("resolves_when"):
            print(f"   ⚠ {link.get('step')}번이 '조사불가'인데 **해소 조건이 없다** — "
                  f"그건 결론이 아니라 포기다. 무엇이 나오면 알 수 있는지 적을 것.")
    if a.get("breaks_if"):
        print(f"   무효 조건: {a['breaks_if']}")


def show() -> int:
    m = _read(MAP, {"axes": []})
    if not m["axes"]:
        print("시장 지도가 비어 있다 — /life-research로 축을 세우는 것이 먼저다.")
        return 0
    for a in m["axes"]:
        kind = "연쇄" if a.get("chain") else "축"
        print(f"\n■ [{kind}] {a['id']}  {a['name']}")
        print(f"   {a.get('thesis', '')}")
        if a.get("chain"):
            show_chain(a)
        if a.get("indicators"):
            print("   지표 — 무엇이 이 축을 움직이나")
            for i in a["indicators"]:
                nxt = f" · 다음 {i['next']}" if i.get("next") else ""
                cur = f" · 현재 {i['current']}" if i.get("current") else ""
                print(f"     · {i['name']} ({i.get('source', '?')}, {i.get('freq', '?')})"
                      f"{cur}{nxt}")
                if i.get("threshold"):
                    print(f"       임계: {i['threshold']}")
        if a.get("names"):
            print("   대표 종목 — 이 축에 어느 방향으로 걸리나")
            for n in a["names"]:
                inuni = "" if n.get("in_universe") else "  (유니버스 밖)"
                print(f"     {n.get('sign', '?'):2} {n['ticker']:8} {n.get('name', ''):14} "
                      f"{n.get('why', '')}{inuni}")
    return 0


def chains() -> int:
    """연쇄만 추려 **가장 약한 고리**를 모아 본다 — 리서치 대기열이 여기서 나온다."""
    m = _read(MAP, {"axes": []})
    cs = [a for a in m["axes"] if a.get("chain")]
    if not cs:
        print("등록된 연쇄가 없다. 거시 사건 → 파급 → 섹터 → 종목 순으로 세우는 것이 먼저다.")
        return 0
    print("연쇄 현황 — 각 논지가 종목까지 얼마나 내려왔는가\n")
    for a in cs:
        links = a.get("chain") or []
        done = sum(1 for l in links if l.get("status") == "확인")
        print(f"■ {a['name']}  ({done}/{len(links)} 고리 확인)")
        w = weakest_link(a)
        broken = [l for l in links if l.get("status") == "반증"]
        held = [l for l in links if l.get("status") == "조사불가"]
        if w:
            print(f"   ★ 막힌 곳: {w.get('step')}. {w['claim']}  [{w.get('status')}]")
            print(f"      → **이번 run에서 조사한다.** 끝나면 상태가 셋 중 하나여야 한다:")
            print(f"         확인 / 반증 / 조사불가(사유·해소조건·해소예정일을 적은 것)")
            print(f"      '미확인'으로 남기고 넘어가면 조사를 안 한 것과 구분되지 않는다.")
        elif broken:
            # 반증은 '끝났다'는 뜻이지 '통과했다'는 뜻이 아니다. 끊긴 연쇄를 완성으로
            # 표시하면 죽은 논지를 매매 가능으로 읽게 된다.
            b = broken[0]
            print(f"   ✗ **끊긴 연쇄** — {b.get('step')}번에서 반증됐다: {b['claim']}")
            print(f"      이 연쇄로는 종목까지 내려오지 않는다. 매매 근거로 쓰지 말 것.")
            print(f"      (같은 종목이 다른 연쇄로 살아 있을 수는 있다 — 그건 다른 논지다.)")
        elif held:
            h = held[0]
            print(f"   ⊘ **보류** — {h.get('step')}번은 지금 알 수 없다: {h['claim']}")
            print(f"      해소: {h.get('resolves_when', '해소 조건 미기재 — 적을 것')}")
        else:
            names = [n for n in (a.get("names") or []) if not n.get("in_universe")]
            print("   ✓ 전 고리 확인 — 종목까지 내려왔다. "
                  + (f"유니버스 밖 후보: {', '.join(n['ticker'] for n in names)}" if names
                     else "유니버스 편입 완료"))
        print()
    return 0


def upcoming(days: int) -> list:
    cal = _read(CAL, {"events": []})
    today = datetime.now(KST).date()
    out = []
    for e in cal["events"]:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        delta = (d - today).days
        if 0 <= delta <= days:
            out.append((delta, d, e))
    return sorted(out, key=lambda x: x[0])


def calendar(days: int) -> int:
    rows = upcoming(days)
    if not rows:
        print(f"앞으로 {days}일 안에 등록된 이벤트가 없다.")
        return 0
    print(f"다가오는 일정 ({days}일)\n")
    print(f"  {'D-':>4} {'날짜':11} {'축':16} 이벤트")
    for delta, d, e in rows:
        print(f"  {('D-' + str(delta)) if delta else 'D-DAY':>4} {e['date']:11} "
              f"{e.get('axis', ''):16} {e['event']}")
        if e.get("why"):
            print(f"       └ {e['why']}")
    return 0


def gaps(days: int) -> int:
    """임박한 이벤트에 **연쇄와 논지가 걸려 있는지**. 여기가 비면 그날 즉흥으로 판단하게 된다.

    두 층을 따로 본다 — 예정된 이벤트는 날짜를 미리 아니 **둘 다** 미리 만들 수 있다.
      · 연쇄: 이 이벤트가 어디로 파급되는가(거시 → 섹터 → 종목)
      · 논지: 그래서 무엇을 어떤 조건에 사고팔 것인가
    연쇄만 있고 논지가 없으면 "파급은 그렸는데 살 것을 안 정한" 상태이고,
    논지만 있고 연쇄가 없으면 "이유 없이 조건만 건" 상태다.
    """
    th = _read(THESES, {"theses": []})
    live = [t for t in th["theses"] if t.get("status") in ("armed", "held")]
    blob = json.dumps(live, ensure_ascii=False)
    m = _read(MAP, {"axes": []})
    chain_keys = {a.get("event_key") for a in m["axes"] if a.get("event_key")}

    rows = upcoming(days)
    holes = []
    for delta, d, e in rows:
        key = e.get("key") or e["event"]
        has_thesis = key in blob or e["date"] in blob or e.get("event", "") in blob
        has_chain = key in chain_keys
        if not (has_thesis and has_chain):
            holes.append((delta, e, has_chain, has_thesis))
    print(f"공백 점검 — 앞으로 {days}일\n")
    if not holes:
        print("  다가오는 이벤트에 연쇄와 논지가 모두 걸려 있다.")
    for delta, e, has_chain, has_thesis in holes:
        miss = []
        if not has_chain:
            miss.append("연쇄 없음(파급 경로 미작성)")
        if not has_thesis:
            miss.append("논지 없음(살 조건 미작성)")
        print(f"  ★ D-{delta} {e['date']} [{e.get('axis', '')}] {e['event']}")
        print(f"       {' · '.join(miss)} — 이대로면 그날 즉흥으로 판단하게 된다.")
        if e.get("why"):
            print(f"       {e['why']}")
    print(f"\n  살아있는 논지 {len(live)}건. **양쪽 시나리오**가 다 걸려 있는지도 확인할 것 —")
    print("  한쪽만 걸면 반대 결과가 나왔을 때 또 아무것도 안 하게 된다.")
    return 0


def gaps_check(days: int, note_path: str = "", out: str = "") -> int:
    """★ 일정 → 논지 **강제**. D-`days` 안 이벤트 중 유니버스 종목이 걸려 있는데 논지도 없고
    노트 §5에 `[통과] <이벤트> — <이유>` 줄도 없으면 종료코드 3.

    왜 — 오라클 실적(9/10)은 9/8부터 일정표에 있었고 ORCL은 유니버스에 있었다. `gaps`가
    띄웠지만 아무도 막지 않았고 9/10 미국 노트에는 오라클이 한 줄도 없었다. **알고 있었는데
    안 한 것**은 몰랐던 것보다 나쁘다 — 그래서 경고가 아니라 게이트다. 산출은 생성줄이다.
    """
    th = _read(THESES, {"theses": []})
    live = [x for x in th["theses"] if x.get("status") in ("armed", "held")]
    live_tk = {x.get("ticker") for x in live}
    blob = json.dumps(live, ensure_ascii=False)
    wl = _read(HERE / "config" / "watchlist.json", {})
    uni = {r["ticker"]: r for mk in ("KR", "US") for r in (wl.get(mk) or []) if r.get("ticker")}
    alias = {}
    for tk, r in uni.items():
        for f in [r.get("name", "")] + list(r.get("aliases") or []):
            if len(str(f)) >= 2:
                alias[str(f)] = tk
    note = ""
    if note_path and Path(note_path).exists():
        note = Path(note_path).read_text(encoding="utf-8", errors="ignore")
    unresolved, passed = [], []
    for delta, d, e in upcoming(days):
        text = f"{e.get('event', '')} {e.get('why', '')} {e.get('key', '')}"
        tks = {tk for f, tk in alias.items() if f in text} | \
              {tk for tk in uni if tk in text}
        if not tks:
            continue                                   # 유니버스 종목이 안 걸린 이벤트는 여기 몫이 아니다
        has_thesis = bool(tks & live_tk) or (e.get("key") or "") in blob
        waived = bool(re.search(r"\[통과\][^\n]*" + re.escape(e.get("event", "")[:12]), note))
        if has_thesis or waived:
            passed.append((delta, e, sorted(tks), "논지 있음" if has_thesis else "[통과] 명시"))
        else:
            unresolved.append((delta, e, sorted(tks)))
    line = f"공백 미해결 **{len(unresolved)}건** · 유니버스 걸린 이벤트 {len(unresolved) + len(passed)}건 (D-{days})"
    buf = [f"## 일정 → 논지 점검 — D-{days}", "", line, ""]
    for delta, e, tks in unresolved:
        buf.append(f"- ★ D-{delta} {e['date']} **{e['event']}** — 유니버스 {', '.join(tks)}: "
                   f"논지 없음, §5 `[통과]` 없음 → **논지를 세우거나 노트 §5에 "
                   f"`[통과] {e['event']} — <이유>`를 적어라**")
    for delta, e, tks, how in passed:
        buf.append(f"- ✓ D-{delta} {e['date']} {e['event']} — {', '.join(tks)} ({how})")
    s = "\n".join(buf) + "\n"
    print(s)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(s, encoding="utf-8")
        print(f"→ {out}")
    return 3 if unresolved else 0


def add(path: Path, kind: str) -> int:
    items = _read(path, None)
    if items is None:
        raise SystemExit(f"파일을 읽을 수 없다: {path}")
    items = items if isinstance(items, list) else [items]
    if kind == "axis":
        m = _read(MAP, {"schema_version": "1.0", "axes": []})
        have = {a["id"] for a in m["axes"]}
        for a in items:
            for k in ("id", "name", "thesis"):
                if not a.get(k):
                    raise SystemExit(f"축에 {k}가 없다: {a.get('id', '?')}")
            m["axes"] = [x for x in m["axes"] if x["id"] != a["id"]]   # 같은 id는 갱신
            a["updated"] = datetime.now(KST).strftime("%Y-%m-%d")
            m["axes"].append(a)
            print(f"{'갱신' if a['id'] in have else '등록'}: 축 {a['id']} {a['name']}")
        _write(MAP, m)
    else:
        c = _read(CAL, {"schema_version": "1.0", "events": []})
        # ★ kind는 어휘 안에서만 — `risk_guard.halve_window`가 `지표`·`정책`에 이벤트 계수를 붙이므로,
        #   메모성 행("뉴스레터 휴간")이 `지표`로 들어오면 목표 비중이 0.75배가 된다(2026-09-21 UPPITY 행).
        #   전건 검증 뒤에 쓴다 — 하나라도 어휘 밖이면 아무것도 기록하지 않는다.
        bad = [e for e in items if e.get("kind") not in EVENT_KINDS]
        if bad:
            for e in bad:
                print(f"kind가 어휘 밖이다: {e.get('kind')!r} ({e.get('date')} {e.get('event')})", file=sys.stderr)
            print("허용 kind: " + " · ".join(EVENT_KINDS) + "\n  " + EVENT_KINDS_HELP, file=sys.stderr)
            return 2
        for e in items:
            for k in ("date", "event"):
                if not e.get(k):
                    raise SystemExit(f"이벤트에 {k}가 없다: {e}")
            datetime.strptime(e["date"], "%Y-%m-%d")     # 형식 검증
            dup = [x for x in c["events"]
                   if x["date"] == e["date"] and x["event"] == e["event"]]
            if dup:
                print(f"이미 있음: {e['date']} {e['event']}")
                continue
            e["added"] = datetime.now(KST).strftime("%Y-%m-%d")
            c["events"].append(e)
            print(f"등록: {e['date']} {e['event']}")
        c["events"].sort(key=lambda x: x["date"])
        _write(CAL, c)
    return 0


# ------------------------------------------------------------------ 섹터 보드

# ---------------------------------------------------------------- 축 조회 (조인 키)

def axes_index() -> tuple:
    """`(ticker → [axis_id]) , (axis_id → 축 dict)`.

    ★ 축이 두 시장을 잇는 **유일한 자연 조인 키**다. `axes[].names[]`는 이미
    양 시장 종목을 한 축에 담고 있다(`us_rates` 축에 국내 보험주가 들어 있다).
    """
    m = _read(MAP, {"axes": [], "sectors": []})
    by_tk, by_id = {}, {}
    for a in (m.get("axes") or []):
        aid = a.get("id")
        if not aid:
            continue
        by_id[aid] = a
        for n in (a.get("names") or []):
            tk = str(n.get("ticker") or "").strip()
            if tk:
                by_tk.setdefault(tk, []).append(aid)
    return by_tk, by_id


def axis_of(ticker: str) -> list:
    """그 종목이 걸린 축 id 목록(없으면 빈 리스트)."""
    return axes_index()[0].get(str(ticker).strip(), [])


def names_of(axis_id: str, market: str = "") -> list:
    """축 하나에서 **양 시장 종목**을 꺼낸다. `market`을 주면 그쪽만."""
    a = axes_index()[1].get(axis_id) or {}
    rows = list(a.get("names") or [])
    if market:
        mk = market.upper()
        rows = [n for n in rows if _market_of(n.get("ticker")) == mk]
    return rows


def _market_of(ticker: str) -> str:
    """티커 표기로 시장을 가른다 — 국내는 6자리 숫자, 그 밖은 미국."""
    tk = str(ticker or "").strip()
    return "KR" if (len(tk) == 6 and tk.isdigit()) else "US"


def label_of(axis_id: str) -> str:
    a = axes_index()[1].get(axis_id) or {}
    return a.get("label") or a.get("name") or axis_id


def match_axis(label: str) -> str:
    """산문 축 라벨 → 축 id. **못 찾으면 빈 문자열**(추측하지 않는다)."""
    lab = (label or "").strip()
    if not lab:
        return ""
    _, by_id = axes_index()
    if lab in by_id:
        return lab
    for aid, a in by_id.items():
        if (a.get("label") or a.get("name") or "").strip() == lab:
            return aid
    return ""


def cmd_axis(a) -> int:
    """축 조회 — 조인 키가 실제로 양 시장을 잇는지 눈으로 확인하는 자리."""
    if a.ticker:
        hit = axis_of(a.ticker)
        print(f"{a.ticker} → " + (" · ".join(f"{x}({label_of(x)})" for x in hit) or "걸린 축 없음"))
        return 0
    if a.axis:
        rows = names_of(a.axis, a.market)
        if not rows:
            print(f"축 '{a.axis}'에 종목이 없다(또는 축이 없다).")
            return 1
        print(f"{a.axis} — {label_of(a.axis)}")
        for n in rows:
            mk = _market_of(n.get("ticker"))
            print(f"  [{mk}] {str(n.get('ticker')):8} {n.get('name', ''):16} "
                  f"{n.get('sign', '+')}  {n.get('why', '')[:50]}")
        return 0
    _, by_id = axes_index()
    for aid, ax in by_id.items():
        ns = ax.get("names") or []
        kr = sum(1 for n in ns if _market_of(n.get("ticker")) == "KR")
        us = len(ns) - kr
        print(f"  {aid:20} {label_of(aid):16} KR {kr} · US {us}"
              + ("   ← 한쪽 시장뿐" if (kr == 0 or us == 0) and ns else ""))
    return 0


# ------------------------------------------------------------------ 전망 (축 = 전망 객체)
#
# ★ 왜 축을 전망으로 격상하는가 — 지도는 **이미 일어난 것의 검증된 목록**이었다
#   (고리 55개: 확인 39 · 반증 11 · 조사불가 5). 방향·기간·기대치·수혜 순서·반영 여부·채점이
#   없으니 "4~12주 뒤 돈이 어디로 가는가"를 담을 곳이 없었고, 그래서 논지 8건 전부가
#   **그 종목이 재료에 처음 뜬 날 또는 그 뒤에** 세워졌다(뉴스보다 먼저 세운 논지 0/8).
#   별도 원장을 만들면 축과 드리프트하므로 축 자체에 필드를 더한다.

FORECAST_FIELDS = ("direction", "expected", "horizon_weeks", "confidence", "status", "opened")
FORECAST_STATUSES = ("open", "hit", "miss", "expired")
MATERIAL_DIR = HERE / "data"


def _first_mention(ticker: str, name: str = "") -> str:
    """그 종목이 **재료에 처음 등장한 날**(YYMMDD). 없으면 빈 문자열.

    선행 판정의 기준이다 — 전망을 세운 날이 이 날보다 앞이면 뉴스보다 먼저 본 것이다.
    """
    tk = (ticker or "").strip(); nm = (name or "").strip()
    for f in sorted(MATERIAL_DIR.glob("material_2*_*.md")):
        m = re.search(r"material_(\d{6})_", f.name)
        if not m:
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if (tk and tk in txt) or (len(nm) >= 2 and nm in txt):
            return m.group(1)
    return ""


def created_before_news(ax: dict) -> tuple:
    """전망이 수혜 종목의 첫 재료 등장보다 앞섰는가 — **기계로** 잰다. 반환 (bool|None, 근거).

    None = 잴 수 없다(수혜 종목이 없거나 opened가 없다). 추측하지 않는다.
    """
    opened = (ax.get("opened") or "")[2:].replace("-", "")
    bens = ax.get("beneficiaries") or []
    if not opened or not bens:
        return None, "opened 또는 beneficiaries 없음"
    # ★ 선행은 **2차·3차 수혜**로 잰다. 1차는 뉴스가 이미 말한 것이라 재료에 있는 게 당연하다 —
    #   그것까지 대조하면 모든 전망이 '뉴스 후'가 된다(실측: 9/8 전망이 1차 두산퓨얼셀 때문에
    #   뉴스 후로 찍혔다. 2차 두산에너빌리티·한전기술은 9/9에야 재료에 떴는데도).
    #   2차·3차가 없는 전망은 앞설 것이 없으므로 1차로 재되 그 사실을 적는다.
    tail = [b for b in bens if b.get("order") in (2, 3)]
    scope = "2차·3차" if tail else "1차뿐"
    firsts = [(b.get("ticker"), _first_mention(b.get("ticker"), b.get("name"))) for b in (tail or bens)]
    seen = [f for _, f in firsts if f]
    if not seen:
        return True, f"{scope} 수혜가 재료에 한 번도 안 나왔다 — 전망이 앞선다"
    earliest = min(seen)
    ok = opened < earliest
    return ok, (f"전망 {opened} vs {scope} 재료 첫 등장 {earliest}"
                + ("" if ok else " — 뉴스가 먼저였다"))


def forecast_validate(ax: dict) -> list:
    """전망 필드 검증. 반환 문제 목록(비어 있으면 통과)."""
    p = []
    if ax.get("direction") not in ("+", "-"):
        p.append("direction은 '+' 또는 '-'")
    if not ax.get("expected"):
        p.append("expected(기대치 — 벤치마크 대비 얼마)가 없다")
    hw = ax.get("horizon_weeks")
    if not isinstance(hw, (int, float)) or not (1 <= hw <= 52):
        p.append("horizon_weeks는 1~52")
    c = ax.get("confidence")
    if not isinstance(c, (int, float)) or not (0 < c <= 1):
        p.append("confidence는 0~1")
    if ax.get("status", "open") not in FORECAST_STATUSES:
        p.append(f"status는 {FORECAST_STATUSES}")
    bens = ax.get("beneficiaries")
    if not isinstance(bens, list) or not bens:
        p.append("beneficiaries가 없다 — 수혜 종목 없는 전망은 살 수 없는 전망이다")
    else:
        orders = {b.get("order") for b in bens}
        if not orders & {2, 3}:
            p.append("2차·3차 수혜가 없다 — 1차는 뉴스가 이미 말한 것이다. 없으면 왜 없는지 "
                     "`no_secondary_why`에 적어라") if not ax.get("no_secondary_why") else None
        for b in bens:
            if not b.get("ticker") or b.get("order") not in (1, 2, 3):
                p.append(f"수혜 행에 ticker·order(1|2|3)가 있어야 한다: {b}")
    return [x for x in p if x]


def cmd_forecast(a) -> int:
    m = _read(MAP, {"axes": []})
    if a.sub == "list":
        rows = m["axes"]
        if a.open:
            rows = [x for x in rows if (x.get("status") or "open") == "open" and x.get("direction")]
        n_blank = 0
        print(f"■ 전망 원장 — {len(rows)}건" + (" (열린 전망만)" if a.open else "") + "\n")
        for ax in rows:
            if not ax.get("direction"):
                n_blank += 1
                print(f"  ○ {ax['id']:28} **전망 미기입** — 방향·기간·수혜 순서를 채워야 전망이다")
                continue
            cbn, why = created_before_news(ax)
            mark = {True: "선행", False: "뉴스 후", None: "?"}[cbn]
            bens = ax.get("beneficiaries") or []
            unp = [b for b in bens if b.get("priced") is False]
            print(f"  {'☐' if (ax.get('status') or 'open') == 'open' else '☑'} {ax['id']:28} "
                  f"{ax['direction']} {ax.get('horizon_weeks')}주 conf {ax.get('confidence')} "
                  f"[{mark}] 수혜 {len(bens)} · 미반영 {len(unp)}")
            print(f"     {ax.get('name', '')[:60]} — {ax.get('expected', '')[:50]}")
            for b in bens:
                pr = {True: "반영", False: "**미반영**", None: "미측정"}.get(b.get("priced"), "미측정")
                print(f"       {b.get('order')}차 [{_market_of(b.get('ticker'))}] {b.get('ticker'):8} "
                      f"{(b.get('name') or '')[:12]:12} {pr:8} {(b.get('why') or '')[:40]}")
        if n_blank:
            print(f"\n  ★ 전망 미기입 축 {n_blank}개 — 지도에는 있지만 앞을 보는 그림은 없다. "
                  f"다음 3단이 `forecast add`로 채운다.")
        return 0

    if a.sub == "add":
        items = _read(Path(a.file), None)
        if items is None:
            raise SystemExit(f"파일을 읽을 수 없다: {a.file}")
        items = items if isinstance(items, list) else [items]
        by_id = {x["id"]: x for x in m["axes"]}
        for it in items:
            base = dict(by_id.get(it.get("id"), {}))
            old_bens = {b.get("ticker"): dict(b) for b in (base.get("beneficiaries") or []) if b.get("ticker")}
            base.update(it)
            # ★ 수혜 표는 **병합**한다 — 통째로 덮으면 `priced`로 잰 값(반영 여부·근거·측정일)이 재실행
            #   한 번에 사라진다(실측 2026-09-15). 들어온 행에 priced 계열이 없으면 기존 값을 지키고,
            #   들어온 JSON에 없는 기존 수혜 종목은 남긴다(빼려면 --replace-beneficiaries).
            if not getattr(a, "replace_beneficiaries", False) and old_bens:
                merged, seen = [], set()
                for b in (base.get("beneficiaries") or []):
                    tk = b.get("ticker")
                    if tk in old_bens:
                        keep = {k: v for k, v in old_bens[tk].items()
                                if k in ("priced", "priced_basis", "priced_at", "in_universe") and k not in b}
                        b = {**old_bens[tk], **b, **keep}
                    merged.append(b); seen.add(tk)
                merged += [v for k, v in old_bens.items() if k not in seen]
                base["beneficiaries"] = merged
            base.setdefault("status", "open")
            base.setdefault("opened", datetime.now(KST).strftime("%Y-%m-%d"))
            for k in ("name", "thesis"):
                if not base.get(k):
                    raise SystemExit(f"축에 {k}가 없다: {it.get('id', '?')}")
            probs = forecast_validate(base)
            if probs:
                raise SystemExit(f"전망 {it.get('id')} 검증 실패:\n  - " + "\n  - ".join(probs))
            for b in base["beneficiaries"]:
                b.setdefault("priced", None)
                b.setdefault("market", _market_of(b.get("ticker")))
                b.setdefault("in_universe", None)
            base["updated"] = datetime.now(KST).strftime("%Y-%m-%d")
            cbn, why = created_before_news(base)
            base["created_before_news"] = cbn
            base["created_before_news_why"] = why
            m["axes"] = [x for x in m["axes"] if x["id"] != base["id"]] + [base]
            print(f"{'갱신' if it.get('id') in by_id else '등록'}: 전망 {base['id']} "
                  f"{base['direction']} {base['horizon_weeks']}주 · "
                  f"{'선행' if cbn else ('뉴스 후' if cbn is False else '?')} ({why})")
        _write(MAP, m)
        return 0

    if a.sub == "judge":
        hit = [x for x in m["axes"] if x["id"] == a.axis]
        if not hit:
            raise SystemExit(f"그런 축이 없다: {a.axis}")
        ax = hit[0]
        if a.status not in FORECAST_STATUSES:
            raise SystemExit(f"status는 {FORECAST_STATUSES}")
        ax["status"] = a.status
        sc = ax.setdefault("score", {})
        sc["judged"] = datetime.now(KST).strftime("%Y-%m-%d")
        if a.excess is not None:
            sc["excess_pct"] = a.excess
        sc["note"] = a.note or sc.get("note", "")
        _write(MAP, m)
        print(f"{a.axis}: {a.status} · 초과 {a.excess if a.excess is not None else '미측정'} — {a.note}")
        return 0

    if a.sub == "score":
        return forecast_score(m, a.days, a.out)
    raise SystemExit("forecast list|add|judge|score")


def forecast_score(m: dict, days: int, out: str = "") -> int:
    """★ 성공 지표 ④ — 전망 선행률·적중률·초과수익·차수별.

    이것이 없으면 "앞을 본다"가 검증 불가능한 주장으로 남는다. 설계 근거 조사
    (비용 차감 후 초과수익 근거 없음)와 사용자의 목표(예측 성능) 중 어느 쪽이 맞는지는
    이 숫자가 가른다. 초과수익이 ≤0이면 첫 줄에 그렇게 쓴다 — 감추지 않는다.
    """
    import io as _io
    since = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    fc = [x for x in m["axes"] if x.get("direction") and (x.get("opened") or "") >= since]
    buf = _io.StringIO()
    print(f"## 전망 채점 — 최근 {days}일 세운 전망 {len(fc)}건\n", file=buf)
    if not fc:
        print("*(채점할 전망이 없다 — 3단이 `forecast add`로 세우기 전이다)*\n", file=buf)
        print("[전망] 세움 0 · 선행 0 · 판정 0 · 적중 0", file=buf)
        s = buf.getvalue(); print(s)
        if out:
            Path(out).write_text(s, encoding="utf-8")
        return 0
    ahead = [x for x in fc if created_before_news(x)[0] is True]
    judged = [x for x in fc if x.get("status") in ("hit", "miss")]
    hits = [x for x in judged if x["status"] == "hit"]
    ahead_judged = [x for x in ahead if x.get("status") in ("hit", "miss")]
    exc = [x["score"]["excess_pct"] for x in ahead_judged
           if isinstance((x.get("score") or {}).get("excess_pct"), (int, float))]
    by_order = {}
    for x in fc:
        for b in x.get("beneficiaries") or []:
            o = b.get("order")
            by_order.setdefault(o, {"n": 0, "unpriced": 0})
            by_order[o]["n"] += 1
            by_order[o]["unpriced"] += 1 if b.get("priced") is False else 0
    mean_exc = (sum(exc) / len(exc)) if exc else None
    # 기계 판독 줄 — 게이트·주간 리포트가 읽는다. 문구를 바꾸면 그쪽도 같이 본다.
    print(f"[전망] 세움 {len(fc)} · 선행 {len(ahead)} · 판정 {len(judged)} · 적중 {len(hits)}"
          + (f" · 선행 초과 {mean_exc:+.1f}%p(n={len(exc)})" if mean_exc is not None else ""), file=buf)
    if mean_exc is not None and mean_exc <= 0:
        print(f"\n**★ 선행 전망의 초과수익이 {mean_exc:+.1f}%p로 0 이하다.** "
              f"설계 근거 조사(비용 차감 후 초과수익 없음)가 지금까지는 맞다. 이 줄을 감추지 않는다.\n", file=buf)
    print(f"\n| 지표 | 값 |\n|---|---|", file=buf)
    print(f"| 선행률 (뉴스보다 먼저 세운 전망) | {len(ahead)}/{len(fc)}"
          f"{f' = {100*len(ahead)/len(fc):.0f}%' if fc else ''} |", file=buf)
    print(f"| 적중률 (판정된 것 중 hit) | {len(hits)}/{len(judged)}"
          f"{f' = {100*len(hits)/len(judged):.0f}%' if judged else ' (판정 전)'} |", file=buf)
    print(f"| 선행 전망 초과수익 (등중량, 벤치마크 대비) | "
          f"{f'{mean_exc:+.1f}%p (n={len(exc)})' if mean_exc is not None else '미측정 — horizon 도달 전'} |", file=buf)
    print(f"\n| 차수 | 수혜 종목 | 미반영 |\n|---|---|---|", file=buf)
    for o in sorted(k for k in by_order if k):
        print(f"| {o}차 | {by_order[o]['n']} | {by_order[o]['unpriced']} |", file=buf)
    print("\n뉴스보다 먼저 세운 전망:", file=buf)
    for x in ahead:
        print(f"- {x['id']} — {x.get('name', '')[:50]} ({created_before_news(x)[1]})", file=buf)
    if not ahead:
        print("- **없음** — 전부 뉴스가 먼저였다. 이 시스템은 아직 앞을 보고 있지 않다.", file=buf)
    s = buf.getvalue(); print(s)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(s, encoding="utf-8")
        print(f"→ {out}")
    return 0


def priced_measure(axis_id: str, days: int = 20) -> dict:
    """수혜 종목의 **20일 상대수익**을 재서 `priced`를 채운다. 반환 {ticker: {...}}.

    "아직 안 움직인 것"이 감이 아니라 숫자가 되게 한다. 기준 둘 — 벤치마크 대비 ·
    축 선두(수혜 중 20일 수익 최대) 대비. 둘 다 +5%p 넘게 앞서 있으면 반영(True),
    선두보다 10%p 넘게 뒤져 있으면 미반영(False), 그 사이는 None(미측정 아님 — '경계').
    """
    from kis_client import KisClient, KisError
    m = _read(MAP, {"axes": []})
    hit = [x for x in m["axes"] if x["id"] == axis_id]
    if not hit:
        raise SystemExit(f"그런 축이 없다: {axis_id}")
    ax = hit[0]
    bens = ax.get("beneficiaries") or []
    if not bens:
        raise SystemExit(f"{axis_id}에 수혜 종목이 없다")
    wl = _read(HERE / "config" / "watchlist.json", {})
    bench = (wl.get("benchmarks") or {})
    c = KisClient(svr="paper")
    end = datetime.now(KST)
    start = (end - timedelta(days=days * 2 + 10))

    def ret(ticker, market, excd=""):
        try:
            if market == "KR":
                rows = c.domestic_daily(ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            else:
                # 거래소 코드가 없으면 NAS→NYS→AMS 순으로 시도한다 — VST·BE(NYSE)·SPY(AMEX)가
                # NAS 고정으로 "일봉 0행"이 나 미국 수혜 전부가 측정 실패로 찍혔다(2026-09-15 실측).
                tries = [excd] if excd else ["NAS", "NYS", "AMS"]
                rows = []
                for ex in tries:
                    rows = [r for r in (c.overseas_daily(ticker, ex) or []) if r.get("close")]
                    if len(rows) >= days + 1:
                        break
        except KisError as e:
            return None, str(e)
        rows = [r for r in rows if r.get("close")]
        if len(rows) < days + 1:
            return None, f"일봉 {len(rows)}행뿐"
        return (rows[0]["close"] / rows[days]["close"] - 1) * 100, ""

    out, errs = {}, {}
    for b in bens:
        mk = b.get("market") or _market_of(b.get("ticker"))
        r, e = ret(b["ticker"], mk, b.get("excd", ""))
        if r is None:
            errs[b["ticker"]] = e
        else:
            out[b["ticker"]] = {"ret20": r, "market": mk}
    bmk = {}
    for mk in {v["market"] for v in out.values()}:
        bt = bench.get(mk) or {}
        r, e = ret(bt.get("ticker", ""), mk, bt.get("excd", "")) if bt.get("ticker") else (None, "벤치마크 없음")
        bmk[mk] = r
    lead = max((v["ret20"] for v in out.values()), default=None)
    for tk, v in out.items():
        b_r = bmk.get(v["market"])
        v["vs_bench"] = (v["ret20"] - b_r) if b_r is not None else None
        v["vs_lead"] = (v["ret20"] - lead) if lead is not None else None
        if v["vs_bench"] is not None and v["vs_bench"] > 5 and (v["vs_lead"] or 0) > -5:
            v["priced"] = True
        elif v["vs_lead"] is not None and v["vs_lead"] < -10:
            v["priced"] = False
        else:
            v["priced"] = None
        v["basis"] = (f"20일 {v['ret20']:+.1f}% · 벤치 대비 "
                      f"{v['vs_bench']:+.1f}%p · 선두 대비 {v['vs_lead']:+.1f}%p"
                      if v["vs_bench"] is not None and v["vs_lead"] is not None
                      else f"20일 {v['ret20']:+.1f}%")
    # 축에 기록한다 — 측정일과 함께. 잰 것만 덮고 못 잰 것은 그대로 둔다.
    today = datetime.now(KST).strftime("%Y-%m-%d")
    for b in bens:
        v = out.get(b["ticker"])
        if v:
            b["priced"] = v["priced"]; b["priced_basis"] = v["basis"]; b["priced_at"] = today
        elif b["ticker"] in errs:
            b["priced_basis"] = f"측정 실패 {today}: {errs[b['ticker']]}"
    _write(MAP, m)
    return {"measured": out, "errors": errs, "bench": bmk, "lead": lead}


def cmd_priced(a) -> int:
    res = priced_measure(a.axis, a.days)
    print(f"■ {a.axis} 수혜 종목 반영 여부 — {a.days}일 상대수익 (벤치마크 {res['bench']})\n")
    for tk, v in sorted(res["measured"].items(), key=lambda kv: -kv[1]["ret20"]):
        pr = {True: "반영", False: "**미반영**", None: "경계"}[v["priced"]]
        print(f"  {tk:8} [{v['market']}] {pr:8} {v['basis']}")
    for tk, e in res["errors"].items():
        print(f"  {tk:8} 측정 실패 — {e}")
    print("\n  미반영 = 축 선두보다 10%p 넘게 뒤져 있다 → 2차·3차 수혜 후보. "
          "반영 = 벤치마크·선두 모두 앞서 있다 → 이미 값에 들어갔다.")
    return 0


def _pct(cur: float, prev: float) -> float:
    return (cur - prev) / prev * 100 if prev else 0.0


def _lookbacks(rows: list) -> dict:
    """일별(최신순)에서 1·5·20거래일 등락. 데이터가 짧으면 그 칸은 None."""
    out = {}
    for h in (1, 5, 20):
        out[f"d{h}"] = _pct(rows[0]["close"], rows[h]["close"]) if len(rows) > h else None
    return out


def _sector_state(m: dict) -> dict:
    """market_map.json의 sectors를 코드로 색인. 없으면 빈 dict.

    ★ `carried_runs`를 **여기서 파생해 채운다.** 이 필드는 읽는 곳이 둘(보드·`cycle`)인데
    **쓰는 곳이 0곳**이라, "3run 이상 이월이면 등급 강등"이 구조적으로 발동할 수 없었다.
    쓰기를 추가하면 또 잊히므로(그게 이 필드가 죽은 이유다) **관찰 이력에서 센다** —
    그 섹터를 본 기록(`sector_watch.jsonl`) 중 등급 갱신일 이후의 건수가 곧 이월 횟수다.
    """
    idx = {s["code"]: s for s in m.get("sectors", []) if s.get("code")}
    watch = _watch_history()
    for code, s in idx.items():
        upd = s.get("updated") or ""
        n = sum(1 for r in watch
                if code in (r.get("sectors") or []) and str(r.get("date") or "") > upd)
        s["carried_runs"] = n
    return idx


def _watch_history() -> list:
    """`sector_watch.jsonl` 전건(시장 무관). 못 읽는 줄은 건너뛰되 조용히 삼키지 않는다."""
    out, bad = [], 0
    if not SECTOR_WATCH.exists():
        return out
    for line in SECTOR_WATCH.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    if bad:
        print(f"  ※ {SECTOR_WATCH.name}에서 {bad}줄을 못 읽었다 — 이월 횟수가 실제보다 "
              f"적게 셀 수 있다.", file=sys.stderr)
    return out


def history_since(code: str, since: str) -> str:
    """`sector_history.jsonl`에서 **그 관찰 시점 이후** 그 섹터 지수의 변화를 잰다.

    왜 필요한가: `cycle` ①은 실시간 보드의 d5/d20을 보여주는데, 그건 *오늘로부터*
    5·20거래일 전 기준이라 **관찰한 날로부터의 성과가 아니다.** "9/9에 A를 준 섹터가
    그 뒤 어떻게 됐나"를 정직하게 답하려면 관찰일에 기록된 값과 비교해야 한다.
    그 기록이 `sector_history.jsonl`이고, **적재만 하고 읽는 코드가 없어서 죽어 있었다.**

    파일은 날짜별로 중첩돼 있고(`{date, sectors:[{key, close, ...}]}`), 레벨(`close`)이
    있는 기록끼리만 비교한다 — `--quick` 보드는 레벨이 없으므로 그 날은 건너뛴다.
    """
    if not SECTOR_HISTORY.exists():
        return ""
    first = last = None
    for line in SECTOR_HISTORY.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("date") or "") < str(since):
            continue
        for s in rec.get("sectors") or []:
            if str(s.get("key")) != str(code):
                continue
            if not isinstance(s.get("close"), (int, float)) or not s["close"]:
                continue
            pair = (rec["date"], s["close"])
            if first is None:
                first = pair
            last = pair
    if not first or not last or first[0] == last[0]:
        return ""            # 비교할 두 시점이 없다 — 모른다고 말하는 편이 낫다
    return (f" · 관찰일({first[0]}) 대비 "
            f"{(last[1] - first[1]) / abs(first[1]) * 100:+.2f}%")


# ------------------------------------------------------------------ 섹터 보드

def _fmt(v, width=7, suffix="%"):
    return f"{'—':>{width}}" if v is None else f"{v:>+{width}.2f}{suffix}"


def board(market: str, record: bool, quick: bool) -> int:
    """§3 섹터 보드 — **전 업종을 재서** 한 표로 낸다.

    뉴스 언급 수로 섹터를 고르면 뉴스가 안 쓴 곳은 영원히 안 보인다. 여기서는 국면을
    **재고**, 안 본 섹터도 '미조사' 행으로 남긴다.
    """
    from kis_client import KisClient, KisError      # 지도 조회만 할 때 토큰을 받지 않도록 지연 임포트

    m = _read(MAP, {"axes": []})
    state = _sector_state(m)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    c = KisClient(svr="paper")

    try:
        rows, bench_name, bench = _fetch_board(c, market, quick)
    except KisError as e:
        print(f"섹터 보드 조회 실패: {e}", file=sys.stderr)
        return 2

    print(f"■ 섹터 보드 — {'국내(KOSPI)' if market == 'kr' else '미국(GICS 대리지표)'}  {today}")
    print(f"  기준 {bench_name} {bench:+.2f}%   ('대비' 열 = 이 기준 대비 초과수익)\n")
    head = (f"  {'섹터':<14}{'오늘':>8}{'대비':>8}{'5일':>8}{'20일':>8}  "
            f"{'등급':<3}{'갱신':<11}{'연쇄':<5}상태")
    print(head)
    print("  " + "─" * (len(head) - 2))

    seen, unstudied, stale = [], [], []
    for r in rows:
        st = state.get(r["key"], {})
        # `.get(k, 기본값)`은 키가 **있고 값이 None**이면 None을 그대로 준다 — 시딩이
        # updated/status를 명시적 null로 넣으므로 여기서 걸린다(2026-09-09 실제로 터졌다).
        # 빈 값의 기본값은 전부 `or`로 잡는다.
        grade = st.get("grade") or "—"
        upd = st.get("updated") or "—"
        nchain = len(st.get("chains") or [])
        status = st.get("status") or "**미조사**"
        seen.append(r["key"])
        if grade == "—":
            unstudied.append(r["name"])
        carried = st.get("carried_runs") or 0
        if carried >= CARRY_LIMIT:
            stale.append(f"{r['name']}({carried}run 이월)")
        # 시세를 못 받은 행은 값이 아니라 **사유**를 보여준다 — 빈 칸만 보이면
        # "움직임이 없었다"로 읽히고, 그건 실패를 데이터로 바꾸는 것이다.
        if r.get("error"):
            status = f"★ 미수신 — {r['error']}"
        print(f"  {r['name']:<14}{_fmt(r['d1'])}{_fmt(r['d1'] - bench if r['d1'] is not None else None)}"
              f"{_fmt(r['d5'])}{_fmt(r['d20'])}  {grade:<3}{upd:<11}{nchain or '—':<5}{status}")

    known = [k for k in state if k in seen]
    studied = [k for k in known if state[k].get("grade", "—") != "—"]
    print(f"\n  커버리지 {len(studied)}/{len(rows)} — 미조사 {len(unstudied)}개")
    if unstudied:
        print("  미조사: " + ", ".join(unstudied))
    if stale:
        print(f"  ★ {CARRY_LIMIT}run 이상 이월(등급 강등 대상): " + ", ".join(stale))
    orphan = [k for k in state if k not in seen]
    if orphan:
        print(f"  ⚠ 로스터에 없는 sectors 항목 {len(orphan)}개: {', '.join(orphan)}"
              " — 거래소 분류가 바뀌었거나 오타다.")

    if record:
        _record_board(rows, market, bench, today)
        print(f"\n  → {SECTOR_HISTORY.name}에 {len(rows)}행 적재")
    return 0


def _fetch_board(c, market: str, quick: bool) -> tuple:
    """(행 목록, 기준지수 이름, 기준지수 등락). 행은 등락률 내림차순."""
    from kis_client import KR_SECTOR_CODES

    if market == "kr":
        raw = c.domestic_sector_board()
        bench = next((x["change_pct"] for x in raw if x["code"] == "0001"), 0.0)
        end = datetime.now(KST)
        start = end - timedelta(days=BOARD_HISTORY_DAYS)
        rows = []
        for s in raw:
            if s["code"] not in KR_SECTOR_CODES:
                continue
            r = {"key": s["code"], "name": s["name"], "d1": s["change_pct"],
                 "d5": None, "d20": None, "turnover": s["turnover_100m"]}
            if not quick:
                hist = c.domestic_index_daily(s["code"], start.strftime("%Y%m%d"),
                                              end.strftime("%Y%m%d"))
                r.update(_lookbacks(hist))
                r["d1"] = s["change_pct"]         # 장중이면 스냅샷이 더 최신이다
                # ★ 지수 **레벨**도 남긴다. d1/d5/d20은 오늘 기준 변화율이라
                #   "관찰한 날로부터 얼마나 갔나"를 계산할 수 없다(레벨이 있어야 된다).
                r["close"] = hist[0].get("close") if hist else None
            rows.append(r)
        rows.sort(key=lambda x: x["d1"], reverse=True)
        return rows, "KOSPI 종합", bench

    br = _read(BRIDGE, {})
    secs = br.get("us_sectors") or {}
    if not secs:
        raise SystemExit(f"{BRIDGE.name}에 us_sectors가 없다 — 미국 보드를 만들 수 없다.")
    rows = []
    for tic, meta in secs.items():
        # ★ 못 받은 섹터를 `continue`로 버리면 **행이 사라져 보드가 전수가 아니게 되고,
        #   그 사실이 어디에도 안 남는다** — 1단 원칙("누락을 침묵이 아니라 행으로")의
        #   정면 위반이고, 커버리지 분모가 줄어 오히려 좋아 보인다.
        #   행은 남기고 값을 None으로 둔다.
        try:
            hist = c.overseas_daily(tic, excd=meta.get("excd", "AMS"))
        except Exception as e:                                # noqa: BLE001
            hist, err = [], f"{type(e).__name__}: {str(e)[:80]}"
        else:
            err = "" if len(hist) >= 2 else f"이력 {len(hist)}일치뿐 — 등락을 계산할 수 없다"
        if err:
            rows.append({"key": tic, "name": f"{meta['name']}({tic})", "d1": None,
                         "d5": None, "d20": None, "turnover": None,
                         "close": None, "error": err})
            continue
        lb = _lookbacks(hist)
        rows.append({"key": tic, "name": f"{meta['name']}({tic})", **lb,
                     "turnover": None, "close": hist[0].get("close") if hist else None})
    spy = c.overseas_daily("SPY", excd="AMS")
    bench = _pct(spy[0]["close"], spy[1]["close"]) if len(spy) > 1 else 0.0
    rows.sort(key=lambda x: (x["d1"] is None, -(x["d1"] or 0)))
    return rows, "S&P500(SPY)", bench


def _upsert_sectors(rows: list, market: str, day: str) -> int:
    """보드에서 본 섹터를 `market_map.json:sectors`에 **자리로 남긴다.**

    ★ 왜 — `sectors`에 국내 코드(4자리 숫자)만 있어서 **미국 등급은 담을 곳이 없었고**,
    `board --market us`가 매 run `0/15`로 리셋됐다. 판단은 매번 새로 했는데 어디에도
    쌓이지 않은 것이다. 코드로 색인하는 구조는 그대로 두고 **데이터 자리만 연다.**

    등급은 여기서 만들지 않는다 — 그건 판단이고 `grade` 명령이 쓴다. 여기서는
    '이 섹터를 언제 봤는가'만 남긴다(그게 `carried_runs` 계산의 재료다).
    """
    m = _read(MAP, {"axes": [], "sectors": []})
    idx = {s.get("code"): s for s in m.setdefault("sectors", []) if s.get("code")}
    n_new = 0
    for r in rows:
        code = r.get("key")
        if not code:
            continue
        s = idx.get(code)
        if s is None:
            s = {"code": code, "name": r.get("name", code), "market": market.upper(),
                 "grade": "—", "status": None, "chains": [], "names": [],
                 "next_event": None, "resolves_when": None, "updated": None}
            m["sectors"].append(s)
            idx[code] = s
            n_new += 1
        s.setdefault("market", market.upper())
        s["last_seen"] = day
    _write(MAP, m)
    return n_new


def cmd_grade(a) -> int:
    """섹터 등급을 기록한다 — **시장 무관**(국내 코드도 미국 ETF 코드도 같은 자리).

    등급이 파일에 남지 않으면 다음 run의 보드가 `—`로 시작하고, 그러면 매 run이
    같은 섹터를 처음부터 다시 판단한다. 그것이 지금까지 미국에서 일어난 일이다.
    """
    m = _read(MAP, {"axes": [], "sectors": []})
    idx = {s.get("code"): s for s in m.setdefault("sectors", []) if s.get("code")}
    s = idx.get(a.code)
    if s is None:
        s = {"code": a.code, "name": a.name or a.code, "market": (a.market or "").upper(),
             "chains": [], "names": [], "next_event": None, "resolves_when": None}
        m["sectors"].append(s)
    if a.grade not in ("A", "B", "C", "—"):
        raise SystemExit("등급은 A·B·C 또는 —")
    s["grade"] = a.grade
    s["updated"] = datetime.now(KST).strftime("%Y-%m-%d")
    if a.note:
        s["status"] = a.note
    if a.research:
        s.setdefault("research", [])
        if a.research not in s["research"]:
            s["research"].append(a.research)
    if a.name:
        s["name"] = a.name
    _write(MAP, m)
    print(f"{a.code} {s.get('name')} [{s.get('market') or '?'}] 등급 {a.grade} 기록"
          + (f" · 리서치 {a.research}" if a.research else ""))
    # 짝 섹터를 알려준다 — 한쪽 등급이 반대편 판단의 입력이다.
    for L in (_read(BRIDGE, {}) or {}).get("links", []):
        if a.code in (L.get("us"), L.get("kr")):
            other = L["kr"] if a.code == L.get("us") else L["us"]
            o = idx.get(other) or {}
            print(f"  ↔ 짝 {other} {L.get('kr_name') or ''} "
                  f"(부호 {L.get('sign')} · 신뢰 {L.get('confidence')}) "
                  f"현재 등급 {o.get('grade') or '—'}")
    return 0


def paired_grades(codes: list) -> list:
    """그 섹터들의 **반대편 짝 섹터 등급·리서치**를 돌려준다.

    `sector_bridge.json:links` 24개는 지금까지 하루치 대응 매핑에만 쓰였고, 등급·리서치를
    건너보내지 않았다 — 미국 XLE가 A여도 국내 화학이 순환 큐에서 우선순위를 못 얻었다.
    """
    m = _read(MAP, {"sectors": []})
    idx = {s.get("code"): s for s in m.get("sectors", []) if s.get("code")}
    out = []
    for L in (_read(BRIDGE, {}) or {}).get("links", []):
        for here, there in ((L.get("us"), L.get("kr")), (L.get("kr"), L.get("us"))):
            if here in codes and there:
                o = idx.get(there) or {}
                if (o.get("grade") or "—") != "—":
                    out.append({"code": here, "pair": there,
                                "pair_name": o.get("name") or L.get("kr_name") or there,
                                "grade": o.get("grade"), "sign": L.get("sign"),
                                "confidence": L.get("confidence"),
                                "research": o.get("research") or []})
    return out


def _record_board(rows: list, market: str, bench: float, day: str) -> None:
    """추세를 계산하려면 이력이 있어야 한다. 한 줄 = 하루치 스냅샷.

    ★ 이력(jsonl)과 **상태(sectors)**는 다르다 — 전자는 시세, 후자는 판단이 앉는 자리다.
    자리가 없으면 판단이 갈 곳이 없어 매 run 증발한다. 그래서 여기서 같이 연다.
    """
    n_new = _upsert_sectors(rows, market, day)
    if n_new:
        print(f"  [지도] {market.upper()} 섹터 {n_new}개를 sectors에 새로 열었다 — "
              f"이제 등급이 run을 넘어 남는다(`market_map.py grade`로 기록).")
    SECTOR_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    rec = {"date": day, "market": market, "benchmark_pct": bench,
           "sectors": [{"key": r["key"], "name": r["name"], "d1": r["d1"],
                        "d5": r["d5"], "d20": r["d20"],
                        "close": r.get("close")} for r in rows]}
    # ★ (날짜, 시장)당 한 행 — 재실행이면 **덮어쓴다**(멱등). 2026-09-22: 캡처 3회 재실행으로 같은 날 행이 둘 쌓여
    #   추세 계산이 같은 날을 두 번 셌다(09-10 kr·09-15 us도 같은 부산물).
    kept = []
    if SECTOR_HISTORY.exists():
        for ln in SECTOR_HISTORY.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if (r.get("date"), r.get("market")) == (day, market):
                continue
            kept.append(ln)
    kept.append(json.dumps(rec, ensure_ascii=False))
    SECTOR_HISTORY.write_text("\n".join(kept) + "\n", encoding="utf-8")


def bridge(closed: str, record: bool = False, score: bool = False) -> int:
    """§1-B 대응 매핑 — **방금 닫힌 시장에서 움직인 것이 이쪽 무엇에 걸리는가.**

    이건 채점이 아니라 **오늘 준비의 첫 입력**이다. 짝은 config/sector_bridge.json에
    미리 고정돼 있다 — 매번 즉흥으로 지으면 그날 서사에 맞는 짝만 고르게 된다.
    """
    from kis_client import KisClient, KisError

    br = _read(BRIDGE, {})
    links = br.get("links") or []
    if not links:
        print(f"{BRIDGE.name}에 links가 없다.", file=sys.stderr)
        return 2
    prep = "kr" if closed == "us" else "us"
    c = KisClient(svr="paper")
    try:
        closed_rows, cb_name, cb = _fetch_board(c, closed, quick=True)
    except KisError as e:
        print(f"{closed} 보드 조회 실패: {e}", file=sys.stderr)
        return 2

    by_key = {r["key"]: r for r in closed_rows}
    print(f"■ {closed.upper()} 회고 → {prep.upper()} 준비 입력   (기준 {cb_name} {cb:+.2f}%)\n")
    print(f"  {'움직인 쪽':<22}{'등락':>8}{'초과':>8}  →  {'걸리는 쪽':<16}{'부호':<5}신뢰")
    print("  " + "─" * 78)

    src_field, dst_field = ("us", "kr") if closed == "us" else ("kr", "us")
    shown, preds = 0, []
    for ln in sorted(links, key=lambda x: -abs((by_key.get(x[src_field], {}).get("d1") or 0))):
        src = by_key.get(ln[src_field])
        if not src or src["d1"] is None:
            continue
        # 움직이지 않은 것은 입력이 아니다 — 초과수익이 문턱 미만이면 접는다.
        excess = src["d1"] - cb
        if abs(excess) < BRIDGE_MIN_EXCESS_PP:
            continue
        dst_name = ln.get("kr_name") if dst_field == "kr" else \
            (br["us_sectors"].get(ln["us"], {}).get("name", ln["us"]))
        print(f"  {src['name']:<22}{src['d1']:>+7.2f}%{excess:>+7.2f}p  →  "
              f"{dst_name:<16}{ln['sign']:<5}{ln.get('confidence', '?')}")
        if ln.get("note"):
            print(f"      {ln['note']}")
        shown += 1
        preds.append({"src": ln[src_field], "src_name": src["name"], "src_d1": src["d1"],
                      "src_excess": excess, "dst": ln[dst_field], "dst_name": dst_name,
                      "sign": ln["sign"], "confidence": ln.get("confidence")})

    if not shown:
        print(f"  초과수익 {BRIDGE_MIN_EXCESS_PP}%p를 넘긴 섹터가 없다 — "
              "그쪽 시장이 이쪽에 줄 입력이 없다.")
    for gap in br.get("kr_without_us" if prep == "kr" else "us_without_kr", []):
        who = gap.get("kr_name") or gap.get("us")
        print(f"\n  ⊘ 대응 없음: {who} — {gap['why']}")
    print("\n  ※ 이 표는 가설이다. 3회 역행한 짝은 sector_bridge.json에서 내린다.")

    if score:
        _score_bridge(c, closed, prep, by_key)
    if record:
        _record_bridge(closed, prep, preds)
        print(f"  → {BRIDGE_LOG.name}에 예측 {len(preds)}건 기록 (다음 run이 채점한다)")
    return 0


def _record_bridge(closed: str, prep: str, preds: list) -> None:
    BRIDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"date": datetime.now(KST).strftime("%Y-%m-%d"), "closed": closed,
           "prep": prep, "preds": preds}
    with BRIDGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _score_bridge(c, closed: str, prep: str, by_key: dict) -> None:
    """직전 예측을 오늘 실적으로 채점한다.

    ★ 왜 두 기준인가 — 초과수익만으로 채점하면 **양쪽 시장이 같은 방향으로 크게 움직인 날**
    거의 전부가 역행으로 찍힌다. 지수가 -1.6%인 날엔 방어 업종이 모두 초과수익 플러스라,
    '미국이 내렸으니 국내도 내릴 것'이라는 예측이 맞았는데도 초과 기준으로는 틀린 것이 된다.
    *실사례(2026-09-10): 그렇게 해서 역행 6건이 나왔고, 그 수치는 대응표의 품질이 아니라
    채점 방식을 재고 있었다.*
    그래서 **절대 등락 방향**과 **초과수익 방향**을 따로 보고, 둘이 엇갈리면 `혼재`로 빼둔다 —
    엇갈린 날은 대응표가 틀린 게 아니라 시장 전체가 움직인 것이다.
    """
    if not BRIDGE_LOG.exists():
        print("\n  (채점할 직전 예측이 없다 — 이번 run부터 --record로 쌓인다)")
        return
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prev = None
    for line in BRIDGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("prep") == prep and r.get("date") != today:
            prev = r                      # 같은 방향의 가장 최근 예측
    if not prev or not prev.get("preds"):
        print("\n  (같은 방향의 직전 예측이 없다)")
        return
    try:
        rows, _, bench = _fetch_board(c, prep, quick=True)
    except Exception as e:                                    # noqa: BLE001
        print(f"\n  채점용 {prep} 보드 조회 실패: {e}")
        return
    act = {r["key"]: r for r in rows}

    print(f"\n■ 직전 예측 채점 ({prev['date']} → {today})")
    print(f"  {'예측':<34}{'절대':>10}{'초과':>10}  판정")
    print("  " + "─" * 66)
    hit = miss = mixed = 0
    for p in prev["preds"]:
        a = act.get(p["dst"])
        if not a or a.get("d1") is None:
            continue
        want_up = (p["src_d1"] > 0) if p["sign"] == "+" else (p["src_d1"] < 0)
        abs_ok = (a["d1"] > 0) == want_up
        exc_ok = ((a["d1"] - bench) > 0) == want_up
        if abs_ok and exc_ok:
            verdict, hit = "적중", hit + 1
        elif not abs_ok and not exc_ok:
            verdict, miss = "역행", miss + 1
        else:
            verdict, mixed = "혼재(제외)", mixed + 1
        print(f"  {p['src_name'][:14]:<14}→ {p['dst_name'][:14]:<16}"
              f"{a['d1']:>+9.2f}%{a['d1'] - bench:>+9.2f}p  {verdict}")
    total = hit + miss
    rate = f"{hit / total * 100:.0f}%" if total else "—"
    print(f"\n  적중 {hit} / 역행 {miss} / 혼재 {mixed}  (적중률 {rate}, 혼재는 분모 제외)")
    print("  혼재 = 절대방향과 초과수익이 엇갈린 것. 시장 전체가 움직인 날의 잡음이라 세지 않는다.")


def cycle(prep: str, note: str = "", sectors: str = "") -> int:
    """순환 슬롯 — **어제 본 것과 이어서** 오늘 무엇을 볼지 정한다.

    ★ 왜 이력이 필요한가 — 순환 슬롯을 매 run 독립적으로 고르면 같은 섹터를 반복해서 보거나,
    한 번 보고 결론 없이 흘려보낸 섹터가 영원히 안 돌아온다. 그러면 '한 바퀴 돈다'가 말뿐이다.
    그래서 **무엇을 언제 봤고 그때 무슨 결론을 냈는지**를 쌓고, 다음 run은 그 위에서 고른다.

    출력 셋:
      ① 지난 관찰의 사후 — 그때 A/B를 준 섹터가 그 뒤 어떻게 됐나(근거 채점의 재료)
      ② 미해결 이월 — 결론을 못 낸 채 넘어온 것(최우선 후보)
      ③ 다음 후보 — 갱신일이 가장 오래된 순(미조사 우선)
    """
    from kis_client import KisClient, KisError

    m = _read(MAP, {"axes": [], "sectors": []})
    state = _sector_state(m)
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if sectors:                       # 이번 run의 관찰을 기록한다
        # ★ 코드는 이 시장 로스터에 있는 것만 받는다 — 2026-09-16 KR run이 `0003,0026,0027`을 적어
        #   (0003·0027은 집계 지수, 0026은 다른 업종) 이력이 오염됐고 손으로 고쳤다. 틀린 코드는
        #   기록을 거부하고 코드↔이름 표를 보여준다.
        want = [x.strip() for x in sectors.split(",") if x.strip()]
        here = {c: st for c, st in state.items()
                if (st.get("market") or ("KR" if str(c).isdigit() else "US")) == prep.upper()}
        bad = [c for c in want if c not in here]
        if bad:
            print(f"★ 기록 거부 — 이 시장({prep.upper()}) 로스터에 없는 섹터 코드: {', '.join(bad)}",
                  file=sys.stderr)
            print("  쓸 수 있는 코드 (코드 · 이름 · 등급):", file=sys.stderr)
            for c, st in sorted(here.items()):
                print(f"    {c:6} {st.get('name', ''):<16} {st.get('grade') or '—'}", file=sys.stderr)
            return 2
        SECTOR_WATCH.parent.mkdir(parents=True, exist_ok=True)
        rec = {"date": today, "market": prep, "note": note, "sectors": want}
        with SECTOR_WATCH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"기록: {rec['sectors']} → {SECTOR_WATCH.name}")

    hist = []
    if SECTOR_WATCH.exists():
        for line in SECTOR_WATCH.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("market") == prep:
                hist.append(r)

    print(f"\n■ 순환 슬롯 — {prep.upper()} · 관찰 이력 {len(hist)}건\n")

    # ① 지난 관찰의 사후
    past = [r for r in hist if r.get("date") != today]
    if past:
        try:
            rows, _, bench = _fetch_board(c := KisClient(svr="paper"), prep, quick=False)
            act = {r["key"]: r for r in rows}
        except (KisError, SystemExit) as e:
            act, bench = {}, 0.0
            print(f"  (사후 조회 실패: {e})")
        print("  ① 지난 관찰이 그 뒤 어떻게 됐나")
        seen = set()
        for r in reversed(past[-5:]):
            for code in r["sectors"]:
                if code in seen:
                    continue
                seen.add(code)
                a = act.get(code)
                nm = (state.get(code) or {}).get("name", code)
                if a and a.get("d5") is not None:
                    # 실시간 d5/d20은 *오늘 기준*이다. 관찰일 기준 성과는 이력에서 잰다.
                    print(f"     {r['date']} {nm:<14} 이후 5일 {a['d5']:>+6.2f}% · "
                          f"20일 {a['d20']:>+6.2f}%{history_since(code, r['date'])}")
                else:
                    print(f"     {r['date']} {nm:<14} (등락 미확인)")
        print()

    # ② 미해결 이월 — 봤는데 등급이 아직 미조사(—)거나 C(보류)인 것
    stuck = [(code, st) for code, st in state.items()
             if code in {c for r in hist for c in r["sectors"]}
             and (st.get("grade") or "—") in ("—", "C")]
    if stuck:
        print("  ② **미해결 이월** — 봤는데 결론이 안 난 것 (최우선)")
        for code, st in stuck:
            print(f"     {st.get('name', code):<14} 등급 {st.get('grade') or '—'} · "
                  f"갱신 {st.get('updated') or '없음'} · {st.get('resolves_when') or '해소조건 없음'}")
        print()

    # ③ 다음 후보 — 오래된 순 + **반대편 짝이 이미 등급을 받았으면 앞으로 당긴다.**
    # ★ 짝 등급은 공짜로 얻은 신호다 — 저쪽에서 이미 조사가 끝난 섹터를 이쪽에서
    # 늦게 보면, 남이 먼저 읽은 뉴스를 뒤늦게 읽는 것과 같은 일이 섹터 단위로 벌어진다.
    # 후보는 **이 시장 섹터만** — 짝 등급은 우선순위 신호이지 후보 목록이 아니다.
    here = {c: s for c, s in state.items()
            if (s.get("market") or ("KR" if str(c).isdigit() else "US")) == prep.upper()}
    pair_by_code = {pg["code"]: pg for pg in paired_grades(list(here))}
    PRIO = {"A": 0, "B": 1, "C": 2}

    def _rank(kv):
        code, st = kv
        pg = pair_by_code.get(code)
        return (PRIO.get((pg or {}).get("grade"), 9),
                st.get("updated") or "0000-00-00")

    cand = sorted(here.items(), key=_rank)
    print("  ③ 다음 후보 (짝 등급 우선 → 갱신일 오래된 순 · 미조사 우선)")
    for code, st in cand[:6]:
        seen_when = st.get("updated") or "**한 번도 안 봄**"
        print(f"     {st.get('name', code):<14} 등급 {st.get('grade') or '—'} · 마지막 {seen_when}")
        for pg in paired_grades([code]):
            # ★ 반대편 짝 섹터가 이미 등급을 받았으면 그것이 오늘 우선순위의 근거다.
            print(f"        ↔ {pg['pair']} {pg['pair_name']} 등급 **{pg['grade']}** "
                  f"(부호 {pg['sign']} · 신뢰 {pg['confidence']})"
                  + (f" · 리서치 {len(pg['research'])}건" if pg["research"] else ""))
    print("\n  이번 run에서 무엇을 봤는지 --sectors 로 기록해야 다음 run이 이어받는다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="시장 지도와 일정표")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("chains")
    c = sub.add_parser("calendar"); c.add_argument("--days", type=int, default=21)
    g = sub.add_parser("gaps"); g.add_argument("--days", type=int, default=14)
    g.add_argument("--check", action="store_true",
                   help="유니버스 종목이 걸린 D-N 이벤트에 논지가 없으면 종료코드 3(게이트용)")
    g.add_argument("--note", default="", help="§5 [통과] 줄을 볼 분석노트")
    g.add_argument("--out", default="", help="생성줄 파일(게이트가 읽는다)")
    aa = sub.add_parser("add-axis"); aa.add_argument("--file", required=True)
    ae = sub.add_parser("add-event"); ae.add_argument("--file", required=True)
    b = sub.add_parser("board", help="섹터 보드 — 전 업종을 재서 한 표로")
    b.add_argument("--market", choices=["kr", "us"], default="kr")
    b.add_argument("--quick", action="store_true", help="당일 등락만(5·20일 생략, 호출 절약)")
    b.add_argument("--record", action="store_true", help="journal/sector_history.jsonl에 적재")
    br = sub.add_parser("bridge", help="다른 시장 회고 → 이 시장 준비 입력")
    br.add_argument("--closed", choices=["kr", "us"], required=True,
                    help="방금 닫힌 시장(회고 대상)")
    br.add_argument("--record", action="store_true", help="예측을 기록해 다음 run이 채점하게 한다")
    br.add_argument("--score", action="store_true", help="직전 예측을 오늘 실적으로 채점한다")
    cy = sub.add_parser("cycle", help="순환 슬롯 — 어제 본 것과 이어서 오늘 볼 것을 고른다")
    cy.add_argument("--market", choices=["kr", "us"], default="kr")
    cy.add_argument("--sectors", default="", help="이번 run에서 본 섹터 코드 (쉼표 구분)")
    cy.add_argument("--note", default="", help="그때 무슨 결론을 냈는지 한 줄")
    gr = sub.add_parser("grade", help="섹터 등급을 기록 — 시장 무관, run을 넘어 남는다")
    gr.add_argument("--code", required=True, help="국내 4자리 코드 또는 미국 ETF 코드")
    gr.add_argument("--grade", required=True, choices=["A", "B", "C", "—"])
    gr.add_argument("--market", default="", choices=["", "kr", "us"])
    gr.add_argument("--name", default="")
    gr.add_argument("--note", default="", help="상태 한 줄")
    gr.add_argument("--research", default="", help="리서치 파일 경로")
    fc = sub.add_parser("forecast", help="전망 원장 — 축을 방향·기간·수혜 순서로")
    fcs = fc.add_subparsers(dest="sub", required=True)
    fl = fcs.add_parser("list"); fl.add_argument("--open", action="store_true")
    fa = fcs.add_parser("add"); fa.add_argument("--file", required=True)
    fa.add_argument("--replace-beneficiaries", action="store_true",
                    help="수혜 표를 병합하지 않고 파일의 것으로 교체한다(priced가 지워진다)")
    fj = fcs.add_parser("judge"); fj.add_argument("--axis", required=True)
    fj.add_argument("--status", required=True, choices=list(FORECAST_STATUSES))
    fj.add_argument("--excess", type=float, default=None, help="벤치마크 대비 %p")
    fj.add_argument("--note", default="")
    fs = fcs.add_parser("score"); fs.add_argument("--days", type=int, default=90)
    fs.add_argument("--out", default="")
    pr = sub.add_parser("priced", help="수혜 종목이 이미 값에 들어갔는지 — 20일 상대수익")
    pr.add_argument("--axis", required=True); pr.add_argument("--days", type=int, default=20)
    ax = sub.add_parser("axis", help="축 조회 — 축 하나로 양 시장 종목을 꺼낸다")
    ax.add_argument("--axis", default="", help="축 id (예: ai_memory)")
    ax.add_argument("--ticker", default="", help="이 종목이 걸린 축을 찾는다")
    ax.add_argument("--market", choices=["KR", "US"], default="", help="그 시장만")
    args = ap.parse_args()

    if args.cmd == "axis":
        return cmd_axis(args)
    if args.cmd == "forecast":
        return cmd_forecast(args)
    if args.cmd == "priced":
        return cmd_priced(args)
    if args.cmd == "grade":
        return cmd_grade(args)
    if args.cmd == "show":
        return show()
    if args.cmd == "chains":
        return chains()
    if args.cmd == "calendar":
        return calendar(args.days)
    if args.cmd == "gaps":
        return gaps_check(args.days, args.note, args.out) if args.check else gaps(args.days)
    if args.cmd == "board":
        return board(args.market, args.record, args.quick)
    if args.cmd == "cycle":
        return cycle(args.market, args.note, args.sectors)
    if args.cmd == "bridge":
        return bridge(args.closed, args.record, args.score)
    p = Path(args.file)
    if not p.exists():
        print(f"파일 없음: {p}", file=sys.stderr)
        return 2
    return add(p, "axis" if args.cmd == "add-axis" else "event")


if __name__ == "__main__":
    sys.exit(main())
