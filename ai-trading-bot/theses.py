#!/usr/bin/env python3
"""논지 원장 — 매수·매도 조건을 **미리** 정해두고, 발화하면 집행한다.

왜 이 파일이 있는가. 지금까지 매 run은 "오늘 살까?"로 시작했다. 시간 압박 속에서 확신을
새로 만들어야 하는 가장 어려운 질문이라, 가장 미루기 쉬웠다(2026-09-08 첫 실 run이
그렇게 `no_trade`로 끝났고, 그날 벤치마크는 +2.45%였다). 그래서 질문을 둘로 쪼갠다.

  ① **논지를 세울 때**(압박 없음) — "무슨 일이 생기면 살 것인가"를 조건으로 적는다
  ② **매일 run**(장중) — "그 일이 일어났는가"만 확인한다. 판단이 아니라 점검이다

매수 후에는 같은 방식으로 매도 조건을 걸어둔다. 파는 것도 미루기 쉬운 결정이라
("아직 지켜볼 만한데") 미리 적어두지 않으면 같은 함정에 빠진다.

가격·손익처럼 **기계로 판정되는 조건은 이 스크립트가 직접 판정한다** — LLM 재량이 아예
개입하지 않는다. 나머지(이벤트 결과 해석 등)는 `judge`로 표시해 분석 단계로 넘기되,
그때 묻는 질문은 "살까?"가 아니라 "이 조건이 충족됐는가?"라 훨씬 좁다.

사용:
    python3 theses.py list
    python3 theses.py check --snapshot data/snapshot_260908_kr.json --market KR
    python3 theses.py add --file /tmp/new_thesis.json
    python3 theses.py set-status --id <id> --status held|closed|expired --note "..."
    python3 theses.py set-trigger --id <id> --trigger x3 --check "price >= 152000" \
        --basis "resistance_1 152,000 (price_levels 20260922) — 과거 고정" --why "진입 전에 목표가 메워짐"
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
STORE = HERE / "journal" / "theses.json"
KST = timezone(timedelta(hours=9))

STATUSES = ("armed", "held", "closed", "expired")

# 기계 판정 가능한 조건. 변수는 스냅샷·보유에서 나오고, 사람이 쓴 문장은 못 들어온다.
VARS = ("price", "change_pct", "pnl_pct", "days_since_entry", "days_to_expiry")
COND = re.compile(rf"^\s*({'|'.join(VARS)})\s*(<=|>=|<|>|==)\s*(-?\d+(?:\.\d+)?)\s*$")


def load() -> dict:
    if not STORE.exists():
        return {"schema_version": "1.0", "theses": []}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise SystemExit(f"논지 원장을 읽을 수 없다 ({e}) — 손으로 확인할 것: {STORE}")


def save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def eval_cond(expr: str, ctx: dict, why: dict = None):
    """반환 True/False/None(판정 불가). eval을 쓰지 않는다 — 정규식이 허용한 형태만 계산한다.

    ★ `None`에는 **성질이 다른 두 가지**가 섞여 있었다.
    ① 식이 기계 판정 형태가 아니다 → 사람이 판단하기로 한 조건(정상).
    ② 식은 맞는데 **값이 없다**(시세 조회 실패·스냅샷에 그 종목이 없음) → **데이터 부재**.
    둘 다 "판정 필요"로 나가면, **시세를 못 받은 손절 트리거**가 "사람이 보기로 한 조건"과
    구분되지 않는다. 매도 쪽에서 이 혼동은 곧 놓친 손절이다.
    `why`를 주면 그 사유를 `{"kind": "unparsed"|"missing_value", "var": ...}`로 적어 준다.
    """
    w = why if why is not None else {}
    m = COND.match(expr or "")
    if not m:
        w.update({"kind": "unparsed", "expr": expr or ""})
        return None
    var, op, rhs = m.group(1), m.group(2), float(m.group(3))
    lhs = ctx.get(var)
    if lhs is None:
        w.update({"kind": "missing_value", "var": var, "expr": expr})
        return None
    return {"<=": lhs <= rhs, ">=": lhs >= rhs, "<": lhs < rhs,
            ">": lhs > rhs, "==": lhs == rhs}[op]


def build_ctx(t: dict, snapshot: dict) -> dict:
    """이 논지의 판정 문맥. 스냅샷의 시세·보유에서만 가져온다."""
    ticker = t.get("ticker")
    prices = snapshot.get("prices") or {}
    positions = {p["ticker"]: p for p in (snapshot.get("balance") or {}).get("positions", [])}
    q = prices.get(ticker) or {}
    pos = positions.get(ticker) or {}
    ctx = {"price": q.get("price") or pos.get("price"),
           "change_pct": q.get("change_pct"),
           "pnl_pct": pos.get("pnl_pct")}
    today = datetime.now(KST).date()
    for key, field in (("days_since_entry", "entered_on"), ("days_to_expiry", "expires")):
        v = t.get(field)
        if v:
            try:
                d = datetime.strptime(v, "%Y-%m-%d").date()
                ctx[key] = (today - d).days if key == "days_since_entry" else (d - today).days
            except ValueError:
                pass
    return ctx


def check(market: str, snapshot_path: Path) -> int:
    data = load()
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    today = datetime.now(KST).date()

    # `unmeasurable` = 식은 맞는데 **값이 없어서** 못 잰 것. `judge`(사람이 보기로 한 조건)와
    # 섞으면 시세를 못 받은 손절 트리거가 정상 판정 대기로 보인다.
    fired, judge, waiting, dead, unmeasurable, spent = [], [], [], [], [], []
    for t in data["theses"]:
        if market and t.get("market", "").upper() != market.upper():
            continue
        if t.get("status") not in ("armed", "held"):
            continue
        exp = t.get("expires")
        if exp:
            try:
                if datetime.strptime(exp, "%Y-%m-%d").date() < today:
                    dead.append(t)
                    continue
            except ValueError:
                pass

        ctx = build_ctx(t, snap)
        key = "exit_triggers" if t.get("status") == "held" else "entry_triggers"
        for trg in t.get(key) or []:
            if trg.get("fired"):
                # ★ 소진된 다리 — 이미 집행된 결정이다. 발화로 다시 세면 같은 결정을 두 번 집행한다(2026-09-23 TSM x3).
                spent.append({"thesis": t, "trigger": trg, "kind": key, "ctx": ctx, "why": {}})
                continue
            why = {}
            verdict = eval_cond(trg.get("check", ""), ctx, why)
            row = {"thesis": t, "trigger": trg, "kind": key, "ctx": ctx, "why": why}
            if verdict is True:
                fired.append(row)
            elif verdict is None and why.get("kind") == "missing_value":
                # 값이 없어서 못 잰 것 — 사람이 판단하기로 한 조건이 아니다.
                unmeasurable.append(row)
            elif verdict is None:
                judge.append(row)
            else:
                waiting.append(row)

    kind_ko = {"entry_triggers": "매수", "exit_triggers": "매도"}
    if spent:
        print(f"· 소진된 다리 {len(spent)}건 — 이미 집행됐다(다시 발화하지 않는다). 다시 걸려면 "
              f"`theses.py set-trigger`로 새 과거 고정 기준을 준다:")
        for r in spent:
            t_, g = r["thesis"], r["trigger"]
            print(f"    {t_['ticker']} {t_['id']}.{g.get('id')} `{g.get('check')}` — {str(g.get('fired_note') or '')[:70]}"
                  f" ({str(g.get('fired_at') or '')[:16]})")
        print()
    print(f"트리거 점검 — {market or '전체'} · {snapshot_path.name}\n")

    if fired:
        print("★ 발화 — 이미 내린 결정이다. 집행한다.")
        for r in fired:
            t, g = r["thesis"], r["trigger"]
            print(f"  [{kind_ko[r['kind']]}] {t['ticker']} {t.get('name', '')}  "
                  f"조건 {g['check']}  (현재 {r['ctx'].get('price')})")
            print(f"        비중 {g.get('size_pct', '?')}%  · 논지 {t['id']}")
    if unmeasurable:
        print("\n★ **재지 못했다 — 값이 없다.** 사람이 판단하기로 한 조건이 아니라 "
              "**데이터 부재**다. 매도 트리거가 여기 있으면 손절이 감시되지 않는 상태다.")
        for r in unmeasurable:
            t_, g = r["thesis"], r["trigger"]
            print(f"  [{kind_ko[r['kind']]}] {t_['ticker']} {t_.get('name', '')}: "
                  f"`{g.get('check', '')}` — `{r['why'].get('var')}` 값이 스냅샷에 없다")
            print(f"        → 시세 조회 실패인지 확인하고, 못 받았으면 그 사실을 "
                  f"노트 §10 한계에 적어라(‘조건 미충족’으로 쓰지 말 것). 논지 {t_['id']}")
    if judge:
        print("\n판정 필요 — 기계로 못 재는 조건. **'살까?'가 아니라 '이 조건이 충족됐는가?'만 답한다.")
        for r in judge:
            t, g = r["thesis"], r["trigger"]
            print(f"  [{kind_ko[r['kind']]}] {t['ticker']} {t.get('name', '')}: {g.get('when', '')}")
            print(f"        비중 {g.get('size_pct', '?')}%  · 논지 {t['id']}")
    if waiting:
        print("\n대기 중 — 조건 미충족(정상)")
        for r in waiting:
            t, g = r["thesis"], r["trigger"]
            cur = r["ctx"].get("price")
            print(f"  [{kind_ko[r['kind']]}] {t['ticker']} {g['check']}"
                  f"{f'  (현재 {cur:,.0f})' if isinstance(cur, (int, float)) else ''}")
    if dead:
        print("\n만료 — 논지 기한이 지났다. 재확인하거나 닫아야 한다.")
        for t in dead:
            print(f"  {t['ticker']} {t.get('name', '')}  (만료 {t['expires']}) · {t['id']}")
    if not (fired or judge or waiting or dead or unmeasurable):
        print("  걸린 논지가 없다. 새 논지를 세울 자리다(§8 리서치).")
    # 못 잰 것이 있으면 종료코드로도 알린다 — `stage.py`가 비0을 크게 찍는다.
    return 3 if unmeasurable else 0


def cmd_list() -> int:
    data = load()
    if not data["theses"]:
        print("논지 원장이 비어 있다.")
        return 0
    print(f"{'상태':7} {'시장':4} {'종목':20} {'축 id':22} {'만료':11} id")
    orphan = 0
    for t in data["theses"]:
        aid = t.get("axis_id") or "미지정"
        if aid == "미지정":
            orphan += 1
        print(f"{t.get('status', '?'):7} {t.get('market', '?'):4} "
              f"{t.get('ticker', '') + ' ' + t.get('name', ''):20} "
              f"{aid:22} {t.get('expires', '-'):11} {t.get('id', '')}")
    if orphan:
        # ★ 축 id가 없는 논지는 **교차 시장으로 이어지지 않는다** — 미국 조건이
        # 국내 대응을 부를 길이 없다. 침묵시키지 말고 매번 센다.
        print(f"\n★ 축 미지정 {orphan}건 — 이 논지들은 반대편 시장과 이어지지 않는다. "
              f"`market_map.py axis`로 축을 확인하고, 축이 없으면 먼저 세워라.")
    return 0


REQUIRED = ("id", "market", "ticker", "axis", "thesis", "expires")
# ★ `axis`(산문 라벨)만으로는 두 시장을 못 잇는다 — 지도의 축 id가 조인 키다.
# 새 논지는 `axis_id`를 반드시 달고, 그 id가 지도에 실재해야 한다.
REQUIRED_NEW = ("axis_id",)


def cmd_add(path: Path) -> int:
    new = json.loads(path.read_text(encoding="utf-8"))
    items = new if isinstance(new, list) else [new]
    data = load()
    have = {t["id"] for t in data["theses"]}
    for t in items:
        missing = [k for k in REQUIRED + REQUIRED_NEW if not t.get(k)]
        if missing:
            raise SystemExit(f"필수 필드 누락 {missing} — {t.get('id', '?')}. "
                             f"axis_id는 `market_map.py axis`의 축 id다")
        if t["axis_id"] != "미지정":
            import market_map as _mm
            ax = _mm.axes_index()[1].get(t["axis_id"])
            if ax is None:
                raise SystemExit(f"지도에 없는 축 id: {t['axis_id']} — {t['id']}. "
                                 f"축을 먼저 세우거나(`add-axis`) '미지정'으로 두어라")
            # ★ 논지는 전망에서 나온다. 축에 전망(방향·기간)이 있으면 horizon을 **상속**한다 —
            #   논지마다 호흡을 따로 정하면 전망 8주에 논지 5일이 걸리는 일이 생긴다.
            if ax.get("direction") and ax.get("horizon_weeks"):
                t.setdefault("horizon_days", int(ax["horizon_weeks"]) * 5)
                t["forecast_id"] = t["axis_id"]
                # 이 종목이 전망의 수혜 표에 있는가 — 없으면 전망 밖 논지다(경고, 차단 아님).
                bens = {b.get("ticker") for b in (ax.get("beneficiaries") or [])}
                if t["ticker"] not in bens:
                    print(f"  ※ {t['ticker']}가 전망 {t['axis_id']}의 수혜 표에 없다 — "
                          f"전망 밖 논지다. 수혜 표에 넣거나(`forecast add`) 사유를 적어라",
                          file=sys.stderr)
                else:
                    b = next(b for b in ax["beneficiaries"] if b.get("ticker") == t["ticker"])
                    t.setdefault("beneficiary_order", b.get("order"))
                    t.setdefault("priced_at_entry", b.get("priced"))
            else:
                print(f"  ※ 축 {t['axis_id']}에 전망(방향·기간)이 없다 — 논지가 전망 없이 섰다. "
                      f"`forecast add`로 먼저 세우는 것이 순서다", file=sys.stderr)
        if t["id"] in have:
            raise SystemExit(f"id 중복: {t['id']}")
        for g in (t.get("entry_triggers") or []) + (t.get("exit_triggers") or []):
            if not g.get("when"):
                raise SystemExit(f"트리거에 when(사람이 읽을 조건)이 없다 — {t['id']}")
            # "judge" = 기계로 못 재는 조건이라는 표시. 이때 판정은 분석 단계가 맡되,
            # 그 질문은 "살까?"가 아니라 "when에 적힌 이 조건이 충족됐는가?"로 좁혀진다.
            if g.get("check") in (None, "", "judge"):
                continue
            if eval_cond(g["check"], {v: 0 for v in VARS}) is None:
                raise SystemExit(f"판정식이 허용 형태가 아니다: {g['check']!r} — "
                                 f"쓸 수 있는 변수 {VARS}, 형태 '<변수> <=|>=|<|>|== <숫자>'")
        # ★ 레버리지 ETF는 호흡을 짧게 강제한다 — 일일 리셋 때문에 몇 주 들고 있으면 기초지수와
        #   어긋나 횡보장에서 녹는다. 막는 규칙이 아니라 수익을 지키는 규칙이다.
        import risk_guard as _rg
        lev, inv = _rg.leverage_factor(t.get("name", ""), t["ticker"])
        max_h = _rg.load_limits().get("leveraged_max_horizon_days") if hasattr(_rg, "load_limits") else None
        if max_h is None:
            try:
                max_h = json.loads((HERE / "config" / "limits.json").read_text(encoding="utf-8")).get("leveraged_max_horizon_days")
            except (json.JSONDecodeError, OSError):
                max_h = None
        if lev > 1 and max_h is not None and int(t.get("horizon_days") or 0) > int(max_h):
            raise SystemExit(f"{t['ticker']}는 {lev}배 ETF다 — horizon_days {t.get('horizon_days')} > 상한 {max_h}. "
                             f"레버리지는 며칠 단위로만 든다(일일 리셋 손실). 호흡을 줄이거나 기초 종목으로 바꿔라")
        if lev > 1:
            t["leverage"] = lev; t["inverse"] = inv
        t.setdefault("status", "armed")
        t.setdefault("created", datetime.now(KST).strftime("%Y-%m-%d"))
        data["theses"].append(t)
        have.add(t["id"])
        print(f"등록: {t['id']}  {t['ticker']} {t.get('name', '')}  "
              f"[{t.get('axis_id')} · {t['axis']}]")
    save(data)
    return 0


def cmd_status(tid: str, status: str, note: str) -> int:
    if status not in STATUSES:
        raise SystemExit(f"status는 {STATUSES} 중 하나")
    data = load()
    for t in data["theses"]:
        if t.get("id") == tid:
            t["status"] = status
            t.setdefault("history", []).append(
                {"ts": datetime.now(KST).isoformat(), "status": status, "note": note})
            if status == "held":
                t.setdefault("entered_on", datetime.now(KST).strftime("%Y-%m-%d"))
            save(data)
            print(f"{tid} → {status}")
            return 0
    raise SystemExit(f"그런 id가 없다: {tid}")


def cmd_set_trigger(tid: str, trig_id: str, check: str, basis: str, why: str,
                    when: str = "", size_pct: float = None) -> int:
    """트리거 하나를 **새 값으로 다시 건다** — 옛 값은 `corrections[]`에 남긴다(JSON 손편집 대신 명령).

    왜: 목표가 진입 전에 메워지면(`risk_guard` "목표 여유 없음" 거부) 다음 run이 **새 과거 고정 기준**으로
    목표를 다시 걸어야 한다. 원장을 조용히 고치면 "왜 바뀌었나"가 사라지므로 옛 값·사유·시각을 같이 남긴다.
    """
    if not COND.match(check):
        raise SystemExit(f"check가 기계 판정 형태가 아니다: {check!r} — `<변수> <=|>=|<|>|== <숫자>`")
    if not basis:
        raise SystemExit("--basis가 없다 — `basis` 없는 가격 트리거는 만들지 않는다(theses_levels.md)")
    data = load()
    t = next((x for x in data["theses"] if x.get("id") == tid), None)
    if t is None:
        raise SystemExit(f"그런 id가 없다: {tid}")
    for field in ("exit_triggers", "entry_triggers"):
        trg = next((x for x in (t.get(field) or []) if x.get("id") == trig_id), None)
        if trg is None:
            continue
        old = dict(trg)
        was_fired = bool(trg.get("fired"))
        t.setdefault("corrections", []).append(
            {"ts": datetime.now(KST).strftime("%Y-%m-%d"),
             "what": f"{trig_id} 트리거 {old.get('check')} → {check} (set-trigger)"
                     + (f" · 소진 표시 해제(옛 집행 {str(old.get('fired_note') or '')[:60]})" if was_fired else ""),
             "why": why, "trigger": trig_id, "field": field,
             "before": {k: old.get(k) for k in ("check", "basis", "when", "size_pct")},
             "after": {"check": check, "basis": basis, "when": when or old.get("when"),
                       "size_pct": size_pct if size_pct is not None else old.get("size_pct")}})
        trg["check"] = check
        trg["basis"] = basis
        if when:
            trg["when"] = when
        if size_pct is not None:
            trg["size_pct"] = size_pct
        # ★ 새 값으로 다시 건 다리는 **살아난다** — 소진 표시를 지운다(옛 집행은 corrections에 남는다).
        for k in ("fired", "fired_at", "fired_note"):
            trg.pop(k, None)
        save(data)
        print(f"{tid} {field}.{trig_id}: {old.get('check')} → {check} (corrections에 기록)")
        return 0
    raise SystemExit(f"{tid}에 트리거 {trig_id}가 없다 (entry/exit 모두)")


def cmd_fire(tid: str, trig_id: str, note: str, undo: bool = False) -> int:
    """트리거를 **소진(집행됨)으로 표시**하거나 되돌린다 — 과거 집행을 소급 기록할 때(JSON 손편집 대신).

    평소에는 `fill.py`가 체결 시 자동으로 찍는다. 이 명령은 그 전에 나간 집행을 메울 때만 쓴다.
    """
    data = load()
    t = next((x for x in data["theses"] if x.get("id") == tid), None)
    if t is None:
        raise SystemExit(f"그런 id가 없다: {tid}")
    for field in ("exit_triggers", "entry_triggers"):
        trg = next((x for x in (t.get(field) or []) if x.get("id") == trig_id), None)
        if trg is None:
            continue
        if undo:
            for k in ("fired", "fired_at", "fired_note"):
                trg.pop(k, None)
            t.setdefault("history", []).append(
                {"ts": datetime.now(KST).isoformat(), "status": t.get("status"),
                 "note": f"{trig_id} 소진 표시 해제 — {note}"})
        else:
            if not note:
                raise SystemExit("--note가 없다 — 무엇이 언제 집행됐는지 적어라(수량·가격·주문번호)")
            trg["fired"] = True
            trg["fired_at"] = datetime.now(KST).isoformat()
            trg["fired_note"] = note
            t.setdefault("history", []).append(
                {"ts": datetime.now(KST).isoformat(), "status": t.get("status"),
                 "note": f"{trig_id} 소진 표시 — {note}"})
        save(data)
        print(f"{tid} {field}.{trig_id}: {'소진 해제' if undo else '소진 표시'}")
        return 0
    raise SystemExit(f"{tid}에 트리거 {trig_id}가 없다 (entry/exit 모두)")


def main() -> int:
    ap = argparse.ArgumentParser(description="논지 원장 — 매수·매도 조건을 미리 걸어둔다")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("check")
    c.add_argument("--snapshot", required=True)
    c.add_argument("--market", default="")
    a = sub.add_parser("add")
    a.add_argument("--file", required=True)
    s = sub.add_parser("set-status")
    s.add_argument("--id", required=True)
    s.add_argument("--status", required=True)
    s.add_argument("--note", default="")
    st = sub.add_parser("set-trigger", help="트리거를 새 값으로 다시 건다 — 옛 값은 corrections[]에 남긴다")
    st.add_argument("--id", required=True)
    st.add_argument("--trigger", required=True, help="트리거 id (x3 · e1 …)")
    st.add_argument("--check", required=True, help='"price >= 152000" 같은 기계 판정식')
    st.add_argument("--basis", required=True, help="기준 (price_levels 이름·기준일 — 과거 고정)")
    st.add_argument("--why", required=True, help="왜 다시 거는가")
    st.add_argument("--when", default="", help="사람이 읽는 설명(생략 시 유지)")
    st.add_argument("--size-pct", type=float, default=None)
    fr = sub.add_parser("fire", help="트리거를 소진(집행됨)으로 표시 — 평소엔 fill.py가 자동으로 찍는다")
    fr.add_argument("--id", required=True)
    fr.add_argument("--trigger", required=True)
    fr.add_argument("--note", default="", help="무엇이 언제 집행됐나(수량·가격·주문번호)")
    fr.add_argument("--undo", action="store_true", help="소진 표시를 해제한다")
    args = ap.parse_args()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "check":
        p = Path(args.snapshot)
        if not p.is_absolute():
            p = HERE / p
        if not p.exists():
            print(f"스냅샷 없음: {p}", file=sys.stderr)
            return 2
        return check(args.market, p)
    if args.cmd == "add":
        p = Path(args.file)
        if not p.exists():
            print(f"파일 없음: {p}", file=sys.stderr)
            return 2
        return cmd_add(p)
    if args.cmd == "fire":
        return cmd_fire(args.id, args.trigger, args.note, args.undo)
    if args.cmd == "set-trigger":
        return cmd_set_trigger(args.id, args.trigger, args.check, args.basis, args.why,
                               args.when, args.size_pct)
    rc = cmd_status(args.id, args.status, args.note)
    # ★ 원장을 옮겼으면 그날 거래 기록의 `thesis_unsynced`도 같이 갱신한다 — 옛 값이 게이트를 막았다.
    try:
        import execute as ex
        today = datetime.now(KST).strftime("%y%m%d")
        for mkt in ("kr", "us"):
            for f in sorted(STORE.parent.glob(f"trades_*_{mkt}.json"))[-2:]:
                ex.refresh_thesis_unsynced(f)
    except Exception as e:                                  # noqa: BLE001
        print(f"  (trades thesis_unsynced 갱신 생략: {type(e).__name__})", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
