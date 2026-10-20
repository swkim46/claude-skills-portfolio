#!/usr/bin/env python3
"""
집행 경로 종단 테스트 — 가짜 증권사로 `execute`·`stops`·`fill`을 **전송부터 체결 확정까지** 끝까지 돌린다.

왜(2026-10-13 사고): 10-12 20:56 코드 편집이 `execute._send_orders`의 전송 줄(`res = place(client, o)`)을 지웠다.
문법 검사와 `test_risk_guard.py` 130건이 편집 24분 뒤에도 통과했다 — 그 함수를 부르는 테스트가 하나도 없었다.
그날 밤 예약 run의 `--send`가 전부 브로커 호출 전에 UnboundLocalError로 죽었다(주문 0건). 그래서 여기서는 함수를
따로 부르지 않고 **실제 run이 타는 경로**(execute.main · _send_orders · fill.confirm · stops.intraday)를 그대로 돈다.

규칙: 네트워크·.env·실제 `config/limits.json`·실제 `journal/`·실제 저장소(`journal/trading.db`)를 건드리지 않는다.
원장(논지·감시 목록·감사 소급)은 케이스마다 `testkit.db_case()`로 연 빈 임시 저장소에 심고, 심은 행이 보이는지 먼저 확인한다
(심기가 조용히 실패한 채 통과하는 테스트가 가장 나쁘다). 모듈 전역은 케이스마다 `finally`로 되돌린다.
`execute.py`를 서브프로세스로 띄우지 않는다(실제 설정을 읽는다) — `stops`의 전송은 기록기로 받는다.

    python3 test_execute_path.py          # run_tests.py가 부른다
"""
import contextlib
import io
import json
import os
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
import db                          # noqa: E402
import testkit                     # noqa: E402

WATCHLIST_SRC = HERE / "config" / "watchlist.json"     # 감시 목록 렌더본 — 읽기만 하고 임시 저장소에 심는다

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

    def __init__(self, prices=None, positions=None, bars=None, fill_on_place=False, cash=100_000_000.0,
                 fail_place=None, crash_on=None, list_mode="ok"):
        self.svr, self.mode, self.kr_excg, self.read_only = "paper", "paper", "KRX", False
        self.calls, self.orders = [], {}
        # ★ 실패 훅(2026-10-19 · R01·R02) — 종목별 한 번만 걸린다.
        #   fail_place={tk: ("raise", exc)}            : 접수하지 않고 예외(거절·미전송·접수 전 끊김)
        #   fail_place={tk: ("accept_then_raise", exc)}: 접수한 뒤 응답을 잃는다(타임아웃 등)
        #   fail_place={tk: ("no_odno",)}               : 접수했는데 주문번호 없는 성공 응답
        #   crash_on={tk: BaseException}                : 접수한 뒤 프로세스가 죽는다
        #   list_mode: 당일 주문 목록 — ok(접수한 주문 그대로·없으면 확정 빈 답) · empty_kiok0560(확정 빈 답) ·
        #              empty_plain(빈 답이지만 확정 아님) · raise(조회 실패)
        self.fail_place = dict(fail_place or {})
        self.crash_on = dict(crash_on or {})
        self.list_mode = list_mode
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
        hook = self.fail_place.pop(ticker, None)
        self.calls.append((kind, {"ticker": ticker, "side": side, "qty": qty, "price": price, **extra}))
        if hook and hook[0] == "raise":
            raise hook[1]
        self._n += 1
        no = f"{self._n:010d}"
        self.orders[no] = {"market": market, "ticker": ticker, "side": side, "qty": int(qty), "price": float(price),
                           "applied": False, "ts": datetime.now(KST).isoformat()}
        if self.fill_on_place:
            self._apply(no)
        if hook and hook[0] == "accept_then_raise":
            raise hook[1]
        if ticker in self.crash_on:
            raise self.crash_on.pop(ticker)
        if hook and hook[0] == "no_odno":
            return {"order_no": "", "order_time": "", "orgno": "", "raw": {"rt_cd": "0", "output": {}}}
        return {"order_no": no, "order_time": "000000", "orgno": "00950", "excg": extra.get("excg"),
                "raw": {"rt_cd": "0"}}

    def accepted(self, ticker=None):
        """증권사가 **접수한** 주문 수(호출 수가 아니라) — 이중 접수 검사용."""
        return sum(1 for o in self.orders.values() if ticker is None or o["ticker"] == ticker)

    def _orders_list(self, market, ticker=""):
        if self.list_mode == "raise":
            raise kc.KisError("당일 주문 목록 조회 실패(테스트)")
        rows = [{"order_no": no, "orgn_order_no": "0", "ticker": o["ticker"], "side": o["side"], "ord_qty": o["qty"],
                 "filled_qty": o["qty"] if o["applied"] else 0, "remain_qty": 0 if o["applied"] else o["qty"],
                 "avg_price": o["price"] if o["applied"] else 0.0, "price": o["price"], "ord_at": o.get("ts"),
                 "is_original": True, "cancelled": False, "rejected": False, "orgno": "00950", "excg": "", "raw": {}}
                for no, o in self.orders.items() if o["market"] == market and (not ticker or o["ticker"] == ticker)]
        if self.list_mode in ("empty_kiok0560", "empty_plain"):
            rows = []
        return {"ok": True, "complete": True, "rows": rows,
                "authoritative_empty": (not rows) and self.list_mode in ("ok", "empty_kiok0560"),
                "msg_cd": "KIOK0560" if not rows else ""}

    def domestic_orders(self, ticker="", date=""):
        return self._orders_list("KR", ticker)

    def overseas_orders(self, ticker="", excd="", date=""):
        return self._orders_list("US", ticker)

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
    """임시 config·journal·signals와 **빈 임시 저장소**로 모듈 전역을 갈아끼운다. 실제 설정·원장·저장소는 읽지도 쓰지도 않는다.
    저장소에는 감시 목록만 심는다(risk_guard가 유니버스를 못 재면 run 전체를 거부한다) — 논지·감사 소급은 케이스가 심는다."""
    tmp = Path(tempfile.mkdtemp(prefix="exectest_"))
    try:
        with testkit.db_case(tmp / "db"):
            testkit.seed(watchlist=json.loads(WATCHLIST_SRC.read_text(encoding="utf-8")))
            if not db.companies("KR"):
                raise AssertionError("감시 목록 심기 실패 — 임시 저장소에 KR 감시 목록 행이 없다")
            with _env_paths(tmp) as t:
                yield t
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def _env_paths(tmp: Path):
    cfg = tmp / "config"
    cfg.mkdir()
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim.update({"account_mode": "paper", "real_trading_enabled": False, "halt_all": False, "scenario_cond_required": False})
    (cfg / "limits.json").write_text(json.dumps(lim, ensure_ascii=False), encoding="utf-8")
    for d in ("journal", "signals", "data", "evid"):
        (tmp / d).mkdir()
    saved = {
        "rg": (rg.LIMITS_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR),
        "ex": (ex.JOURNAL_DIR, ex.KisClient, ex.market_session, ex.us_daytime_session, ex._send_cfg),
        "fill": (fill.JOURNAL_DIR, fill.LIMITS_PATH, fill.confirm),
        "stops": (stops.KisClient, stops.market_session, stops.EVID, stops.SIGNALS, stops.held_theses,
                  stops._entry_touches, stops.subprocess.call),
        "argv": list(sys.argv),
        "temp": os.environ.get("TEMP"),
    }
    os.environ["TEMP"] = str(tmp / "gate")         # 게이트 통과 기록 폴더 격리(실제 ~/claude_stepgate를 안 건드린다)
    rg.LIMITS_PATH, rg.KILL_PATH = cfg / "limits.json", cfg / "KILL"
    rg.SIGNALS_DIR = tmp / "signals"
    rg.DATA_DIR, rg.JOURNAL_DIR = tmp / "data", tmp / "journal"       # JOURNAL_DIR = 그날 거래 기록(trades_*.json) 자리
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
        (rg.LIMITS_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR) = saved["rg"]
        (ex.JOURNAL_DIR, ex.KisClient, ex.market_session, ex.us_daytime_session, ex._send_cfg) = saved["ex"]
        (fill.JOURNAL_DIR, fill.LIMITS_PATH, fill.confirm) = saved["fill"]
        (stops.KisClient, stops.market_session, stops.EVID, stops.SIGNALS, stops.held_theses,
         stops._entry_touches, stops.subprocess.call) = saved["stops"]
        sys.argv = saved["argv"]
        if saved["temp"] is None:
            os.environ.pop("TEMP", None)
        else:
            os.environ["TEMP"] = saved["temp"]


def _stepgate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sg_test", str(ex.STEPGATE_PY))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_pass(path: Path, cp: str = "dispatch") -> dict:
    """전송 직전 점검(judge) 통과를 흉내 — **실제 게이트 엔진**으로 이 파일 바이트의 해시를 서명해 통과 기록에 남긴다."""
    return _stepgate().append_pass_record("trade-run", cp, "d_test", {"approved": str(path), "deliverable": str(path)})


def approved_file(tmp: Path, market: str, orders: list, name: str = None, gate: bool = True, **hdr) -> Path:
    """risk_guard가 쓰는 머리를 그대로 갖춘 승인 파일. `gate=True`(기본)면 이 파일의 dispatch 통과 기록도 남긴다(R03 —
    집행기는 통과 기록 없이 본 승인을 보내지 않는다). 관문 자체를 시험할 때만 `gate=False`."""
    body = {"schema_version": rg.SCHEMA_VERSION, "market": market, "generated_at": datetime.now(KST).isoformat(),
            "signal_ref": None, "snapshot_ref": "test", "limits_sha256": rg.sha256_of(rg.LIMITS_PATH),
            "orders": orders, "rejected": [], "account_mode": "paper", "order_cap": None, **hdr}
    p = tmp / "signals" / (name or f"approved_261013_{market.lower()}.json")
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    if gate and body.get("discipline_only") is not True:
        gate_pass(p)
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
        if not (t_over and t_over[0].get("status") == "UNSENT"
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
        testkit.seed(theses=[thesis])
        if not db.thesis_get("kr-test-005930") or [t.get("id") for t in stops.held_theses("KR")] != ["kr-test-005930"]:
            p.append("논지 심기 실패 — 저장소에서 held 논지가 안 보인다")
        day = datetime.now(KST).strftime("%Y%m%d")
        FakeBroker(prices={"005930": 70500},
                   positions=[{"ticker": "005930", "qty": 10, "avg_price": 72000, "market": "KR"}],
                   bars={"005930": {"date": day, "open": 70000, "high": 71500, "low": 68000, "close": 70500,
                                    "volume": 1}})
        stops.market_session = _open_session
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
        if not db.bars("KR", "005930"):
            p.append("장중 터치 일봉이 저장소 경유(db.domestic_daily)로 오지 않았다 — mk_bar에 행이 없다")
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
    """E10 — 마지막 완주 뒤 집행 경로 코드가 바뀌면 캡처 [테스트] 줄이 ★로 알린다(테스트 기록은 저장소 op_test_run)."""
    p = []
    import run_tests as rt
    if not hasattr(rt, "_src_hashes"):
        p.append("run_tests에 집행 경로 해시 기록이 없다")
    else:
        cur = rt._src_hashes()
        stale = {k: "000000000000" for k in cur}
        ts = datetime.now(KST).isoformat(timespec="seconds")
        rows = [{"ts": ts, "file": f, "total": 1, "failed": 0, "completed": True, "why": [], "ok": True,
                 "src": stale} for f in rt.TESTS]
        with testkit.db_case():                   # 테스트 기록은 추가만 하는 표 — 경우마다 빈 임시 저장소에 심는다
            testkit.seed(logs={"test_runs": rows})
            if len(db.log_rows("test_runs")) != len(rows):
                p.append("심기 실패 — 테스트 기록이 저장소에 안 보인다")
            line = rt.last_line()
            if not line.startswith("★") or "바뀜" not in line:
                p.append(f"코드 변경을 못 알렸다: {line[:120]}")
        with testkit.db_case():
            testkit.seed(logs={"test_runs": [{**r, "src": cur} for r in rows]})
            if len(db.log_rows("test_runs")) != len(rows):
                p.append("심기 실패 — 테스트 기록이 저장소에 안 보인다")
            line2 = rt.last_line()
            if line2.startswith("★"):
                p.append(f"바뀐 것이 없는데 ★: {line2[:120]}")
    report("E10 [테스트] 줄 — 마지막 완주 뒤 집행 경로 코드 변경을 ★로", p)


def test_e11_audit_reversal_send_settles_ledger():
    """E11 — 감사 소급 되돌림 매도(2026-10-13): 승인 → 전송 → 체결 확정 → 되돌림 기록(저장소 ex_reversal_event)이 filled로 닫힌다."""
    p = []
    with env() as tmp:
        jd = tmp / "journal"
        row = {"id": "REV-261013-kr-1", "stamp": "261013", "market": "KR", "ticker": "005930", "name": "",
               "kind": "buy_unsupported", "qty": 5, "filled_qty": 5, "why": "근거 MISMATCH(테스트)", "round": 0,
               "fc_rows": ["ev_verdict:1"], "buy_ref": "approved_261013_kr.json", "thesis_id": "", "status": "pending"}
        testkit.seed(reversals=[row])
        if [r.get("id") for r in rg.reversals_pending("KR")] != ["REV-261013-kr-1"]:
            p.append("감사 소급 행 심기 실패 — 저장소 대기 행이 안 보인다")
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
        st = [r.get("status") for r in db.reversals_all()]
        if st != ["filled"]:
            p.append(f"되돌림 기록 상태 {st}(filled여야) — fill이 기록을 닫지 않았다")
        last = (db.reversals_all() or [{}])[0]
        if last.get("sold_qty") != 5 or last.get("qty") != 0 or not last.get("settled_orders"):
            p.append(f"되돌림 기록 체결 칸 sold_qty {last.get('sold_qty')} · qty {last.get('qty')} · 주문 {last.get('settled_orders')}")
        # 같은 거래 기록으로 fill을 다시 불러도(멱등) 행이 늘지 않는다
        n_ev = len(db.log_rows("audit_reversals"))
        fill.confirm(tf, client=fb, max_sec=10)
        if len(db.log_rows("audit_reversals")) != n_ev:
            p.append("fill 재호출이 같은 체결을 되돌림 기록에 또 적었다(멱등 아님)")
        if len(fb.order_calls()) != 1:
            p.append(f"증권사 주문 호출 {len(fb.order_calls())}건")
    report("E11 감사 소급 매도 — 전송·체결 확정·되돌림 기록 filled", p)


def test_e12_send_fill_import():
    """E12 — 전송 → 체결 확정 → `db.import_run_artifacts`(run 끝 close가 부른다)가 승인·주문·체결을 집행 표로 옮긴다(2026-10-17).

    거래 기록 파일(trades_*.json)은 그대로 남고, 저장소의 `ex_approval`·`ex_order`·`ex_fill`은 그 파일에서 온다 —
    주문번호·체결 수량이 파일과 같아야 하고, 다시 불러도(멱등) 행이 늘지 않아야 한다."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000, "000660": 200000},
                        positions=[{"ticker": "000660", "qty": 4, "avg_price": 190000, "market": "KR"}])
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0), KR("000660", "SELL", 2, 200000.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc != 0:
            p.append(f"종료코드 {rc}: {out.strip().splitlines()[-1][:120] if out.strip() else ''}")
        tf = tmp / "journal" / "trades_261013_kr.json"
        tr = (json.loads(tf.read_text(encoding="utf-8")).get("trades") or []) if tf.exists() else []
        if [(t.get("ticker"), (t.get("fill") or {}).get("verdict")) for t in tr] != [("005930", "FILLED"), ("000660", "FILLED")]:
            p.append(f"거래 기록 {[(t.get('ticker'), (t.get('fill') or {}).get('verdict')) for t in tr]}")
        n = db.import_run_artifacts("261013", "kr", here=tmp)
        rid = db.run_id("261013", "kr")
        orders = db.q("SELECT * FROM ex_order WHERE run_id=? ORDER BY id", (rid,))
        fills = db.q("SELECT f.*, o.ticker AS o_ticker FROM ex_fill f JOIN ex_order o ON o.id = f.order_id "
                     "WHERE f.run_id=? ORDER BY f.id", (rid,))
        apprs = db.q("SELECT * FROM ex_approval WHERE run_id=? ORDER BY id", (rid,))
        if (n.get("orders"), n.get("fills"), n.get("approvals")) != (2, 2, 2):
            p.append(f"옮긴 건수 주문 {n.get('orders')} · 체결 {n.get('fills')} · 승인 {n.get('approvals')}(기대 2·2·2)")
        want_no = {t.get("ticker"): (t.get("result") or {}).get("order_no") for t in tr}
        got = [(o["ticker"], o["side"], o["qty"], o["order_no"]) for o in orders]
        if got != [("005930", "BUY", 3, want_no.get("005930")), ("000660", "SELL", 2, want_no.get("000660"))] \
                or not all(o["order_no"] for o in orders):
            p.append(f"ex_order {got} (파일 주문번호 {want_no})")
        if [(f["o_ticker"], f["verdict"], f["filled_qty"]) for f in fills] != [("005930", "FILLED", 3), ("000660", "FILLED", 2)]:
            p.append(f"ex_fill {[(f['o_ticker'], f['verdict'], f['filled_qty']) for f in fills]}")
        if not all(f["settle_date"] for f in fills):
            p.append("ex_fill에 결제일이 없다")
        if [(a["ticker"], a["action"], a["qty"]) for a in apprs] != [("005930", "BUY", 3), ("000660", "SELL", 2)]:
            p.append(f"ex_approval {[(a['ticker'], a['action'], a['qty']) for a in apprs]}")
        # 주문↔승인 연결(v7 · R10) — 집행기가 적은 승인 해시+순번으로 recorded · 의도 키가 저장소에도 실린다
        if [(o["approval_id"], o["approval_link"]) for o in orders] != [(a["id"], "recorded") for a in apprs] \
                or not all(o["intent_key"] and o["approved_sha"] for o in orders):
            p.append(f"승인 연결 {[(o['approval_id'], o['approval_link'], o['intent_key']) for o in orders]}")
        n2 = db.import_run_artifacts("261013", "kr", here=tmp)
        if n2.get("orders") or n2.get("fills") or n2.get("approvals") \
                or len(db.q("SELECT 1 FROM ex_order WHERE run_id=?", (rid,))) != 2:
            p.append(f"두 번째 옮기기가 행을 늘렸다(멱등 아님): {n2}")
        if len(fb.order_calls()) != 2:
            p.append(f"증권사 주문 호출 {len(fb.order_calls())}건")
    report("E12 전송 → 체결 확정 → 저장소 집행 표(ex_approval·ex_order·ex_fill) 이전 · 멱등", p)


def test_e13_thesis_read_failure_after_send():
    """전송 뒤 논지 원장을 못 읽어도 거래 기록 쓰기까지 간다 — 예외로 멈추면 그날 기록이 없어 같은 승인 재실행이
    이미 나간 주문을 다시 보낸다. 실패는 숨기지 않고 체결 티커를 '원장 읽기 실패' 미동기로 올린다 · 테스트 문지기는 그대로 올린다."""
    import sqlite3
    p = []
    fills = {"005930": {"verdict": "FILLED"}, "000660": {"verdict": "UNFILLED"}}
    real = ex.db.theses_all

    def boom():
        raise sqlite3.OperationalError("database is locked")
    ex.db.theses_all = boom
    try:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            got = ex._thesis_unsynced(fills)
        if got != [{"ticker": "005930", "thesis_status": ["(논지 원장 읽기 실패)"]}]:
            p.append(f"원장 읽기 실패 → 미동기: {got}")
        if "논지 원장 읽기 실패" not in err.getvalue():
            p.append("실패 경고가 없다")

        def guard():
            raise ex.db.TestGuard("임시 저장소 없음")
        ex.db.theses_all = guard
        try:
            ex._thesis_unsynced(fills)
            p.append("TestGuard를 삼켰다")
        except ex.db.TestGuard:
            pass
    finally:
        ex.db.theses_all = real
    report("E13 전송 뒤 논지 원장 읽기 실패 — 기록은 계속 · 체결 티커는 '원장 읽기 실패' 미동기 · 테스트 문지기는 올린다", p)


def _trades(tmp, mk="kr"):
    tf = tmp / "journal" / f"trades_261013_{mk}.json"
    return (json.loads(tf.read_text(encoding="utf-8")).get("trades") or []) if tf.exists() else []


def test_e14_response_lost_not_resent():
    """E14 — 접수 뒤 응답 유실(R01 · 리뷰 재현 accepted_but_response_lost의 올바른 동작): 첫 행은 UNKNOWN(FAILED 아님) ·
    같은 승인 재실행은 **보내지 않는다** · 당일 주문 목록이 그 주문을 행에 붙인다(SENT) · 체결 확정 FILLED · 접수 1건."""
    p = []
    with env() as tmp:
        lost = kc.KisError("network error on POST /order-cash: TimeoutError after broker acceptance", kind="unknown")
        fb = FakeBroker(prices={"005930": 70000}, fail_place={"005930": ("accept_then_raise", lost)})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc1, out1 = run_main([str(ap), "--send", "--stamp", "261013", "--no-wait"])
        tr = _trades(tmp)
        if [t.get("status") for t in tr] != ["UNKNOWN"]:
            p.append(f"첫 상태 {[t.get('status') for t in tr]}(기대 UNKNOWN)")
        if tr and (tr[0].get("result") or {}).get("kind") != "unknown":
            p.append(f"result.kind {(tr[0].get('result') or {}).get('kind')}")
        if tr and (tr[0].get("fill") or {}).get("qty_before") is None:
            p.append("접수 불명 행에 전송 전 보유(qty_before)가 없다")
        if tr and (tr[0].get("fill") or {}).get("verdict"):
            p.append(f"접수 불명인데 즉시 판정 {(tr[0].get('fill') or {}).get('verdict')}")
        rc2, out2 = run_main([str(ap), "--send", "--stamp", "261013", "--no-wait"])
        if len(fb.order_calls()) != 1:
            p.append(f"재실행이 다시 보냈다 — 호출 {len(fb.order_calls())}(기대 1)")
        rc3, out3 = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        tr = _trades(tmp)
        r0 = (tr[0].get("result") or {}) if tr else {}
        if [t.get("status") for t in tr] != ["SENT"] or ((r0.get("resolved") or {}).get("how") != "day-list"):
            p.append(f"당일 목록 채택 실패 — {[t.get('status') for t in tr]} · {r0.get('resolved')}")
        if tr and (tr[0].get("fill") or {}).get("verdict") != "FILLED":
            p.append(f"채택 뒤 체결 확정 {(tr[0].get('fill') or {}).get('verdict')}")
        if fb.accepted("005930") != 1 or len(fb.order_calls()) != 1:
            p.append(f"이중 접수 — 접수 {fb.accepted('005930')} · 호출 {len(fb.order_calls())}")
    report("E14 접수 뒤 응답 유실 → UNKNOWN · 재전송 0 · 당일 목록으로 SENT 채택 · FILLED · 접수 1", p)


def test_e14b_explicit_rejection_retryable():
    """E14b — 증권사의 분명한 거절(REJECTED)은 접수 0이라 같은 승인으로 다시 보낼 수 있다(시도 번호 2 · 의도 키가 다르다)."""
    p = []
    with env() as tmp:
        rej = kc.KisError("국내 주문(BUY 005930 x3) 실패 [40580000] 장운영시간이 아닙니다", kind="rejected")
        fb = FakeBroker(prices={"005930": 70000}, fail_place={"005930": ("raise", rej)})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        run_main([str(ap), "--send", "--stamp", "261013", "--no-wait"])
        tr = _trades(tmp)
        if [(t.get("status"), (t.get("fill") or {}).get("verdict")) for t in tr] != [("REJECTED", "REJECTED")]:
            p.append(f"거절 기록 {[(t.get('status'), (t.get('fill') or {}).get('verdict')) for t in tr]}")
        run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        tr = _trades(tmp)
        if len(fb.order_calls()) != 2 or fb.accepted() != 1:
            p.append(f"재시도 호출 {len(fb.order_calls())}(기대 2) · 접수 {fb.accepted()}(기대 1)")
        keys = [t.get("intent_key") for t in tr]
        if len(tr) != 2 or len(set(keys)) != 2 or [t.get("attempt") for t in tr] != [1, 2]:
            p.append(f"시도 번호·의도 키 {[(t.get('attempt'), t.get('intent_key')) for t in tr]}")
    report("E14b 증권사 거절 → REJECTED · 같은 승인 재시도 가능(시도 2 · 접수 1)", p)


def test_e14c_no_order_no_and_unsent():
    """E14c — 주문번호 없는 성공 응답은 UNKNOWN(목록이 붙인다) · 요청이 안 닿은 오류(unsent)는 UNSENT(다시 보낼 수 있다)."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000, "000660": 200000},
                        fail_place={"005930": ("no_odno",),
                                    "000660": ("raise", kc.KisError("network error: Connection refused", kind="unsent"))})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0), KR("000660", "BUY", 1, 200000.0)])
        run_main([str(ap), "--send", "--stamp", "261013", "--no-wait"])
        st = {t["ticker"]: t.get("status") for t in _trades(tmp)}
        if st != {"005930": "UNKNOWN", "000660": "UNSENT"}:
            p.append(f"상태 {st}")
        run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        tr = _trades(tmp)
        by = {}
        for t in tr:
            by.setdefault(t["ticker"], []).append(t.get("status"))
        if by.get("005930") != ["SENT"]:
            p.append(f"주문번호 없는 행 채택 {by.get('005930')}")
        if by.get("000660") != ["UNSENT", "SENT"]:
            p.append(f"미전송 재시도 {by.get('000660')}")
        if fb.accepted("005930") != 1 or fb.accepted("000660") != 1:
            p.append(f"접수 005930 {fb.accepted('005930')} · 000660 {fb.accepted('000660')}")
    report("E14c 주문번호 없는 성공 → UNKNOWN → 채택 · 요청 미도달 → UNSENT → 재시도", p)


def test_e14d_not_placed_resolved_unsent():
    """E14d — 응답 유실인데 증권사 목록이 '없다'(확정)를 두 번 주고 잔고도 그대로면 UNSENT로 내리고 그 뒤 재실행이 한 번 보낸다.
    목록이 확정이 아니면(empty_plain) UNKNOWN을 유지한다(다시 보내지 않는다)."""
    p = []
    saved_ms = fill.market_session
    fill.market_session = _open_session            # 장중으로 고정 — 실제 시각이 마감 뒤면 '마감 뒤 만료'가 먼저 닫는다
    try:
        _e14d_body(p)
    finally:
        fill.market_session = saved_ms
    report("E14d 응답 유실 + 확정 빈 목록 2회 + 잔고 불변 → UNSENT → 재전송 1 · 확정 아님은 UNKNOWN 유지", p)


def _e14d_body(p):
    with env() as tmp:
        lost = kc.KisError("network error on POST: TimeoutError", kind="unknown")
        fb = FakeBroker(prices={"005930": 70000}, fail_place={"005930": ("raise", lost)}, list_mode="empty_plain")
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        run_main([str(ap), "--send", "--stamp", "261013", "--no-wait"])
        tf = tmp / "journal" / "trades_261013_kr.json"
        fill.confirm(tf, client=fb, max_sec=5)
        if [t.get("status") for t in _trades(tmp)] != ["UNKNOWN"]:
            p.append(f"확정 아닌 빈 목록에서 상태가 바뀌었다 {[t.get('status') for t in _trades(tmp)]}")
        fb.list_mode = "empty_kiok0560"
        fill.confirm(tf, client=fb, max_sec=5)          # 확정 빈 답 1회 — 아직 내리지 않는다(접수 직후 반영 지연 대비)
        if [t.get("status") for t in _trades(tmp)] != ["UNKNOWN"]:
            p.append(f"확정 빈 답 1회 만에 상태가 바뀌었다 {[t.get('status') for t in _trades(tmp)]}")
        fill.confirm(tf, client=fb, max_sec=5)          # 다른 폴링의 두 번째 확정 빈 답 → 미접수 확인
        tr = _trades(tmp)
        if [t.get("status") for t in tr] != ["UNSENT"] or "미접수 확인" not in str((tr[0].get("fill") or {}).get("note")):
            p.append(f"확정 빈 목록 2회 → UNSENT 아님 {[(t.get('status'), (t.get('fill') or {}).get('note')) for t in tr]}")
        run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        if fb.accepted() != 1 or len(fb.order_calls()) != 2:
            p.append(f"미접수 확인 뒤 재전송 — 접수 {fb.accepted()} · 호출 {len(fb.order_calls())}")


def test_e15_crash_after_first_order():
    """E15 — 첫 주문 접수 뒤 둘째 주문에서 프로세스가 죽어도(R02 · 리뷰 재현 crash_after_first_order의 올바른 동작) 기록이 남는다:
    첫 행 SENT · 둘째 행 PENDING · runs[0] 승인 스냅샷. 재실행은 아무것도 다시 보내지 않고 PENDING을 목록으로 붙인다(종목당 접수 1)."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000, "000660": 200000}, crash_on={"000660": SystemExit(9)})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0), KR("000660", "BUY", 1, 200000.0)])
        try:
            run_main([str(ap), "--send", "--stamp", "261013", "--no-wait"])
            p.append("중단이 일어나지 않았다(훅 실패)")
        except SystemExit:
            pass
        tf = tmp / "journal" / "trades_261013_kr.json"
        if not tf.exists():
            p.append("중단 뒤 거래 기록이 없다")
        st = {t["ticker"]: t.get("status") for t in _trades(tmp)}
        if st != {"005930": "SENT", "000660": "PENDING"}:
            p.append(f"중단 뒤 상태 {st}(기대 005930 SENT · 000660 PENDING)")
        rec = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else {}
        if not (rec.get("runs") or [{}])[0].get("approved_orders"):
            p.append("보내기 전 승인 스냅샷(runs[0].approved_orders)이 없다")
        if (tmp / "journal" / ".trades_261013_kr.lock").exists():
            p.append("중단 뒤 잠금 파일이 남았다")
        run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        st2 = {t["ticker"]: (t.get("status"), (t.get("fill") or {}).get("verdict")) for t in _trades(tmp)}
        if st2 != {"005930": ("SENT", "FILLED"), "000660": ("SENT", "FILLED")}:
            p.append(f"재실행 뒤 {st2}")
        if fb.accepted("005930") != 1 or fb.accepted("000660") != 1 or len(fb.order_calls()) != 2:
            p.append(f"재실행 중복 — 005930 {fb.accepted('005930')} · 000660 {fb.accepted('000660')} · 호출 {len(fb.order_calls())}")
    report("E15 첫 주문 뒤 중단 → 기록 존재(SENT·PENDING·승인 스냅샷) · 재실행 중복 0 · PENDING 채택", p)


def test_e15b_corrupt_day_record_stops():
    """E15b — 그날 거래 기록이 깨졌으면 보내지 않는다(종료 2 · 호출 0 · 원본 그대로) — 빈 기록으로 읽고 다시 보내던 길(R02)."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        tf = tmp / "journal" / "trades_261013_kr.json"
        tf.write_text('{"trades": [', encoding="utf-8")
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013"])
        if rc != 2 or fb.calls:
            p.append(f"종료코드 {rc} · 호출 {len(fb.calls)}")
        if tf.read_text(encoding="utf-8") != '{"trades": [' or list((tmp / "journal").glob("*.corrupt_*")):
            p.append("깨진 원본을 바꾸거나 옮겼다")
    report("E15b 깨진 그날 기록 → 종료 2 · 호출 0 · 원본 보존", p)


def test_e15c_day_lock():
    """E15c — 다른 살아 있는 프로세스가 그날 기록을 쓰고 있으면 보내지 않는다(종료 5) · 죽은 프로세스의 잠금은 치우고 진행한다."""
    import os as _os
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        lp = tmp / "journal" / ".trades_261013_kr.lock"
        lp.write_text(json.dumps({"pid": _os.getppid(), "ts": datetime.now(KST).isoformat(), "cmd": "fill.py (테스트)"}),
                      encoding="utf-8")
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013"])
        if rc != 5 or fb.calls:
            p.append(f"살아 있는 잠금 — 종료코드 {rc} · 호출 {len(fb.calls)}(기대 5 · 0)")
        lp.write_text(json.dumps({"pid": 999999, "ts": "2026-10-13T09:00:00+09:00", "cmd": "죽은 프로세스"}), encoding="utf-8")
        rc2, out2 = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc2 != 0 or fb.accepted() != 1:
            p.append(f"죽은 잠금 회수 실패 — 종료코드 {rc2} · 접수 {fb.accepted()}")
        if lp.exists():
            p.append("끝난 뒤 잠금 파일이 남았다")
    report("E15c 그날 기록 잠금 — 살아 있는 쓰기면 종료 5 · 죽은 잠금은 회수", p)


def test_e18_risk_reducing_sell_not_skipped():
    """E18 — 위험 축소 매도(손절·추적·소급)는 전송 직전 시세가 2% 넘게 빠져도 보낸다(사용자 결정 2026-10-19 · R08 ·
    리뷰 재현 stop_sell_price_drift의 올바른 동작). 모델 매도·자금 조달·이익실현 다리·매수는 ±2% 그대로."""
    p = []
    with env():
        fb = FakeBroker(prices={"005930": 95000})
        stop = KR("005930", "SELL", 1, 100000, source="discipline", leg_kind="stop", risk_reducing=True)
        px, q, why, skip = ex.refresh_limit(fb, stop, {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 0})
        if skip or px != 95000 or q != 1:
            p.append(f"손절 매도 {px} x{q} skip={skip}")
        legacy = KR("005930", "SELL", 1, 100000, source="audit_reversal")          # 도장 전 승인 — 규칙으로 판정
        if ex.refresh_limit(fb, legacy, {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 0})[3]:
            p.append("도장 없는 소급 매도를 막았다")
        for o in (KR("005930", "SELL", 1, 100000, source="llm"), KR("005930", "SELL", 1, 100000, source="funding"),
                  KR("005930", "SELL", 1, 100000, source="discipline", leg_kind="target")):
            if not ex.refresh_limit(fb, o, {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 0})[3]:
                p.append(f"{o['source']}/{o.get('leg_kind')} 매도가 이탈 한도를 넘어 나갔다")
        fb2 = FakeBroker(prices={"005930": 105000})
        if not ex.refresh_limit(fb2, KR("005930", "BUY", 1, 100000), {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 0})[3]:
            p.append("매수가 +5% 이탈인데 나갔다")
        truth = [rg.is_risk_reducing(o) for o in (
            {"action": "SELL", "source": "discipline"}, {"action": "SELL", "source": "discipline", "leg_kind": "trail"},
            {"action": "SELL", "source": "discipline", "leg_kind": "target"}, {"action": "SELL", "source": "audit_reversal"},
            {"action": "SELL", "source": "llm", "full_exit": True}, {"action": "SELL", "source": "funding"},
            {"action": "BUY", "source": "discipline"})]
        if truth != [True, True, False, True, False, False, False]:
            p.append(f"is_risk_reducing 진리표 {truth}")
        if fill.chase_price("SELL", "KR", 95000, 100000, 100000, 0.5, floor=False) != 95000 \
                or fill.reorder_price("SELL", "KR", 95000, 100000, 1.0, floor=False) != 95000 \
                or fill.reorder_price("SELL", "KR", 95000, 100000, 1.0) is not None:
            p.append("체결 추적 하한이 위험 축소 매도에만 꺼지지 않는다")
    report("E18 위험 축소 매도 — 이탈 한도 밖이어도 전송(95,000) · 모델/자금/이익실현/매수는 그대로 · 추적 하한 끔", p)


def test_e19_gate_bound_to_approved_bytes():
    """E19 — 집행기 관문(R03 · 리뷰 재현 gate_missing_and_short_true의 올바른 동작): 통과 기록 없음 → 종료 2 · 호출 0 ·
    다른 내용(해시)에 대한 통과 → 거부 · 이 파일 바이트의 통과 → 전송 · 통과가 있어도 미달 표시 참 → 거부."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)], gate=False,
                           deficit_short=True, factor_short=True, undersized_short=True)
        rc, out = run_main([str(ap), "--send", "--stamp", "261013"])
        if rc != 2 or fb.calls:
            p.append(f"통과 기록 없음 — 종료코드 {rc} · 호출 {len(fb.calls)}(기대 2 · 0)")
        ap2 = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)], gate=False)
        gate_pass(ap2)                                         # 이 내용으로 통과
        body = json.loads(ap2.read_text(encoding="utf-8"))
        body["orders"][0]["qty"] = 30                          # judge 뒤 파일이 바뀌었다
        ap2.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        rc2, out2 = run_main([str(ap2), "--send", "--stamp", "261013"])
        if rc2 != 2 or fb.calls or "해시" not in out2:
            p.append(f"바뀐 승인 — 종료코드 {rc2} · 호출 {len(fb.calls)} · 해시 안내 {'해시' in out2}")
        gate_pass(ap2)                                         # 바뀐 내용으로 judge를 다시 받았다
        rc3, out3 = run_main([str(ap2), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc3 != 0 or fb.accepted() != 1:
            p.append(f"이 바이트의 통과 — 종료코드 {rc3} · 접수 {fb.accepted()}")
        tf = tmp / "journal" / "trades_261013_kr.json"
        runs = (json.loads(tf.read_text(encoding="utf-8")).get("runs") or []) if tf.exists() else []
        if not runs or (runs[-1].get("gate") or {}).get("cp") != "dispatch" or not runs[-1].get("approved_sha256"):
            p.append(f"runs[]에 관문 기록 없음 {runs[-1:] }")
        ap4 = approved_file(tmp, "KR", [KR("000660", "BUY", 1, 200000.0)], name="approved_261013_kr_v2.json",
                            factor_short=True)                 # 통과 기록은 있지만 미달 표시
        rc4, out4 = run_main([str(ap4), "--send", "--stamp", "261013"])
        if rc4 != 2 or fb.accepted("000660"):
            p.append(f"미달 표시 참인데 보냈다 — 종료코드 {rc4}")
    report("E19 집행기 관문 — 통과 기록 없음·내용 바뀜·미달 표시면 호출 0 · 이 바이트 통과면 전송", p)


def test_e19b_discipline_and_refill_paths():
    """E19b — 규율 전용 승인은 관문 없이 매도만 보낸다(매수 섞이면 거부) · 재투자 승인은 그 run 본 승인의 통과 위에서만."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000, "000660": 200000},
                        positions=[{"ticker": "005930", "qty": 5, "avg_price": 72000, "market": "KR"}])
        d1 = approved_file(tmp, "KR", [KR("005930", "SELL", 5, 70000.0, source="discipline", leg_kind="stop")],
                           name="approved_261013_kr_stops_1000.json", discipline_only=True, track1=None, note_lint=None)
        rc, out = run_main([str(d1), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc != 0 or fb.accepted("005930") != 1:
            p.append(f"규율 매도 — 종료코드 {rc} · 접수 {fb.accepted('005930')}")
        d2 = approved_file(tmp, "KR", [KR("005930", "SELL", 1, 70000.0, source="discipline"), KR("000660", "BUY", 1, 200000.0)],
                           name="approved_261013_kr_stops_1100.json", discipline_only=True, track1=None, note_lint=None)
        rc2, out2 = run_main([str(d2), "--send", "--stamp", "261013"])
        if rc2 != 2 or fb.accepted("000660"):
            p.append(f"규율 전용에 매수가 섞였는데 — 종료코드 {rc2}")
        r1 = approved_file(tmp, "KR", [KR("000660", "BUY", 1, 200000.0)], name="approved_261013_kr_refill_1200.json",
                           gate=False, refill=True)
        rc3, out3 = run_main([str(r1), "--send", "--stamp", "261013"])
        if rc3 != 2 or fb.accepted("000660"):
            p.append(f"본 승인 통과 없는 재투자 — 종료코드 {rc3}")
        main_ap = approved_file(tmp, "KR", [KR("035420", "BUY", 1, 200000.0)])     # 그 run 본 승인 + 통과 기록
        rc4, out4 = run_main([str(r1), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc4 != 0 or fb.accepted("000660") != 1:
            p.append(f"본 승인 통과 위 재투자 — 종료코드 {rc4} · 접수 {fb.accepted('000660')}")
    report("E19b 규율 전용 = 매도만(관문 없이) · 재투자 = 본 승인 통과 위에서만", p)


def test_e20_fact_changed_after_approval():
    """E20 — 승인 뒤 근거 값이 바뀌면 보내지 않는다(R04): 승인 행의 사실 판(version)보다 값 판(value_rev)이 뒤면 종료 2 · 호출 0 ·
    표기만 바뀐 것(rev만 오름 · value_rev 그대로)은 보낸다."""
    import facts
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        q = "Samsung Electronics reported quarterly revenue of 86 trillion won"
        with contextlib.redirect_stdout(io.StringIO()):
            f1 = facts.add_fact({"kind": "earnings", "ticker": "005930", "period": "2026Q3", "metric": "revenue", "actual": 86,
                                 "as_of": "2026-10-13", "url": "https://example.com/e20-a", "tier": 1, "quote": q}, ("261013", "kr"))["id"]
            f2 = facts.add_fact({"kind": "earnings", "ticker": "005930", "period": "2026Q3", "metric": "op_income", "actual": 12,
                                 "as_of": "2026-10-13", "url": "https://example.com/e20-b", "tier": 1, "quote": q + " and op income"},
                                ("261013", "kr"))["id"]
            facts.correct(f1, {"actual": 85}, "원문 값")                       # 값 교정 → value_rev 2
            facts.correct(f2, {"source_label": "IR 자료"}, "출처 이름")         # 표기만 → rev 2 · value_rev 1
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0, facts=[{"id": f1, "role": "support", "version": 1}])])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013"])
        if rc != 2 or fb.calls or "승인 뒤 근거가 고쳐졌다" not in out:
            p.append(f"값이 바뀐 근거 — 종료코드 {rc} · 호출 {len(fb.calls)} · {out[-160:]!r}")
        ap2 = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0, facts=[{"id": f2, "role": "support", "version": 1}])],
                            name="approved_261013_kr_v2.json")
        rc2, out2 = run_main([str(ap2), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc2 != 0 or fb.accepted() != 1:
            p.append(f"표기만 바뀐 근거 — 종료코드 {rc2} · 접수 {fb.accepted()} · {out2[-160:]!r}")
    report("E20 승인 뒤 근거 값이 바뀌면 전송 0 · 표기만 바뀐 것은 전송(R04)", p)


def test_e21_account_family_fixed():
    """E21 — run 계좌 계열(R09): 실계좌로 시작한 run을 지금 모의로 보내면 거부 · 그날 기록이 다른 계열이면 거부 · 호출 0."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        db.run_start("261013", "kr", account_mode="real")
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013"])
        if rc != 2 or fb.calls or "실계좌" not in out and "real" not in out:
            p.append(f"다른 계열 run — 종료코드 {rc} · 호출 {len(fb.calls)} · {out[-160:]!r}")
        (tmp / "journal" / "trades_261014_kr.json").write_text(json.dumps({"runs": [{"account_mode": "real", "svr": "real"}],
                                                                         "trades": []}), encoding="utf-8")
        ap2 = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)], name="approved_261014_kr.json")
        rc2, out2 = run_main([str(ap2), "--send", "--stamp", "261014"])
        if rc2 != 2 or fb.calls or "다른 계좌" not in out2:
            p.append(f"다른 계열 기록 — 종료코드 {rc2} · 호출 {len(fb.calls)} · {out2[-160:]!r}")
    report("E21 run 계좌 계열 — 다른 계열로 시작한 run·다른 계열 기록이면 전송 0(R09)", p)


def test_e22_send_fill_stage_rows():
    """E22 — 전송·체결 단계 행(U02·J04): 보낸 run에 execute(시작 = sent_at)·fill(종결 = filled_at) 행이 생긴다."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000}, fill_on_place=True)
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        rows = {r["stage"]: r for r in db.q("SELECT stage, status, started_at, ended_at FROM ex_stage WHERE run_id='261013_kr'")}
        r = db.run_get("261013", "kr") or {}
        if rc != 0 or "execute" not in rows or r.get("sent_at") != rows["execute"]["started_at"]:
            p.append(f"전송 단계: rc={rc} {rows.get('execute')} sent_at={r.get('sent_at')}")
        if "fill" not in rows or rows["fill"]["status"] != "ok" or not r.get("filled_at"):
            p.append(f"체결 단계: {rows.get('fill')} filled_at={r.get('filled_at')}")
    report("E22 전송·체결 단계 행 — execute 시작 = sent_at · fill 종결 = filled_at", p)


def test_e23_day_record_lost_vs_store():
    """E23 — 저장소에 이 승인 파일의 주문 의도가 있는데 그날 기록에서 사라졌으면(덮임) 보내지 않는다(종료 2 · 호출 0 · v7 R02).
    기록이 멀쩡하면 같은 승인 재실행은 중복으로 건너뛴다(호출 0 · 종료 0)."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"005930": 70000})
        ap = approved_file(tmp, "KR", [KR("005930", "BUY", 3, 70000.0)])
        rc, out = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        db.import_run_artifacts("261013", "kr", here=tmp)
        tf = tmp / "journal" / "trades_261013_kr.json"
        good = tf.read_text(encoding="utf-8")
        n0 = len(fb.order_calls())
        rc2, out2 = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc2 != 0 or len(fb.order_calls()) != n0:
            p.append(f"기록이 멀쩡한 재실행 — 종료 {rc2} · 호출 {len(fb.order_calls()) - n0}")
        rec = json.loads(good)
        rec["trades"] = []
        tf.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        rc3, out3 = run_main([str(ap), "--send", "--stamp", "261013", "--max-sec", "60"])
        if rc3 != 2 or len(fb.order_calls()) != n0 or "기록이 덮였다" not in out3:
            p.append(f"기록이 덮인 재실행 — 종료 {rc3} · 호출 {len(fb.order_calls()) - n0} · {out3.strip().splitlines()[-1][:120] if out3.strip() else ''}")
    report("E23 그날 기록이 덮였는데 저장소에 그 승인의 주문 의도가 있으면 전송 0(종료 2) · 멀쩡하면 중복 건너뜀", p)


def test_e24_premise_check_at_send():
    """E24 — 전송 직전 전제 재검사(J04): 모델 매수는 시세 한 번으로 손절선·진입 구간·목표 여유를 다시 본다 — 손절선 아래면 UNSENT
    '전제 이탈'(호출 0) · 구간 밖이면 UNSENT · 그대로면 전송 · 전송 시점 시세가 행(quote_at_send)과 mk_quote(source=send)에 · 위험 축소 매도는
    손절선 아래여도 전송(R08) · 전제 항목이 없으면 검사 대상 없음으로 전송."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"000001": 95000, "000002": 99000, "000003": 101000, "000004": 70000, "000009": 90000},
                        positions=[{"ticker": "000009", "qty": 5, "avg_price": 100000, "market": "KR"}])
        stop = lambda n: {"exits": [{"id": "x1", "check": f"price <= {n}"}, {"id": "x2", "check": "price >= 150000"}]}
        orders = [KR("000001", "BUY", 1, 100000.0, proposal={"ticker": "000001", **stop(97000)}),
                  KR("000002", "BUY", 1, 100000.0, trigger_id="e1",
                     proposal={"ticker": "000002", "entry": {"basis": "support_1 100,000 — 구간 99,500~100,500"}, **stop(90000)}),
                  KR("000003", "BUY", 1, 100000.0, proposal={"ticker": "000003", **stop(90000)}),
                  KR("000004", "BUY", 1, 70000.0, proposal={"ticker": "000004"}),
                  KR("000009", "SELL", 5, 92000.0, source="discipline", leg_kind="stop", risk_reducing=True)]
        ap = approved_file(tmp, "KR", orders)
        rc, out = run_main([str(ap), "--send", "--stamp", "261013", "--no-wait"])
        tr = {t["ticker"]: t for t in _trades(tmp)}
        placed = {c[1].get("ticker") for c in fb.order_calls()}
        if tr["000001"].get("status") != "UNSENT" or "전제 이탈 — 손절선 97,000 아래" not in json.dumps(tr["000001"].get("result"), ensure_ascii=False) \
                or "000001" in placed:
            p.append(f"손절선 아래 매수: {tr['000001'].get('status')} {tr['000001'].get('result')} · 호출 {'000001' in placed}")
        if tr["000002"].get("status") != "UNSENT" or "진입 구간" not in json.dumps(tr["000002"].get("result"), ensure_ascii=False):
            p.append(f"구간 밖 매수: {tr['000002'].get('status')} {tr['000002'].get('result')}")
        if tr["000003"].get("status") != "SENT" or (tr["000003"].get("quote_at_send") or {}).get("price") != 101000.0 \
                or tr["000003"].get("premise") != "전제 유지":
            p.append(f"전제 유지 매수: {tr['000003'].get('status')} {tr['000003'].get('quote_at_send')} {tr['000003'].get('premise')}")
        if tr["000004"].get("status") != "SENT" or tr["000004"].get("premise") != "전제 검사 대상 없음":
            p.append(f"전제 항목 없는 매수: {tr['000004'].get('status')} {tr['000004'].get('premise')}")
        if tr["000009"].get("status") != "SENT" or "quote_at_send" in tr["000009"]:
            p.append(f"위험 축소 매도: {tr['000009'].get('status')} · 시세 기록 {tr['000009'].get('quote_at_send')}")
        sends = {r["subject"] for r in db.q("SELECT subject FROM mk_quote WHERE source='send'")}
        if sends != {"KR:000001", "KR:000002", "KR:000003", "KR:000004"}:
            p.append(f"mk_quote 전송 시점 시세: {sends}")
        runs = (json.loads((tmp / "journal" / "trades_261013_kr.json").read_text(encoding="utf-8")).get("runs") or [{}])
        if "snapshot_age_min" not in runs[-1]:
            p.append("집행 기록에 스냅샷 나이 칸이 없다")
    report("E24 전송 직전 전제 재검사 — 손절선 아래·구간 밖 매수 UNSENT(호출 0) · 그대로면 전송 · 전송 시세 기록 · 위험 축소 매도는 전송", p)


def _w01_record(tmp, now, order_no="100"):
    path = tmp / "journal" / "trades_261013_kr.json"
    row = {"ts": now.isoformat(), "market": "KR", "ticker": "000660", "action": "BUY", "qty": 5, "price": 100000.0, "status": "SENT",
           "source": "llm", "result": {"order_no": order_no, "orgno": "00950"}, "fill": {"qty_before": 0, "cancel_sent": now.isoformat(),
                                                                                         "cancel_why": "대기 초과 — 잔량 5 취소"}}
    db.write_json_atomic(path, {"market": "KR", "svr": "paper", "trades": [row]})
    return path


def test_e25_reorder_crash_no_duplicate():
    """E25 ← reorder_crash_duplicates(Codex W01)의 올바른 동작 — 재발주·정정은 보내기 **전에** 의도(번호 없는 링크)를 디스크에 쓴다.
    ① 재발주 접수 직후 프로세스 종료 → 디스크에 의도 · 재시작은 다시 보내지 않고 당일 목록으로 그 주문을 붙인다(접수 1 · FILLED)
    ② 재발주 성공 응답 뒤 결과 저장 직전 종료 → 같은 결과 ③ 분명한 거절이면 의도를 거두고 원래 상태 ④ 정정 접수 직후 종료 → 재시작이
    다시 정정하지 않고 원주문 번호로 정정 주문을 붙인다."""
    from unittest.mock import patch
    p = []
    orig_status = fill._status

    def status_100(client, t, no, excd=""):                   # 원주문 100은 취소 확정(잔량 0) — 나머지는 가짜 증권사 그대로
        if str(no) == "100":
            return {"found": True, "filled_qty": 0, "remain_qty": 0, "avg_price": 0, "cancelled": True, "rejected": False}
        return orig_status(client, t, no, excd)
    cfg = {**fill.fill_cfg({}), "fill_reorder_max": 1, "fill_reorder_max_pct": 1.0, "fill_poll_sec": 1}
    # ① 접수 직후 종료
    with env() as tmp:
        now = datetime.now(KST)
        path = _w01_record(tmp, now)
        fb = FakeBroker(prices={"000660": 100000}, crash_on={"000660": SystemExit("accepted then died")})
        with patch.object(fill, "_status", status_100), patch.object(fill, "_live_price", return_value=100000.0), \
                patch.object(fill, "market_session", _open_session):
            try:
                fill.confirm(path, client=fb, cfg=cfg, sleep_fn=lambda _: None, max_sec=0, chase=False, quiet=True)
                p.append("① 종료가 일어나지 않았다(훅)")
            except SystemExit:
                pass
            disk = json.loads(path.read_text(encoding="utf-8"))["trades"][0]["fill"]
            if not any(r.get("pending") for r in disk.get("reorders") or []) or not any(
                    lk.get("pending") and lk.get("why") == "재발주" for lk in disk.get("order_chain") or []):
                p.append(f"① 보내기 전 의도가 디스크에 없다: {disk.get('reorders')} {disk.get('order_chain')}")
            fill.confirm(path, client=fb, cfg=cfg, sleep_fn=lambda _: None, max_sec=60, chase=False, quiet=True)
            t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
        nos = [lk.get("order_no") for lk in (t["fill"].get("order_chain") or [])]
        if fb.accepted("000660") != 1 or len(fb.order_calls()) != 1 or t["fill"].get("verdict") != "FILLED" or len(nos) != 2 or not nos[1]:
            p.append(f"① 재시작 — 접수 {fb.accepted('000660')} · 호출 {len(fb.order_calls())} · {t['fill'].get('verdict')} · 체인 {nos}")
    # ② 성공 응답 뒤 결과 저장 직전 종료
    with env() as tmp:
        now = datetime.now(KST)
        path = _w01_record(tmp, now)
        fb = FakeBroker(prices={"000660": 100000})
        real_write, n = db.write_json_atomic, {"k": 0}

        def write_then_die(pth, obj):
            n["k"] += 1
            if n["k"] == 2:                                   # 1 = 보내기 전 의도 · 2 = 성공 응답 뒤 저장 → 여기서 죽는다
                raise SystemExit("died before saving the reorder result")
            return real_write(pth, obj)
        with patch.object(fill, "_status", status_100), patch.object(fill, "_live_price", return_value=100000.0), \
                patch.object(fill, "market_session", _open_session):
            with patch.object(fill.db, "write_json_atomic", write_then_die):
                try:
                    fill.confirm(path, client=fb, cfg=cfg, sleep_fn=lambda _: None, max_sec=0, chase=False, quiet=True)
                except SystemExit:
                    pass
            fill.confirm(path, client=fb, cfg=cfg, sleep_fn=lambda _: None, max_sec=60, chase=False, quiet=True)
            t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
        if fb.accepted("000660") != 1 or t["fill"].get("verdict") != "FILLED":
            p.append(f"② 저장 직전 종료 뒤 재시작 — 접수 {fb.accepted('000660')} · {t['fill'].get('verdict')}")
    # ③ 분명한 거절 — 의도를 거두고 원래 상태(취소 확정)로
    with env() as tmp:
        now = datetime.now(KST)
        path = _w01_record(tmp, now)
        rej = kc.KisError("[40580000] 주문가능수량 초과", kind="rejected")
        fb = FakeBroker(prices={"000660": 100000}, fail_place={"000660": ("raise", rej)})
        with patch.object(fill, "_status", status_100), patch.object(fill, "_live_price", return_value=100000.0), \
                patch.object(fill, "market_session", _open_session):
            fill.confirm(path, client=fb, cfg=cfg, sleep_fn=lambda _: None, max_sec=0, chase=False, quiet=True)
            t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
        if (t["fill"].get("reorders") or []) or any(lk.get("pending") for lk in t["fill"].get("order_chain") or []) \
                or t["fill"].get("verdict") != "CANCELLED":
            p.append(f"③ 거절 뒤 의도 잔존 — {t['fill'].get('reorders')} · {t['fill'].get('verdict')}")
    # ④ 정정 접수 직후 종료 → 재시작이 다시 정정하지 않고 원주문 번호로 붙인다
    with env() as tmp:
        now = datetime.now(KST) - timedelta(minutes=2)
        path = tmp / "journal" / "trades_261013_kr.json"
        row = {"ts": now.isoformat(), "market": "KR", "ticker": "000660", "action": "BUY", "qty": 5, "price": 100000.0, "status": "SENT",
               "source": "llm", "result": {"order_no": "100", "orgno": "00950"}, "fill": {"qty_before": 0}}
        db.write_json_atomic(path, {"market": "KR", "svr": "paper", "trades": [row]})
        fb = FakeBroker(prices={"000660": 100500})
        mods = []

        def modify_then_die(client, t, order_no, orgno, excd, qty, price, cancel):
            mods.append((order_no, qty, price, cancel))
            raise SystemExit("modify accepted then died")

        def status_live(client, t, no, excd=""):
            return {"found": True, "filled_qty": 0, "remain_qty": 5, "avg_price": 0, "cancelled": False, "rejected": False} \
                if str(no) == "100" else {"found": True, "filled_qty": 5, "remain_qty": 0, "avg_price": 100500.0,
                                          "cancelled": False, "rejected": False}

        def day_list(client, t):
            return {"ok": True, "complete": True, "authoritative_empty": False,
                    "rows": [{"order_no": "300", "orgn_order_no": "100", "ticker": "000660", "side": "BUY", "ord_qty": 5,
                              "is_original": False, "ord_at": datetime.now(KST).isoformat(), "orgno": "00950"}]}
        ccfg = {**cfg, "fill_wait_minutes": 60, "fill_chase_max": 2, "fill_chase_after_sec": 1, "fill_chase_max_pct": 1.0}
        with patch.object(fill, "_status", status_live), patch.object(fill, "_live_price", return_value=100500.0), \
                patch.object(fill, "market_session", _open_session), patch.object(fill, "_modify", modify_then_die), \
                patch.object(fill, "_day_orders", day_list):
            try:
                fill.confirm(path, client=fb, cfg=ccfg, sleep_fn=lambda _: None, max_sec=0, chase=True, quiet=True)
                p.append("④ 정정 종료가 일어나지 않았다(훅)")
            except SystemExit:
                pass
            ch = json.loads(path.read_text(encoding="utf-8"))["trades"][0]["fill"].get("order_chain") or []
            if not any(lk.get("pending") and lk.get("orgn_order_no") == "100" for lk in ch):
                p.append(f"④ 정정 의도가 디스크에 없다: {ch}")
            fill.confirm(path, client=fb, cfg=ccfg, sleep_fn=lambda _: None, max_sec=60, chase=True, quiet=True)
            t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
        nos = [lk.get("order_no") for lk in (t["fill"].get("order_chain") or [])]
        if len(mods) != 1 or "300" not in nos or t["fill"].get("verdict") != "FILLED":
            p.append(f"④ 재시작 — 정정 호출 {len(mods)} · 체인 {nos} · {t['fill'].get('verdict')}")
    report("E25 재발주·정정은 보내기 전에 의도 기록 — 접수 직후·저장 직전 종료 뒤 재시작 추가 접수 0 · 연결 복원 · 거절이면 의도 거둠(W01)", p)


def test_e26_request_persisted_before_post():
    """E26 ← pending_keeps_pre_refresh_qty(Codex W02)의 올바른 동작 — 전송 직전 갱신·상한으로 바뀐 **실제 요청**을 보내기 전에 디스크에 쓴다.
    승인 5주 → 시세 101,000으로 4주 전송 → 접수 직후 종료: 디스크 qty 4·request 4·approved_qty 5 → 당일 목록 대사가 그 주문을 붙인다(SENT).
    주문당 상한으로 줄어든 경우도 같다 · 요청 가격 = 보낸 호가."""
    p = []
    with env() as tmp:
        fb = FakeBroker(prices={"000660": 101000}, crash_on={"000660": SystemExit("post-accept crash")})
        path = tmp / "journal" / "trades_261013_kr.json"
        try:
            ex._send_orders(fb, [KR("000660", "BUY", 5, 100000.0)], "KR", "approved_261013_kr.json", journal=path,
                            approved_sha="sha", qty_before={"000660": 0})
            p.append("종료가 일어나지 않았다")
        except SystemExit:
            pass
        rec = json.loads(path.read_text(encoding="utf-8"))
        t = rec["trades"][0]
        posted = fb.order_calls()[0][1]
        if (t.get("qty"), (t.get("request") or {}).get("qty"), t.get("approved_qty"), t.get("status")) != (4, 4, 5, "PENDING") \
                or posted["qty"] != 4 or (t.get("request") or {}).get("price") != posted["price"]:
            p.append(f"디스크 요청: qty {t.get('qty')} · request {t.get('request')} · 승인 {t.get('approved_qty')} · 보낸 것 {posted}")
        fill.resolve_pending(fb, rec, datetime.now(KST), lambda *a, **k: None)
        if rec["trades"][0].get("status") != "SENT" or rec["trades"][0]["result"].get("order_no") not in fb.orders:
            p.append(f"당일 목록 대사: {rec['trades'][0].get('status')} {rec['trades'][0].get('result')}")
    with env() as tmp:
        fb = FakeBroker(prices={"000660": 300000}, crash_on={"000660": SystemExit("post-accept crash")})
        path = tmp / "journal" / "trades_261013_kr.json"
        try:
            ex._send_orders(fb, [KR("000660", "BUY", 5, 300000.0)], "KR", "approved_261013_kr.json", journal=path, approved_sha="sha",
                            qty_before={"000660": 0}, cap={"max_krw": 1_000_000, "max_shares": 2, "mode": "real", "fx": 1.0})
        except SystemExit:
            pass
        rec = json.loads(path.read_text(encoding="utf-8"))
        t = rec["trades"][0]
        if fb.order_calls() and ((t.get("request") or {}).get("qty") != fb.order_calls()[0][1]["qty"] or t.get("approved_qty") != 5):
            p.append(f"상한 축소 요청: {t.get('request')} vs {fb.order_calls()[0][1]} · 승인 {t.get('approved_qty')}")
        if fb.order_calls():
            fill.resolve_pending(fb, rec, datetime.now(KST), lambda *a, **k: None)
            if rec["trades"][0].get("status") != "SENT":
                p.append(f"상한 축소 대사: {rec['trades'][0].get('status')}")
        elif t.get("status") != "UNSENT":
            p.append(f"상한 축소 — 보내지도 않고 UNSENT도 아니다: {t.get('status')}")
    report("E26 실제 요청(갱신·상한 뒤 수량·가격)을 보내기 전에 기록 — 접수 직후 종료 뒤 당일 목록 대사가 붙인다 · 승인 값은 따로(W02)", p)


def test_e27_quote_retry_runs_premise():
    """E27 ← quote_retry_skips_premise(Codex W04)의 올바른 동작 — 첫 시세 실패 → 재조회 성공이면 **그 시세로** 전제 검사를 한다:
    손절선 아래·진입 구간 밖·목표 여유 미달은 전송 0 · 정상이면 그 시세·시각을 기록하고 1회 전송 · 둘 다 실패면 '전제 미검사' 기록 뒤
    승인가로 전송(시세는 두 번만 읽는다) · 위험 축소 매도는 대상 아님."""
    from unittest.mock import patch
    p = []
    fail = (None, None, "현재가 재조회 실패(KisError)")

    def run(order, quotes, cfg=None):
        with env():
            fb = FakeBroker(prices={order["ticker"]: 100000})
            calls = {"n": 0}

            def q(client, o):
                calls["n"] += 1
                return quotes[min(calls["n"] - 1, len(quotes) - 1)]
            with patch.object(ex, "_live_quote", q), patch.object(ex, "_send_cfg", lambda: cfg or {"send_refresh_max_pct": 2.0,
                                                                                                  "fill_aggressive_ticks": 0}):
                rows, _ = ex._send_orders(fb, [order], "KR", "approved_261013_kr.json")
            return rows[0], fb, calls["n"]
    at = "2026-10-13T10:00:00+09:00"
    r, fb, n = run(KR("000660", "BUY", 5, 100000.0, proposal={"exits": [{"check": "price <= 99500"}]}), [fail, (99000.0, at, "")])
    if r["status"] != "UNSENT" or "손절선 99,500 아래" not in json.dumps(r.get("result"), ensure_ascii=False) or fb.order_calls() \
            or (r.get("quote_at_send") or {}).get("tries") != 2:
        p.append(f"손절선 아래(재조회): {r['status']} {r.get('result')} 호출 {len(fb.order_calls())} {r.get('quote_at_send')}")
    r, fb, n = run(KR("000660", "BUY", 5, 100000.0, proposal={"entry": {"basis": "구간 99,800~100,200"}}), [fail, (101000.0, at, "")])
    if r["status"] != "UNSENT" or "진입 구간" not in json.dumps(r.get("result"), ensure_ascii=False) or fb.order_calls():
        p.append(f"구간 밖(재조회): {r['status']} {r.get('result')}")
    lim = {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 0, "position_sizing": {"min_target_gap_pct": 10.0}, "account_mode": "paper"}
    r, fb, n = run(KR("000660", "BUY", 5, 100000.0, proposal={"exits": [{"check": "price >= 108000"}]}), [fail, (100000.0, at, "")], lim)
    if r["status"] != "UNSENT" or "바닥" not in json.dumps(r.get("result"), ensure_ascii=False) or fb.order_calls():
        p.append(f"목표 여유 미달(재조회): {r['status']} {r.get('result')}")
    r, fb, n = run(KR("000660", "BUY", 5, 100000.0, proposal={"exits": [{"check": "price <= 95000"}]}), [fail, (100000.0, at, "")])
    q_ = r.get("quote_at_send") or {}
    if r["status"] != "SENT" or len(fb.order_calls()) != 1 or (q_.get("price"), q_.get("at"), q_.get("tries")) != (100000.0, at, 2) \
            or r.get("premise") != "전제 유지" or not q_.get("first_error"):
        p.append(f"정상(재조회): {r['status']} 호출 {len(fb.order_calls())} {q_} {r.get('premise')}")
    r, fb, n = run(KR("000660", "BUY", 5, 100000.0, proposal={"exits": [{"check": "price <= 95000"}]}), [fail, fail])
    if r["status"] != "SENT" or r.get("premise") != "시세 조회 실패 — 전제 미검사" or n != 2 or fb.order_calls()[0][1]["price"] != 100000:
        p.append(f"둘 다 실패: {r['status']} {r.get('premise')} 시세 읽기 {n}회")
    r, fb, n = run(KR("000660", "SELL", 5, 100000.0, source="discipline", leg_kind="stop", risk_reducing=True), [(95000.0, at, "")])
    if r["status"] != "SENT" or r.get("premise") or "quote_at_send" in r:
        p.append(f"위험 축소 매도: {r['status']} {r.get('premise')}")
    report("E27 첫 시세 실패 → 재조회 성공이면 그 시세로 전제 검사(손절선·구간·목표 여유 전송 0 · 정상 1회) · 둘 다 실패면 미검사 기록 · 매도 그대로(W04)", p)


def test_e28_quote_reads_recover():
    """E28 — 시세를 못 읽는 원인을 코드에서 없앤다(2026-10-19 사용자 '제대로 읽어오면 되잖아').
    ① 토큰이 run 도중 만료(HTTP 500 · EGW00123)되면 새 토큰을 받아 한 번 다시 묻는다(예전엔 같은 낡은 토큰으로 네 번 실패) ·
    ② 주문(POST)도 인증 단계 만료면 새 토큰으로 한 번 다시 보내 접수 1건(만료 응답은 접수가 아니다) ·
    ③ 틀린 거래소로 물어 빈 값이 오면 같은 계열의 다른 거래소로 다시 묻고 그 거래소를 돌려준다 ·
    ④ 거래소 코드 없이 온 미국 매수는 시세를 준 거래소로 갱신·전제 검사·전송한다."""
    import urllib.error
    import urllib.request
    from unittest.mock import patch
    p = []
    tmpd = Path(tempfile.mkdtemp(prefix="e28_"))

    def client():
        c = object.__new__(kc.KisClient)
        c.svr, c.read_only, c.base, c._ctx, c._last_call = "real", False, "https://kis.example", None, 0.0
        c._token, c.app_key, c.app_secret = "OLD", "appkey-123456", "secret"
        c.tr = {"ov_price": "HHDFS00000300", "dom_price": "FHKST01010100"}
        return c

    class Resp:
        def __init__(self, obj):
            self.b = json.dumps(obj).encode("utf-8")

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    calls = []

    def fake_urlopen(script):
        def _open(req, timeout=20, context=None):
            auth = req.get_header("Authorization") or ""
            calls.append((req.get_method(), req.full_url, auth))
            act = script(req, auth)
            if isinstance(act, Exception):
                raise act
            return Resp(act)
        return _open

    def expired():
        return urllib.error.HTTPError("https://kis.example", 500, "err", {}, io.BytesIO(json.dumps(
            {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}).encode("utf-8")))
    try:
        with patch.object(kc, "TOKEN_CACHE", tmpd / "token.json"), patch.object(kc.time, "sleep", lambda s_: None):
            # ① 조회 — 만료 → 새 토큰 → 성공
            def s1(req, auth):
                if req.full_url.endswith("/oauth2/tokenP"):
                    return {"access_token": "NEW", "expires_in": 86400}
                return expired() if "OLD" in auth else {"rt_cd": "0", "output": {"last": "101.5", "rate": "0.3", "curr": "USD", "base": "100"}}
            calls.clear()
            with patch.object(urllib.request, "urlopen", fake_urlopen(s1)):
                c = client()
                q = c.overseas_price("XOM", excd="NYS")
            gets = [x for x in calls if x[0] == "GET"]
            if q["price"] != 101.5 or [g[2] for g in gets] != ["Bearer OLD", "Bearer NEW"] or c._token != "NEW":
                p.append(f"① 만료 토큰 조회: {q.get('price')} · {[g[2] for g in gets]}")
            # ② 주문 — 만료 → 새 토큰으로 한 번 → 접수 1
            posts = []

            def s2(req, auth):
                if req.full_url.endswith("/oauth2/tokenP"):
                    return {"access_token": "NEW2", "expires_in": 86400}
                if req.get_method() == "POST":
                    posts.append(auth)
                    if "OLD" in auth:
                        return expired()
                    return {"rt_cd": "0", "msg_cd": "APBK0013", "output": {"ODNO": "0000555", "ORD_TMD": "100000", "KRX_FWDG_ORD_ORGNO": "00950"}}
                return {"rt_cd": "0", "output": {}}
            with patch.object(urllib.request, "urlopen", fake_urlopen(s2)):
                c = client()
                r = c._request("POST", "/uapi/overseas-stock/v1/trading/order", headers=c._headers("TTTT1002U"), body={"x": 1})
            if (r.get("output") or {}).get("ODNO") != "0000555" or posts != ["Bearer OLD", "Bearer NEW2"]:
                p.append(f"② 만료 토큰 주문: {r} · {posts}")
            # ③ 빈 값 거래소 → 다른 거래소
            def s3(req, auth):
                if "EXCD=NYS" in req.full_url:
                    return {"rt_cd": "0", "output": {"last": "88.20", "rate": "-1.0", "curr": "USD", "base": "89.1"}}
                return {"rt_cd": "0", "output": {"last": "", "rate": "", "curr": "", "base": ""}}
            calls.clear()
            with patch.object(urllib.request, "urlopen", fake_urlopen(s3)):
                c = client()
                q = c.overseas_price("VST", excd="NAS")
            if q["price"] != 88.2 or q.get("excd") != "NYS" or q.get("excd_tried") != ["NAS", "NYS"]:
                p.append(f"③ 거래소 재조회: {q}")
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    # ④ 거래소 없는 미국 매수 — 시세를 준 거래소로 갱신·전제 검사·전송
    class NyseBroker(FakeBroker):
        def overseas_price(self, ticker, excd="NAS"):
            self.calls.append(("overseas_price", {"ticker": ticker, "excd": excd}))
            return {"ticker": ticker, "price": 88.2, "change_pct": 0.0, "currency": "USD", "excd": "NYS", "excd_tried": [excd, "NYS"]}
    with env():
        fb = NyseBroker(prices={"VST": 88.2})
        o = US("VST", "BUY", 3, 88.0, excd=None, proposal={"exits": [{"check": "price <= 80"}]})
        o.pop("excd", None)
        rows, _ = ex._send_orders(fb, [o], "US", "approved_261013_us.json")
        sent = fb.order_calls()
        if rows[0]["status"] != "SENT" or not sent or sent[0][1].get("excd") != "NYSE" or rows[0].get("excd_from_quote") != "NYS" \
                or rows[0].get("premise") != "전제 유지" or (rows[0].get("quote_at_send") or {}).get("price") != 88.2:
            p.append(f"④ 거래소 없는 주문: {rows[0].get('status')} · 보낸 거래소 {sent[0][1].get('excd') if sent else None} · "
                     f"{rows[0].get('excd_from_quote')} · {rows[0].get('premise')}")
    report("E28 시세 읽기 복구 — 토큰 만료면 새 토큰으로 한 번 다시(조회·주문 · 주문 접수 1) · 빈 값 거래소면 다른 거래소 · 거래소 없는 주문은 찾은 거래소로", p)


for _fn in (test_e1_send_loop_kr, test_e2_send_loop_us_daytime, test_e3_order_cap_at_send, test_e4_main_end_to_end_kr,
            test_e5_immediate_fill_settle_date, test_e6_dry_run_sends_nothing, test_e7_headerless_approved_rejected,
            test_e8_stops_intraday_header, test_e9_finalize_backfills_settle_date, test_e10_tests_line_flags_code_change,
            test_e11_audit_reversal_send_settles_ledger, test_e12_send_fill_import, test_e13_thesis_read_failure_after_send,
            test_e14_response_lost_not_resent, test_e14b_explicit_rejection_retryable, test_e14c_no_order_no_and_unsent,
            test_e14d_not_placed_resolved_unsent, test_e15_crash_after_first_order, test_e15b_corrupt_day_record_stops,
            test_e15c_day_lock, test_e18_risk_reducing_sell_not_skipped, test_e19_gate_bound_to_approved_bytes,
            test_e19b_discipline_and_refill_paths, test_e20_fact_changed_after_approval, test_e21_account_family_fixed,
            test_e22_send_fill_stage_rows, test_e23_day_record_lost_vs_store, test_e24_premise_check_at_send,
            test_e25_reorder_crash_no_duplicate, test_e26_request_persisted_before_post, test_e27_quote_retry_runs_premise,
            test_e28_quote_reads_recover):
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
