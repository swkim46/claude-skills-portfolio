#!/usr/bin/env python3
"""regret.py 회귀 — 네트워크 없이 픽스처로 돈다.

    python3 test_regret.py

★ 2026-10-11 사용자: "테스트가 멈추지 않도록 · 멈추는 걸 모르지 않도록." 구간(R1~R11)마다 함수로 격리해
  한 구간이 예외·SystemExit로 죽어도 FAIL로 적고 다음 구간으로 간다. 요약 전에 프로세스가 끝나면 atexit이
  '멈춤'을 크게 찍고 TEST-HALTED 줄을 남긴다. 요약에는 TEST-SUMMARY 줄 — `run_tests.py`가 센다.
"""
import atexit
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import regret as rg  # noqa: E402

FAILS = []
CHECKS = [0]
_STATE = {"current": "(시작 전)", "done": False}


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILS.append(name)


def _halt_banner():
    if _STATE["done"]:
        return
    print(f"\n★★ 테스트가 중간에 멈췄다 — 마지막으로 시작한 구간: {_STATE['current']} · 그때까지 검사 {CHECKS[0]}건(실패 {len(FAILS)}) · "
          f"이 뒤 구간은 돌지 않았다", file=sys.stderr)
    print(f"TEST-HALTED file=test_regret.py ran={CHECKS[0]} failed={len(FAILS)} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def _safe(fn):
    """구간 하나를 격리해 돌린다 — 예외·SystemExit가 나도 FAIL로 적고 다음 구간으로 간다(Ctrl-C만 통과)."""
    _STATE["current"] = fn.__doc__ or fn.__name__
    print(fn.__doc__ or fn.__name__)
    try:
        fn()
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001 — SystemExit 포함
        CHECKS[0] += 1
        FAILS.append(f"{fn.__name__} 중단")
        print(f"  ✗ {fn.__name__} — 중단({type(e).__name__}: {str(e)[:160]}) · 나머지 구간은 계속 돈다")


def mv(**k):
    base = {"ticker": "005930", "name": "삼성전자", "ret": 12.0, "trough_date": "260918", "peak_date": "260923",
            "from": "260917", "to": "260925", "low_min": 250000.0}
    base.update(k)
    return base


def ctx(**data):
    d = {"uni": {"005930"}}
    d.update(data)
    return rg.Ctx("kr", "260925", data=d)


# 구간끼리 같이 쓰는 픽스처(값만 — 실패할 일이 없다)
closed = {"id": "t1", "ticker": "005930", "status": "closed", "created": "2026-09-08",
          "history": [{"ts": "2026-09-21T13:14:00+09:00", "status": "closed"}]}
live = {"id": "t2", "ticker": "005930", "status": "held", "created": "2026-09-10",
        "entry_triggers": [{"id": "e1", "check": "price <= 230000", "size_pct": 2}]}
trades = [("260921", {"trades": [{"ticker": "005930", "action": "SELL", "qty": 4, "source": "llm",
                                  "trigger_id": "x2", "status": "SENT", "fill": {"verdict": "FILLED"}}]})]


class Cli:
    def __init__(self, ok):
        self.ok, self.calls = ok, []

    def overseas_daily(self, tk, excd, bymd):
        self.calls.append(excd)
        if excd != self.ok:
            return []
        return [{"date": f"202609{30 - i:02d}", "close": 100 + i, "high": 101 + i, "low": 99} for i in range(10)]


def r1():
    """(R1) 최대 상승폭 — 올랐다가 되돌린 종목도 잡는다"""
    ru = rg.run_up([{"date": "20260901", "close": 10, "high": 10}, {"date": "20260902", "close": 12, "high": 12},
                    {"date": "20260903", "close": 9, "high": 9}])
    check("a 10→12→9 = 최대 상승 +20%", abs(ru["ret"] - 20) < 1e-9, ru)
    check("b 되돌림 −25%", abs(ru["retrace_pct"] + 25) < 1e-9, ru)
    check("c 종가 대 종가는 −10%(참고값)", abs(ru["ret_close"] + 10) < 1e-9, ru)
    check("d 저점일·고점일", ru["trough_date"] == "260901" and ru["peak_date"] == "260902", ru)
    ru2 = rg.run_up([{"date": "260901", "close": 10, "high": 11}, {"date": "260902", "close": 8, "high": 9},
                     {"date": "260903", "close": 9, "high": 10.4}])
    check("e 저점이 뒤에 오면 그 저점에서 잰다(8→10.4 = +30%)", abs(ru2["ret"] - 30) < 1e-9 and ru2["trough_date"] == "260902", ru2)
    check("f 같은 날 저가→고가는 세지 않는다(첫날 고가 11 무시)", ru2["ret"] < 31, ru2)


def r2():
    """(R2) 미국 일봉은 시세용 거래소 코드로 묻고, 비면 다른 거래소로 다시 묻는다"""
    c = Cli("NYS")
    rows = rg.daily_rows(c, "ORCL", "us", "NYSE", "260930", 5)
    check("a 주문용 NYSE → 시세용 NYS로 첫 호출", c.calls[0] == "NYS", c.calls)
    check("b 6행·날짜 오름차순", len(rows) == 6 and rows[0]["date"] < rows[-1]["date"], rows)
    c = Cli("NYS")
    rg.daily_rows(c, "BE", "us", "NASD", "260930", 5)
    check("c 거래소가 틀려도 나머지 두 곳을 묻는다(NAS→NYS)", c.calls[:2] == ["NAS", "NYS"], c.calls)


def r3():
    """(R3) 닫힌 논지는 그 창의 '논지 있음'이 아니다"""
    st = rg.thesis_state("005930", ctx(theses=[closed]), "260922", "260925")
    check("a 창 시작(09-22) 전에 닫힌 논지 → thesis_live False", st["thesis_live"] is False and st["thesis_closed_before"], st)
    st = rg.thesis_state("005930", ctx(theses=[closed]), "260918", "260923")
    check("b 창 안에서 닫혔으면 살아 있던 것으로 센다", st["thesis_live"] is True, st)


def r4():
    """(R4) 진입가 미도달은 창 최저가와의 간격으로 적는다"""
    bs = rg.blockers(mv(), ctx(theses=[live]))
    eu = [b for b in bs if b["type"] == "entry_unreached"]
    check("a entry_unreached 1건", len(eu) == 1, bs)
    check("b 간격 +8.7% 위", eu and "+8.7%" in eu[0]["detail"], eu)
    fired = {**live, "entry_triggers": [{"id": "e1", "check": "price <= 230000", "fired": True}]}
    check("c 이미 발화한 다리는 미도달이 아니다",
          not [b for b in rg.blockers(mv(), ctx(theses=[fired])) if b["type"] == "entry_unreached"])


def r5():
    """(R5) 규칙 매도 뒤 재진입 논지가 없으면 그 사실을 적는다"""
    bs = rg.blockers(mv(), ctx(trades=trades, theses=[closed]))
    rs = [b for b in bs if b["type"] == "rule_sell"]
    check("a rule_sell 1건(trigger_id가 있으면 규칙 매도)", len(rs) == 1, bs)
    check("b '재진입 논지 없음'", rs and "재진입 논지 없음" in rs[0]["detail"], rs)
    reent = {"id": "t3", "ticker": "005930", "status": "held", "created": "2026-09-22", "entry_triggers": []}
    rs = [b for b in rg.blockers(mv(), ctx(trades=trades, theses=[closed, reent])) if b["type"] == "rule_sell"]
    check("c 매도 뒤 새 논지가 있으면 '재진입 논지 있음'", rs and "재진입 논지 있음" in rs[0]["detail"], rs)
    cancelled = [("260921", {"trades": [{"ticker": "005930", "action": "SELL", "qty": 4, "source": "discipline",
                                         "status": "SENT", "fill": {"verdict": "CANCELLED"}}]})]
    check("d 취소된 매도는 매도가 아니다",
          not [b for b in rg.blockers(mv(), ctx(trades=cancelled)) if b["type"].endswith("_sell")])


def r6():
    """(R6) 거부 사유는 유형으로 가른다"""
    appr = [("260920", {"rejected": [{"ticker": "005930", "why": "자금 배분 — KR 자산으로는 1주(275,000)도 못 산다"}],
                        "orders": []})]
    check("a 자금", rg.blockers(mv(), ctx(approved=appr))[0]["type"] == "funds")
    appr2 = [("260920", {"rejected": [], "orders": [{"ticker": "005930", "action": "BUY", "qty": 3}]})]
    tr2 = [("260920", {"trades": [{"ticker": "005930", "action": "BUY", "status": "FAILED", "fill": {"verdict": "REJECTED"}}]})]
    check("b 승인됐는데 체결 안 됨 → unsent", [b["type"] for b in rg.blockers(mv(), ctx(approved=appr2, trades=tr2))] == ["unsent"])


def r7():
    """(R7) '기록 없음'은 다른 유형이 하나도 없을 때만"""
    check("a 유니버스 안 · 논지 없음 → no_thesis", [b["type"] for b in rg.blockers(mv(), ctx())] == ["no_thesis"])
    check("b 유니버스 밖 → universe_out(기록 없음 아님)",
          [b["type"] for b in rg.blockers(mv(ticker="AMAT", name="Applied Materials"), ctx())] == ["universe_out"])
    judge = {"id": "t4", "ticker": "005930", "status": "held", "created": "2026-09-10",
             "entry_triggers": [{"id": "e1", "check": "judge"}]}
    check("c 논지는 살아 있는데 흔적 없음 → no_record", [b["type"] for b in rg.blockers(mv(), ctx(theses=[judge]))] == ["no_record"])
    snaps = [("260922", {"balance": {"eval_total": 1000, "cash": 990, "positions": [{"ticker": "005930", "qty": 1, "eval_amt": 10}]}})]
    check("d 보유 비중 1% → 극소 크기", rg.blockers(mv(), ctx(snaps=snaps))[0]["type"] == "tiny_size")


def r10():
    """(R10) 비중 분모는 종목 평가금액 합 + 현금 — 미국 eval_total(평가손익 원화)을 믿지 않는다"""
    us_snap = [("261006", {"balance": {"eval_total": 8171368.87, "cash": 10600.99, "positions": [
        {"ticker": "CEG", "qty": 71, "eval_amt": 21752.98}, {"ticker": "VST", "qty": 108, "eval_amt": 17300.0},
        {"ticker": "OTHER", "qty": 1, "eval_amt": 59948.0}]}})]
    held, w, now = rg._weight(ctx(snaps=us_snap), "CEG", "260930", "261006")
    check("a CEG 21,753 / (109,001 + 10,601) ≈ 18.2%(eval_total로 나누면 0.27%)", abs(w - 21752.98 / (21752.98 + 17300 + 59948 + 10600.99) * 100) < 1e-6, w)
    check("b 큰 보유는 극소 크기가 아니다",
          not [b for b in rg.blockers(mv(ticker="CEG", name="Constellation", trough_date="260930", peak_date="261006"),
                                      ctx(snaps=us_snap, uni={"CEG"})) if b["type"] == "tiny_size"])


def r11():
    """(R11) 전망 수혜 표에만 있는 종목도 그 축에 걸린 것이다"""
    import market_map as _mm
    _orig = _mm.axes_index
    _mm.axes_index = lambda: ({"000660": ["ax1"]}, {"ax1": {"id": "ax1", "direction": "+", "names": [{"ticker": "000660"}],
                                                            "beneficiaries": [{"ticker": "000660", "order": 1}, {"ticker": "281820", "order": 2}]}})
    try:
        c2 = rg.Ctx("kr", "261008")
    finally:
        _mm.axes_index = _orig
    check("a 수혜 표에만 있는 281820 → 축 ax1", c2.by_tk.get("281820") == ["ax1"], c2.by_tk.get("281820"))
    check("b names와 수혜 표 둘 다 있는 000660은 한 번만", c2.by_tk.get("000660") == ["ax1"], c2.by_tk.get("000660"))


def r9():
    """(R9) 일반 낱말과 같은 티커는 그 낱말 문맥만 빼고 센다"""
    check("a 'ETF·ETN 제외'는 이튼 언급이 아니다", not rg._mentions("KRX 애프터마켓 (ETF·ETN 제외)", "ETN", "Eaton"))
    check("b 'Eaton'이 있으면 센다", rg._mentions("ETN Eaton +6.5%", "ETN", "Eaton"))
    check("b2 티커 나열 속 ETN은 센다(이름 없어도)", rg._mentions("GEV · ETN · CMI · GNRC · ai_power", "ETN", "Eaton"))
    check("c 보통 티커는 경계로 센다(MU ≠ MUST)", rg._mentions("MU +4%", "MU", "Micron") and not rg._mentions("MUST win", "MU", "Micron"))


def r8():
    """(R8) 주간 집계는 종목 단위"""
    rows = [{"stamp": "260921", "market": "kr", "ticker": "A", "ret": 9, "axis": 1, "forecast": 0, "thesis": 0, "position": 0, "blocked": "기록 없음"},
            {"stamp": "260922", "market": "kr", "ticker": "A", "ret": 14, "axis": 1, "forecast": 1, "thesis": 0, "position": 0,
             "blockers": [{"type": "no_thesis"}]}]
    orig = rg.load_ledger
    rg.load_ledger = lambda since="": rows
    try:
        w = rg.weekly(7000)
    finally:
        rg.load_ledger = orig
    check("a 같은 종목 두 행 → 1종목", "급등 1종목" in w, w[:200])
    check("b 큰 움직임 행의 유형을 센다(논지 없음)", "| 논지 없음 | 1 |" in w, w)


for _fn in (r1, r2, r3, r4, r5, r6, r7, r10, r11, r9, r8):
    _safe(_fn)

_STATE["done"] = True
print()
print("FAIL " + ", ".join(FAILS) if FAILS else "ALL PASS")
print(f"TEST-SUMMARY file=test_regret.py total={CHECKS[0]} passed={CHECKS[0] - len(FAILS)} failed={len(FAILS)}")
sys.exit(1 if FAILS else 0)
