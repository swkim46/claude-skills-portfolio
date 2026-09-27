#!/usr/bin/env python3
"""배분 원장 — 주식 비율은 상수가 아니라 **이어받는 판단**이다.

왜 이 파일이 있는가. 2026-09-22까지 주식 비율은 `limits.json`의 상수(목표 60% · 하한 20% · 상한 70% ·
예약 15%/25%)가 정했고, 모델은 "이번엔 현금을 남기겠다/전부 넣겠다"를 적을 자리가 없었다. 사용자:
"현금을 보유하려면 명분이 있어야 한다 — 무작정 상수로 주식 비율을 설정하지 마라. 적절한 비율을
판단하고, 못 미치면 최대한 빨리 도달하게 하라." 그리고 "비율과 명분은 매번 독립적으로가 아니라
이전에 설정한 히스토리 위에 세운다."

그래서 배분 판단은 논지·시나리오처럼 **원장**이다: run마다 한 행, `based_on`으로 직전 판단을 가리키고,
바꿀 때만 `change.why`를 적는다. 바뀌지 않는 한 그대로 간다 — 판단은 상수가 아니라 상태다.

행: {id: "AL-<stamp>-<mkt>", stamp, market, based_on, target_invested_pct, regime, cash_reason,
     cash_release_when, reserved[], change|null, recorded_at, approved_ref}

사용:
    python3 allocation.py prev                 # 직전 판단 블록(2단 carry가 붙인다)
    python3 allocation.py record --approved signals/approved_260922_us.json
    python3 allocation.py show [--n 10]
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOURNAL_DIR = HERE / "journal"
KST = timezone(timedelta(hours=9))

NO_CASH_REASON = ("", "없음", "none", "n/a", "-")


def _ledger() -> Path:
    return JOURNAL_DIR / "allocation.jsonl"


def rows() -> list:
    p = _ledger()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def last(exclude_id: str = None) -> dict:
    """마지막 판단. `exclude_id`를 주면 그 id(= 이번 run이 이미 쓴 행)는 뺀다 — 같은 run에서
    risk_guard를 다시 돌려도 `based_on`이 직전 *다른* run을 가리키면 된다."""
    for r in reversed(rows()):
        if exclude_id and r.get("id") == exclude_id:
            continue
        return r
    return {}


def make_id(stamp: str, market: str) -> str:
    return f"AL-{stamp}-{(market or '').lower()}"


def validate(alloc, this_id: str) -> list:
    """시그널 `allocation` 블록의 **구조·이어받기** 검사. 오류 문자열 목록(비면 통과).
    현금 명분이 필요한지(목표 < 100 − 예약%)는 자산을 아는 risk_guard가 본다(`cash_reason_needed`)."""
    errs = []
    if not isinstance(alloc, dict):
        return ["allocation 블록이 없다 — 이번 run의 주식 비율 판단(target_invested_pct·regime·cash_reason·"
                "reserved·based_on)을 시그널에 실어라. 상수 목표는 없다"]
    tgt = alloc.get("target_invested_pct")
    if not isinstance(tgt, (int, float)) or not 0 <= float(tgt) <= 100:
        errs.append(f"allocation.target_invested_pct는 0~100 숫자여야 한다: {tgt!r}")
    regime = str(alloc.get("regime") or "").strip()
    if not regime:
        errs.append("allocation.regime이 비어 있다 — 국면 판단을 보드·벤치마크 숫자로 적어라")
    elif not re.search(r"\d", regime):
        errs.append("allocation.regime에 숫자가 없다 — 명분은 숫자를 인용한다(캡처의 '국면 재료' 블록: 벤치마크 20·60일·이동평균·상승/하락 섹터 수)")
    res = alloc.get("reserved")
    if res is None:
        res = []
    if not isinstance(res, list):
        errs.append("allocation.reserved는 배열이어야 한다")
    else:
        for i, r in enumerate(res):
            if not isinstance(r, dict) or not r.get("thesis_id") or r.get("market") not in ("KR", "US") \
                    or not isinstance(r.get("amount"), (int, float)) or float(r.get("amount")) < 0 \
                    or not str(r.get("why") or "").strip():
                errs.append(f"allocation.reserved[{i}]: thesis_id·market(KR|US)·amount(≥0)·why가 전부 있어야 한다")
    prev = last(exclude_id=this_id)
    based = alloc.get("based_on")
    if prev:
        if based != prev.get("id"):
            errs.append(f"allocation.based_on={based!r} — 직전 판단 {prev.get('id')}을 읽고 그 위에서 정해야 한다"
                        f"(`python3 allocation.py prev`)")
        else:
            changed = (float(tgt) != float(prev.get("target_invested_pct") or 0)) if isinstance(tgt, (int, float)) else False
            changed = changed or (str(alloc.get("cash_reason") or "").strip() != str(prev.get("cash_reason") or "").strip())
            ch = alloc.get("change")
            if changed and not (isinstance(ch, dict) and str(ch.get("why") or "").strip()):
                errs.append("비율 또는 현금 명분이 직전 판단과 다른데 change.why가 없다 — 무엇이 새로 알려져 바꾸는지 적어라"
                            f"(직전 {prev.get('target_invested_pct')}% · {str(prev.get('cash_reason') or '없음')[:40]})")
    elif based not in (None, ""):
        errs.append(f"원장이 비어 있는데 based_on={based!r} — 최초 판단은 based_on: null")
    return errs


FULL_INVEST_SLACK_PCT = 5.0   # 호가 반올림·수수료·다음 run 주문 여유 — 현금 목표가 아니라 집행 여유라 명분이 필요 없다


def cash_reason_needed(alloc: dict, reserved_pct: float) -> str:
    """목표 < 100 − 예약% − 집행 여유(5%p) 인데 명분이나 해제 조건이 없으면 오류 문자열, 아니면 ''."""
    tgt = float(alloc.get("target_invested_pct") or 0)
    if tgt >= 100 - reserved_pct - FULL_INVEST_SLACK_PCT:
        return ""
    reason = str(alloc.get("cash_reason") or "").strip()
    if reason.lower() in NO_CASH_REASON:
        return (f"현금 명분 없음 — 목표 {tgt:.0f}%가 전부 넣기(100 − 예약 {reserved_pct:.1f}% − 집행 여유 "
                f"{FULL_INVEST_SLACK_PCT:.0f}%p)보다 낮은데 cash_reason이 없다. 현금을 남기려면 국면 판단의 명분을 적거나 목표를 올려라")
    if not str(alloc.get("cash_release_when") or "").strip():
        return ("현금 명분에 해제 조건이 없다 — cash_release_when(명분이 끝나는 조건)을 적어라. "
                "5단이 그 조건을 시나리오 원장에 올려 매 run 판정한다")
    return ""


def default_reserved(market: str, equity_by_market: dict, theses: list = None) -> list:
    """기계가 논지 원장에서 계산한 예약 기본값 — armed 논지의 **다음 사다리 칸**(`entry_triggers` 중 아직 안 산
    첫 칸) `size_pct` × 그 시장 자산. 이미 기록된 판단이라 모델이 매번 다시 정하지 않아도 된다."""
    if theses is None:
        try:
            theses = json.loads((JOURNAL_DIR / "theses.json").read_text(encoding="utf-8")).get("theses") or []
        except (OSError, json.JSONDecodeError):
            theses = []
    out = []
    for t in theses:
        if t.get("status") != "armed":
            continue
        mkt = str(t.get("market") or "").upper()
        eq = float((equity_by_market or {}).get(mkt) or 0)
        if eq <= 0:
            continue
        rungs = [r for r in (t.get("entry_triggers") or []) if isinstance(r, dict)]
        if not rungs:
            continue
        pct = float(rungs[0].get("size_pct") or 0)
        if pct <= 0:
            continue
        out.append({"thesis_id": t.get("id"), "market": mkt, "amount": round(eq * pct / 100),
                    "why": f"{t.get('ticker')} 다음 칸 {rungs[0].get('id')} {pct:g}% 대기(논지 원장 기본값)"})
    return out


def block(market: str = "") -> str:
    """carry 파일에 붙이는 직전 판단 블록."""
    r = last()
    if not r:
        return ("■ 배분 판단 — 직전 **없음** (원장이 비어 있다 · 최초 판단)\n"
                "  기본은 전부 넣는 것이다. 현금을 남기려면 명분과 해제 조건을 적는다. based_on: null")
    res = r.get("reserved") or []
    res_s = " · ".join(f"{x.get('thesis_id')} {x.get('market')} {float(x.get('amount') or 0):,.0f}" for x in res) or "없음"
    ch = r.get("change")
    ch_s = (f"{ch.get('from')}→{ch.get('to')}%: {str(ch.get('why'))[:120]}" if isinstance(ch, dict) and ch else "유지")
    return (f"■ 배분 판단 — 직전 {r.get('id')} ({r.get('market', '').upper()} {r.get('stamp')})\n"
            f"  목표 주식 비율 {float(r.get('target_invested_pct') or 0):.0f}%\n"
            f"  국면: {str(r.get('regime') or '')[:300]}\n"
            f"  현금 명분: {str(r.get('cash_reason') or '없음')[:200]}\n"
            f"  해제 조건: {str(r.get('cash_release_when') or '')[:160] or '(없음)'}\n"
            f"  예약: {res_s}\n"
            f"  직전 변경: {ch_s}\n"
            f"  → 이번 시그널 allocation.based_on = \"{r.get('id')}\". 유지하면 change: null, 바꾸면 change.why에 "
            f"무엇이 새로 알려졌는지(재료 확장·회고·이벤트 결과)를 적는다.")


def record(approved: dict, approved_ref: str = "") -> dict:
    """approved의 `allocation`을 원장에 쓴다 — 같은 id(같은 run)가 있으면 **덮어쓴다**(risk_guard 재실행)."""
    al = approved.get("allocation") or {}
    market = str(approved.get("market") or "").lower()
    m = re.search(r"_(\d{6})_(kr|us)", str(approved_ref or approved.get("signal_ref") or ""))
    stamp = m.group(1) if m else datetime.now(KST).strftime("%y%m%d")
    row = {"id": make_id(stamp, market), "stamp": stamp, "market": market,
           "based_on": al.get("based_on"),
           "target_invested_pct": al.get("target_invested_pct"),
           "regime": al.get("regime"), "cash_reason": al.get("cash_reason"),
           "cash_release_when": al.get("cash_release_when"),
           "reserved": al.get("reserved") or [], "change": al.get("change"),
           "gap_pct": al.get("gap_pct"), "deployable": al.get("deployable"),
           "recorded_at": datetime.now(KST).isoformat(), "approved_ref": approved_ref}
    rs = [r for r in rows() if r.get("id") != row["id"]]
    rs.append(row)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    _ledger().write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs), encoding="utf-8")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prev", help="직전 배분 판단 블록")
    rc = sub.add_parser("record", help="approved의 allocation을 원장에 기록")
    rc.add_argument("--approved", required=True)
    sh = sub.add_parser("show")
    sh.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    if a.cmd == "prev":
        print(block())
        return 0
    if a.cmd == "record":
        p = Path(a.approved)
        if not p.exists():
            print(f"파일 없음: {p}", file=sys.stderr)
            return 2
        row = record(json.loads(p.read_text(encoding="utf-8")), str(p))
        print(f"기록: {row['id']} 목표 {row['target_invested_pct']}% · based_on {row['based_on']}")
        return 0
    for r in rows()[-a.n:]:
        ch = r.get("change")
        print(f"{r.get('id')}  목표 {r.get('target_invested_pct')}%  현금 명분: {str(r.get('cash_reason') or '없음')[:50]}"
              + (f"  변경: {ch.get('from')}→{ch.get('to')} {str(ch.get('why'))[:60]}" if isinstance(ch, dict) and ch else "  유지"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
