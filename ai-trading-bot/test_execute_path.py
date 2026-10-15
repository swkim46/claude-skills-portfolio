#!/usr/bin/env python3
"""
집행 경로 종단 테스트 — 가짜 증권사로 `execute`·`stops`·`fill`을 **전송부터 체결 확정까지** 끝까지 돌린다.

왜(2026-10-13 사고): 10-12 20:56 코드 편집이 `execute._send_orders`의 전송 줄(`res = place(client, o)`)을 지웠다.
문법 검사와 `test_risk_guard.py` 130건이 편집 24분 뒤에도 통과했다 — 그 함수를 부르는 테스트가 하나도 없었다.
그날 밤 예약 run의 `--send`가 전부 브로커 호출 전에 UnboundLocalError로 죽었다(주문 0건). 그래서 여기서는 함수를
따로 부르지 않고 **실제 run이 타는 경로**(execute.main · _send_orders · fill.confirm · stops.intraday)를 그대로 돈다.

규칙: 네트워크·.env·실제 `config/limits.json`·실제 `journal/`을 건드리지 않는다. 모듈 전역은 케이스마다 `finally`로 되돌린다.
`execute.py`를 서브프로세스로 띄우지 않는다(실제 설정을 읽는다) — `stops`의 전송은 기록기로 받는다.

    python3 test_execute_path.py          # run_tests.py가 부른다
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import risk_guard as rg            # noqa: E402
import execute as ex               # noqa: E402
import fill                        # noqa: E402
import stops                       # noqa: E402
import kis_client as kc            # noqa: E402

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []

import atexit  # noqa: E402
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n★★ 테스트가 중간에 멈췄다 — 마지막으로 시작한 케이스: {_STATE['current']} · 그때까지 {n}건(실패 {nf})",
          file=sys.stderr)
    print(f"TEST-HALTED file=test_execute_path.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def _safe(fn, *a, **k):
    """케이스 하나를 격리해 돌린다 — 예외·SystemExit도 FAIL로 적고 다음으로 간다."""
    name = getattr(fn, "__name__", str(fn))
    _STATE["current"] = name
    try:
        return fn(*a, **k)
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001
        results.append((FAIL, f"{name} — 중단", f"{type(e).__name__}: {e}"))
        print(f"[{FAIL}] {name} — 중단({type(e).__name__}: {str(e)[:160]})")


def report(label: str, problems: list):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


# ───────────────────────────────────────────────────────────── 가짜 증권사

class FakeBroker:
    """덕 타이핑 가짜 증권사 — 주문을 받으면 번호를 주고, 상태 조회 첫 폴링에 전량 체결로 답한다.

    `fill_on_place=True`면 주문 즉시 잔고가 움직인다(→ execute의 즉시 잔고 대조가 FILLED로 닫는다).
    정정·취소는 기록만 한다(이 테스트에서 불리면 안 된다). 상태기계 세부는 test_risk_guard (56)이 본다.
    """
    current = None

    def __init__(self, prices=None, positions=None, bars=None, fill_on_place=False, cash=100_000_000.0):
        self.svr, self.mode, self.kr_excg, self.read_only = "paper", "paper", "KRX", False
        self.calls, self.orders = [], {}
        self.prices = dict(prices or {})
        self.positions = {p["ticker"]: dict(p) for p in (positions or [])}
        self.bars = dict(bars or {})
        self.fill_on_place = fill_on_place
        self.cash = cash
        self._n = 1000
        FakeBroker.current = self

    @classmethod
    def for_mode(cls, orders=False, cfg=None):
        return cls.current

    # 시세·잔고
    def domestic_price(self, ticker):
        return {"ticker": ticker, "price": float(self.prices.get(ticker, 70000)), "change_pct": 0.0}

    def overseas_price(self, ticker, excd="NAS"):
        self.calls.append(("overseas_price", {"ticker": ticker, "excd": excd}))
        return {"ticker": ticker, "price": float(self.prices.get(ticker, 100.0)), "change_pct": 0.0, "currency": "USD"}

    def _bal(self, market):
        pos = [dict(p, market=market) for p in self.positions.values()
               if p.get("market", "KR") == market and int(p.get("qty") or 0) > 0]
        for p in pos:
            p.setdefault("price", self.prices.get(p["ticker"], p.get("avg_price", 0)))
            p["eval_amt"] = float(p["price"]) * int(p["qty"])
        return {"currency": "KRW" if market == "KR" else "USD", "cash": self.cash, "positions": pos}

    def domestic_balance(self):
        return self._bal("KR")

    def overseas_balance(self, excd="NASD", currency="USD"):
        return self._bal("US")

    def domestic_daily(self, ticker, start="", end=""):
        b = self.bars.get(ticker)
        return [b] if b else []

    def overseas_daily(self, ticker, excd="NAS", bymd=""):
        b = self.bars.get(ticker)
        return [b] if b else []

    # 주문
    def _place(self, kind, market, ticker, side, qty, price, **extra):
        self._n += 1
        no = f"{self._n:010d}"
        self.calls.append((kind, {"ticker": ticker, "side": side, "qty": qty, "price": price, **extra}))
        self.orders[no] = {"market": market, "ticker": ticker, "side": side, "qty": int(qty), "price": float(price),
                           "applied": False}
        if self.fill_on_place:
            self._apply(no)
        return {"order_no": no, "order_time": "000000", "orgno": "00950", "excg": extra.get("excg"),
                "raw": {"rt_cd": "0"}}

    def _apply(self, no):
        o = self.orders[no]
        if o["applied"]:
            return
        o["applied"] = True
        p = self.positions.setdefault(o["ticker"], {"ticker": o["ticker"], "qty": 0, "avg_price": o["price"],
                                                    "market": o["market"]})
        p["qty"] = int(p.get("qty") or 0) + (o["qty"] if o["side"] == "BUY" else -o["qty"])

    def domestic_order(self, ticker, side, qty, price=0, market_order=False, excg=None):
        return self._place("domestic_order", "KR", ticker, side, qty, price, excg=excg or self.kr_excg)

    def overseas_order(self, ticker, side, qty, limit_price, excd="NASD"):
        return self._place("overseas_order", "US", ticker, side, qty, limit_price, excd=excd)

    def overseas_daytime_order(self, ticker, side, qty, limit_price, excd="NASD"):
        if side != "SELL":
            raise ValueError("주간거래는 매도 전용")
        return self._place("overseas_daytime_order", "US", ticker, side, qty, limit_price, excd=excd)

    def _status(self, order_no):
        o = self.orders.get(str(order_no))
        if not o:
            return {"order_no": order_no, "found": False, "ord_qty": 0, "filled_qty": 0, "remain_qty": 0,
                    "avg_price": 0.0, "cancelled": False, "rejected": False, "reject_reason": "", "orgno": "", "raw": {}}
        self._apply(str(order_no))
        return {"order_no": order_no, "found": True, "ord_qty": o["qty"], "filled_qty": o["qty"], "remain_qty": 0,
                "avg_price": o["price"], "cancelled": False, "rejected": False, "reject_reason": "",
                "orgno": "00950", "raw": {}}

    def domestic_order_status(self, order_no, ticker="", date=""):
        return self._status(order_no)

    def overseas_order_status(self, order_no, ticker="", excd="", date=""):
        return self._status(order_no)

    def domestic_modify(self, *a, **k):
        self.calls.append(("domestic_modify", k))
        return {"order_no": "9999999999"}

    def overseas_modify(self, *a, **k):
        self.calls.append(("overseas_modify", k))
        return {"order_no": "9999999998"}

    def overseas_daytime_modify(self, *a, **k):
        self.calls.append(("overseas_daytime_modify", k))
        return {"order_no": "9999999997"}

    def order_calls(self):
        return [c for c in self.calls if c[0] in ("domestic_order", "overseas_order", "overseas_daytime_order")]


# ───────────────────────────────────────────────────────────── 격리 환경

def _open_session(*a, **k):
    now = datetime.now(KST)
    return {"market": "X", "open": now - timedelta(hours=1), "close": now + timedelta(hours=1), "is_open": True,
            "holiday": False, "session_date": now.date(), "why": "테스트 — 열림"}


@contextlib.contextmanager
def env():
    """임시 config·journal·signals로 모듈 전역을 갈아끼운다. 실제 설정·원장은 읽지도 쓰지도 않는다."""
    tmp = Path(tempfile.mkdtemp(prefix="exectest_"))
    cfg = tmp / "config"
    cfg.mkdir()
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim.update({"account_mode": "paper", "real_trading_enabled": False, "halt_all": False})
    (cfg / "limits.json").write_text(json.dumps(lim, ensure_ascii=False), encoding="utf-8")
    shutil.copy(rg.WATCHLIST_PATH, cfg / "watchlist.json")
    for d in ("journal", "signals", "data", "evid"):
        (tmp / d).mkdir()
    import allocation as _al
    saved = {
        "rg": (rg.LIMITS_PATH, rg.WATCHLIST_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.PEAKS_PATH, rg.DATA_DIR,
               rg.JOURNAL_DIR, rg.CALENDAR_PATH),
        "al": _al.JOURNAL_DIR,
        "ex": (ex.JOURNAL_DIR, ex.KisClient, ex.market_session, ex.us_daytime_session, ex._send_cfg),
        "fill": (fill.JOURNAL_DIR, fill.LIMITS_PATH, fill.confirm),
        "stops": (stops.KisClient, stops.market_session, stops.EVID, stops.SIGNALS, stops.held_theses,
                  stops._entry_touches, stops.subprocess.call),
        "argv": list(sys.argv),
    }
    rg.LIMITS_PATH, rg.WATCHLIST_PATH, rg.KILL_PATH = cfg / "limits.json", cfg / "watchlist.json", cfg / "KILL"
    rg.SIGNALS_DIR, rg.PEAKS_PATH = tmp / "signals", tmp / "peaks.json"
    rg.DATA_DIR, rg.JOURNAL_DIR, rg.CALENDAR_PATH = tmp / "data", tmp / "journal", tmp / "journal" / "calendar.json"
    _al.JOURNAL_DIR = tmp / "journal"
    ex.JOURNAL_DIR = tmp / "journal"
    ex.KisClient = FakeBroker
    ex.market_session = ex.us_daytime_session = _open_session
    ex._send_cfg = lambda: {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 0}
    fill.JOURNAL_DIR, fill.LIMITS_PATH = tmp / "journal", cfg / "limits.json"
    _orig_confirm = saved["fill"][2]
    fill.confirm = lambda *a, **k: _orig_confirm(*a, **{**k, "sleep_fn": lambda s: None, "quiet": True})
    stops.KisClient, stops.EVID, stops.SIGNALS = FakeBroker, tmp / "evid", tmp / "signals"
    try:
        yield tmp
    finally:
        (rg.LIMITS_PATH, rg.WATCHLIST_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.PEAKS_PATH, rg.DATA_DIR,
         rg.JOURNAL_DIR, rg.CALENDAR_PATH) = saved["rg"]
        _al.JOURNAL_DIR = saved["al"]
        (ex.JOURNAL_DIR, ex.KisClient, ex.market_session, ex.us_daytime_session, ex._send_cfg) = saved["ex"]
        (fill.JOURNAL_DIR, fill.LIMITS_PATH, fill.confirm) = saved["fill"]
        (stops.KisClient, stops.market_session, stops.EVID, stops.SIGNALS, stops.held_theses,
         stops._entry_touches, stops.subprocess.call) = saved["stops"]
        sys.argv = saved["argv"]
        shutil.rmtree(tmp, ignore_errors=True)


def approved_file(tmp: Path, market: str, orders: list, name: str = None, **hdr) -> Path:
    """risk_guard가 쓰는 머리를 그대로 갖춘 승인 파일."""
    body = {"schema_version": rg.SCHEMA_VERSION, "market": market, "generated_at": datetime.now(KST).isoformat(),
            "signal_ref": None, "snapshot_ref": "test", "limits_sha256": rg.sha256_of(rg.LIMITS_PATH),
            "orders": orders, "rejected": [], "account_mode": "paper", "order_cap": None, **hdr}
    p = tmp / "signals" / (name or f"approved_261013_{market.lower()}.json")
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


def run_main(argv: list) -> tuple:
    """execute.main()을 run과 같은 인자로 부른다. (종료코드, 출력)."""
    sys.argv = ["execute.py"] + argv
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = ex.main()
    return rc, buf.getvalue()


def KR(ticker, action, qty, price, **k):
    return {"market": "KR", "ticker": ticker, "name": ticker, "action": action, "qty": qty, "price": price,
            "source": k.pop("source", "llm"), **k}


def US(ticker, action, qty, price, **k):
    return {"market": "US", "ticker": ticker, "name": ticker, "action": action, "qty": qty, "price": price,
            "excd": k.pop("excd", "NASD"), "source": k.pop("source", "llm"), **k}


# ───────────────────────────────────────────────────────────── 케이스

def test_e1_send_loop_kr():
    """E1 — `_send_orders`가 국내 매수·매도를 실제로 증권사에 보낸다(10-12 사고: 전송 줄이 빠져 0건)."""
    p = []
    with env():
        fb = FakeBroker(prices={"005930": 70000, "000660": 200000},
                        positions=[{"ticker": "000660", "qty": 5, "avg_price": 190000, "market": "KR"}])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            trades, failures = ex._send_orders(fb, [KR("005930", "BUY", 3, 70000.0), KR("000660", "SELL", 2, 200000.0)],
                                               "KR", "approved_x.json", "2026-10-13T10:00:00+09:00", None)
        sent = fb.order_calls()
        if len(sent) != 2:
            p.append(f"증권사 주문 호출 {len(sent)}건(기대 2) — 전송 줄이 빠졌나")
        if failures:
            p.append(f"실패 {failures}건")
        if [t.get("status") for t in trades] != ["SENT", "SENT"]:
            p.append(f"행 상태 {[t.get('status') for t in trades]}")
        if not all((t.get("result") or {}).get("order_no") for t in trades):
            p.append("주문번호 없는 행")
        if sent and [c[1]["side"] for c in sent] != ["BUY", "SELL"]:
            p.append(f"방향 {[c[1]['side'] for c in sent]}")
        if sent and not all(isinstance(c[1]["price"], int) for c in sent):
            p.append("국내 지정가가 정수가 아니다")
    report("E1 국내 전송 루프 — 매수·매도가 증권사에 닿는다", p)


def test_e2_send_loop_us_daytime():
    """E2 — 미국 정규장은 overseas_order, `session: daytime` 매도는 주간거래 TR로. 시세 코드는 주문 코드로."""
    p = []
    with env():
        fb = FakeBroker(prices={"AAPL": 250.0, "MU": 900.0})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            trades, failures = ex._send_orders(
                fb, [US("AAPL", "BUY", 2, 250.0, excd="NAS"), US("MU", "SELL", 1, 900.0, session="daytime")],
                "US", "approved_y.json", "2026-10-13T23:00:00+09:00", None)
        kinds = [c[0] for c in fb.order_calls()]
        if kinds != ["overseas_order", "overseas_daytime_order"]:
            p.append(f"호출 순서·종류 {kinds}")
        reg = [c for c in fb.order_calls() if c[0] == "overseas_order"]
        if reg and reg[0][1].get("excd") != "NASD":
            p.append(f"시세 코드 NAS가 주문 코드로 안 바뀌었다: {reg[0][1].get('excd')}")
        if [t.get("session") for t in trades] != ["regular", "daytime"]:
            p.append(f"session 기록 {[t.get('session') for t in trades]}")
        if failures:
            p.append(f"실패 {failures}건")
    report("E2 미국 전송 — 정규장·주간거래 매도 경로", p)


def test_e3_order_cap_at_send():
    """E3 — 전송 직전 주문당 상한: 승인 자체가 상한을 넘으면 보내지 않는다 · 안이면 그대로 보낸다."""
    p = []
    cap = {"max_krw": 1_000_000, "max_shares": 2, "fx": 1.0}
    with env():
        fb = FakeBroker(prices={"005930": 70000})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            t_over, f_over = ex._send_orders(fb, [KR("005930", "BUY", 20, 70000.0)], "KR", "a.json", "", cap)
        if fb.order_calls():
            p.append("상한을 넘는 승인이 증권사에 나갔다")
        if not (t_over and t_over[0].get("status") == "FAILED"
                and "주문당 상한 우회" in str((t_over[0].get("result") or {}).get("error"))):
            p.append(f"우회 승인 기록 오류 {t_over[0].get('status') if t_over else None}")
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            t_ok, f_ok = ex._send_orders(fb, [KR("005930", "BUY", 14, 70000.0)], "KR", "b.json", "", cap)
        if len(fb.order_calls()) != 1 or fb.order_calls()[0][1]["qty"] != 14:
            p.append(f"상한 안 주문이 그대로 안 나갔다 {fb.order_calls()}")
    report("E3 전송 직전 주문당 상한 — 우회 승인 차단 · 상한 안은 그대로", p)


def test_e4_main_end_to_end_kr():
    """E4 — execute.main --send: 전송 → 체결 확정 → 거래 기록 파일까지(run이 타는 그대로)."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        tf = tmp / "journal" / "trades_261013_kr.json"
        if rc != 0:
            p.append(f"종료코드 {rc}: {out.strip().splitlines()[-1][:120] if out.strip() else ''}")
        if not tf.exists():
            p.append("거래 기록 파일이 없다")
        else:
            r = json.loads(tf.read_text(encoding="utf-8"))
            tr = r.get("trades") or []
            if len(tr) != 1 or tr[0].get("status") != "SENT" or not (tr[0].get("result") or {}).get("order_no"):
                p.append(f"전송 행 {[(t.get('status'), (t.get('result') or {}).get('order_no')) for t in tr]}")
            f = (tr[0].get("fill") or {}) if tr else {}
            if f.get("verdict") != "FILLED" or not f.get("confirmed_at"):
                p.append(f"체결 확정 {f.get('verdict')} · confirmed_at {bool(f.get('confirmed_at'))}")
            if not f.get("settle_date"):
                p.append("결제일 없음")
            if (r.get("fill_confirmed") or {}).get("open_orders") != 0:
                p.append(f"미확정 {(r.get('fill_confirmed') or {}).get('open_orders')}")
            if not (r.get("runs") or [{}])[0].get("approved_orders"):
                p.append("승인 스냅샷(runs[0].approved_orders) 없음")
            if (r.get("reconcile") or {}).get("overfill") != 0:
                p.append(f"승인 대조 {(r.get('reconcile') or {}).get('overfill')}")
        if len(fb.order_calls()) != 1:
            p.append(f"증권사 주문 호출 {len(fb.order_calls())}건")
        if [c for c in fb.calls if c[0].endswith("_modify")]:
            p.append("즉시 체결인데 정정·취소가 나갔다")
    report("E4 execute.main 종단 — 전송·체결 확정·기록(국내)", p)


def test_e5_immediate_fill_settle_date():
    """E5 — 전송 직후 잔고로 바로 체결된 행에도 결제일이 찍힌다(US는 settle_fx 칸도)."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"AAPL": 250.0}, fill_on_place=True)
        ap = approved_file(tmp, "US", [US("AAPL", "BUY", 2, 250.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        tf = tmp / "journal" / "trades_261013_us.json"
        r = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else {}
        f = ((r.get("trades") or [{}])[0].get("fill") or {})
        if rc != 0:
            p.append(f"종료코드 {rc}")
        if f.get("verdict") != "FILLED":
            p.append(f"즉시 체결 판정 {f.get('verdict')}")
        if not f.get("settle_date"):
            p.append("즉시 체결 행에 결제일이 없다")
        if "settle_fx" not in f:
            p.append("미국 행에 settle_fx 칸이 없다(세금 원장이 환율을 못 채운다)")
    report("E5 즉시 체결 행의 결제일·결제일 환율 칸", p)


def test_e6_dry_run_sends_nothing():
    """E6 — --send 없이 부르면 아무것도 안 보내고 기록도 안 남긴다."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc, out = run_main([str(ap), "--stamp", "261013"])
        if rc != 0:
            p.append(f"종료코드 {rc}")
        if fb.calls:
            p.append(f"dry-run에서 증권사 호출 {fb.calls[:2]}")
        if (tmp / "journal" / "trades_261013_kr.json").exists():
            p.append("dry-run이 거래 기록을 남겼다")
    report("E6 dry-run — 증권사 호출 0 · 기록 0", p)


def test_e7_headerless_approved_rejected():
    """E7 — 머리(한도 해시·스키마·계좌 모드)가 없는 승인 파일은 보내지 않는다."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        bare = tmp / "signals" / "approved_bare.json"
        bare.write_text(json.dumps({"orders": [KR("005930", "SELL", 1, 70000.0)], "kind": "stops_intraday",
                                    "generated_at": datetime.now(KST).isoformat()}), encoding="utf-8")
        rc, out = run_main([str(bare), "--send", "--stamp", "261013"])
        if rc != 2:
            p.append(f"종료코드 {rc}(기대 2 — preflight 거부)")
        if fb.calls:
            p.append("머리 없는 승인으로 증권사를 불렀다")
    report("E7 머리 없는 승인 파일 — 거부·호출 0", p)


def test_e8_stops_intraday_header():
    """E8 — 장중 저가 터치 매도(`stops.py --intraday --send`)의 승인 파일이 execute.preflight를 통과한다.

    10-13 01:32: 이 경로가 머리 없는 파일을 써서 MU·NVDA·LRCX·SNDK 4건이 '승인 당시 None'으로 거부됐다.
    """
    p = []
    with env() as tmp:
        thesis = {"id": "kr-test-005930", "ticker": "005930", "market": "KR", "status": "held",
                  "exit_triggers": [{"id": "x1", "check": "price <= 69000", "size_pct": 100}]}
        (tmp / "journal" / "theses.json").write_text(json.dumps({"theses": [thesis]}), encoding="utf-8")
        day = datetime.now(KST).strftime("%Y%m%d")
        FakeBroker(prices={"005930": 70500},
                   positions=[{"ticker": "005930", "qty": 10, "avg_price": 72000, "market": "KR"}],
                   bars={"005930": {"date": day, "open": 70000, "high": 71500, "low": 68000, "close": 70500,
                                    "volume": 1}})
        stops.market_session = _open_session
        stops.held_theses = lambda m: [thesis] if m == "KR" else []
        stops._entry_touches = lambda *a, **k: []
        seen = {}

        def recorder(argv, *a, **k):
            path = Path(argv[2])
            seen["argv"] = list(argv)
            seen["ap"] = json.loads(path.read_text(encoding="utf-8"))
            try:
                ex.preflight(seen["ap"], path, json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8")))
                seen["preflight"] = "ok"
            except rg.Rejection as e:
                seen["preflight"] = f"거부: {e}"
            return 0
        stops.subprocess.call = recorder
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            row = stops.intraday("KR", True, "261013")
        ap = seen.get("ap") or {}
        if not seen:
            p.append(f"전송까지 안 갔다 — {row.get('why')} · fired {row.get('fired')}")
        else:
            if seen.get("preflight") != "ok":
                p.append(f"execute.preflight {str(seen.get('preflight'))[:120]}")
            missing = [k for k in ("schema_version", "limits_sha256", "account_mode", "market") if not ap.get(k)]
            if missing:
                p.append(f"승인 머리 누락 {missing}")
            if ap.get("kind") != "stops_intraday":
                p.append(f"kind {ap.get('kind')}")
            o = (ap.get("orders") or [{}])[0]
            if o.get("ticker") != "005930" or o.get("touch_basis") != "low":
                p.append(f"주문 {o.get('ticker')} · 터치 기준 {o.get('touch_basis')}")
            if "--send" not in seen.get("argv", []):
                p.append("execute를 --send로 부르지 않았다")
    report("E8 장중 터치 매도 승인 파일 — execute.preflight 통과(머리 보존)", p)


def test_e9_finalize_backfills_settle_date():
    """E9 — 거래 기록을 쓸 때마다 빠진 결제일을 채운다. 주간거래 행은 KST 날짜 기준(T+1)."""
    p = []
    with env() as tmp:
        rows = [
            {"ts": "2026-10-13T10:40:00+09:00", "market": "KR", "ticker": "A", "action": "BUY", "qty": 1, "status": "SENT",
             "fill": {"verdict": "FILLED", "filled_qty": 1, "avg_price": 1.0}},
            {"ts": "2026-10-13T23:00:00+09:00", "market": "US", "ticker": "B", "action": "BUY", "qty": 1, "status": "SENT",
             "fill": {"verdict": "FILLED", "delta": 1}},
            {"ts": "2026-10-13T11:00:00+09:00", "market": "US", "ticker": "C", "action": "SELL", "qty": 1, "status": "SENT",
             "session": "daytime", "fill": {"verdict": "FILLED", "filled_qty": 1, "avg_price": 1.0}},
            {"ts": "2026-10-13T10:40:00+09:00", "market": "KR", "ticker": "D", "action": "BUY", "qty": 1, "status": "FAILED",
             "fill": {"verdict": "REJECTED"}},
            {"ts": "2026-10-13T10:40:00+09:00", "market": "KR", "ticker": "E", "action": "BUY", "qty": 1, "status": "SENT",
             "fill": {"verdict": "FILLED", "filled_qty": 1, "settle_date": "2099-01-01"}},
        ]
        rec = {"trades": rows}
        fill.finalize(rec, tmp / "journal" / "trades_261013_kr.json", datetime.now(KST))
        got = {t["ticker"]: (t.get("fill") or {}) for t in rec["trades"]}
        want = {"A": "2026-10-15", "B": "2026-10-14", "C": "2026-10-14"}
        for tk, d in want.items():
            if got[tk].get("settle_date") != d:
                p.append(f"{tk} 결제일 {got[tk].get('settle_date')}(기대 {d})")
        if "settle_fx" not in got["B"] or "settle_fx" not in got["C"] or "settle_fx" in got["A"]:
            p.append("settle_fx 칸은 미국 행에만 있어야 한다")
        if "settle_date" in got["D"]:
            p.append("거부 행에 결제일을 찍었다")
        if got["E"].get("settle_date") != "2099-01-01":
            p.append("이미 있던 결제일을 덮었다")
    report("E9 거래 기록 쓰기마다 결제일 채우기 — 국내 T+2 · 미국 T+1 · 주간거래 KST 기준", p)


def test_e10_tests_line_flags_code_change():
    """E10 — 마지막 완주 뒤 집행 경로 코드가 바뀌면 캡처 [테스트] 줄이 ★로 알린다."""
    p = []
    import run_tests as rt
    tmp = Path(tempfile.mkdtemp(prefix="rtline_"))
    saved = rt.LEDGER
    try:
        if not hasattr(rt, "_src_hashes"):
            p.append("run_tests에 집행 경로 해시 기록이 없다")
        else:
            cur = rt._src_hashes()
            stale = {k: "000000000000" for k in cur}
            ts = datetime.now(KST).isoformat(timespec="seconds")
            rt.LEDGER = tmp / "test_runs.jsonl"
            rows = [{"ts": ts, "file": f, "total": 1, "failed": 0, "completed": True, "why": [], "ok": True,
                     "src": stale} for f in rt.TESTS]
            rt.LEDGER.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            line = rt.last_line()
            if not line.startswith("★") or "바뀜" not in line:
                p.append(f"코드 변경을 못 알렸다: {line[:120]}")
            rt.LEDGER.write_text("\n".join(json.dumps({**r, "src": cur}) for r in rows) + "\n", encoding="utf-8")
            line2 = rt.last_line()
            if line2.startswith("★"):
                p.append(f"바뀐 것이 없는데 ★: {line2[:120]}")
    finally:
        rt.LEDGER = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("E10 [테스트] 줄 — 마지막 완주 뒤 집행 경로 코드 변경을 ★로", p)


def test_e11_audit_reversal_send_settles_ledger():
    """E11 — 감사 소급 되돌림 매도(2026-10-13): 승인 → 전송 → 체결 확정 → 되돌림 원장이 filled로 닫힌다(fill 연결)."""
    import reversals as rv
    p = []
    with env() as tmp:
        jd = tmp / "journal"
        row, _ = rv.add("261013", "KR", "005930", 5, "근거 MISMATCH(테스트)", jdir=jd)
        fb = FakeBroker(prices={"005930": 70000},
                        positions=[{"ticker": "005930", "qty": 5, "avg_price": 72000, "market": "KR"}])
        ap = approved_file(tmp, "KR", [KR("005930", "SELL", 5, 70000.0, source="audit_reversal", full_exit=True,
                                          trigger_id="audit_reversal", reversal_id=row["id"])],
                           name="approved_261013_kr_stops_1500.json")
        rc, out = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        tf = jd / "trades_261013_kr.json"
        tr = (json.loads(tf.read_text(encoding="utf-8")).get("trades") or []) if tf.exists() else []
        if rc != 0:
            p.append(f"종료코드 {rc}: {out.strip().splitlines()[-1][:120] if out.strip() else ''}")
        if [(t.get("source"), (t.get("fill") or {}).get("verdict")) for t in tr] != [("audit_reversal", "FILLED")]:
            p.append(f"거래 행 {[(t.get('source'), (t.get('fill') or {}).get('verdict')) for t in tr]}")
        st = [r.get("status") for r in rv.rows(jd)]
        if st != ["filled"]:
            p.append(f"되돌림 원장 상태 {st}(filled여야) — fill이 원장을 닫지 않았다")
        if len(fb.order_calls()) != 1:
            p.append(f"증권사 주문 호출 {len(fb.order_calls())}건")
    report("E11 감사 소급 매도 — 전송·체결 확정·되돌림 원장 filled", p)


for _fn in (test_e1_send_loop_kr, test_e2_send_loop_us_daytime, test_e3_order_cap_at_send, test_e4_main_end_to_end_kr,
            test_e5_immediate_fill_settle_date, test_e6_dry_run_sends_nothing, test_e7_headerless_approved_rejected,
            test_e8_stops_intraday_header, test_e9_finalize_backfills_settle_date, test_e10_tests_line_flags_code_change,
            test_e11_audit_reversal_send_settles_ledger):
    _safe(_fn)

print()
n_fail = sum(1 for s, _, _ in results if s == FAIL)
_STATE["done"] = True
print(f"총 {len(results)}건 · 통과 {len(results) - n_fail} · 실패 {n_fail}")
print(f"TEST-SUMMARY file=test_execute_path.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
if n_fail:
    for s, name, why in results:
        if s == FAIL:
            print(f"  FAIL {name}: {why}")
sys.exit(1 if n_fail else 0)
