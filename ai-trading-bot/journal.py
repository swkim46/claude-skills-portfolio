#!/usr/bin/env python3
"""
저널 — equity 곡선, 그림자 벤치마크, 고점 기록, 주간 리포트.

이 시스템의 성공 지표는 수익률이 아니라 ①규율 집행률 ②무사고 ③벤치마크 병기 기록의
완전성이다(AI자동매매_공개사례_성능조사_v1_0.md §5). 그래서 저널은 부가 기능이 아니라
산출물 본체다. 조사에서 확인된 개인 공개사례들이 근거가 되지 못한 이유가 정확히
"같은 기간 buy-and-hold와 비교하지 않았다"였다.

그림자 벤치마크: 첫 실행일에 equity와 벤치마크 ETF 가격을 기준점으로 박아두고,
이후 매일 "같은 돈을 그날 그 ETF에 넣고 방치했다면 지금 얼마인가"를 계산한다.

position_peaks.json은 risk_guard의 트레일링 스톱이 읽는다 — 저널이 멈추면 트레일링도
멈추므로, 이 스크립트는 매 run 마지막에 반드시 돌아야 한다.

사용:
    python3 journal.py --daily --market kr
    python3 journal.py --weekly
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import peaks as peaks_store
import risk_guard as rg
from kis_client import KisClient, KisError, us_buying_power

HERE = Path(__file__).parent
CONFIG_DIR = HERE / "config"
JOURNAL_DIR = HERE / "journal"
WEEKLY_DIR = JOURNAL_DIR / "weekly"
EQUITY_PATH = JOURNAL_DIR / "equity_curve.jsonl"
PEAKS_PATH = JOURNAL_DIR / "position_peaks.json"
BENCHMARK_PATH = CONFIG_DIR / "benchmark.json"
WATCHLIST_PATH = CONFIG_DIR / "watchlist.json"

KST = timezone(timedelta(hours=9))


def latest_per_day(rows: list) -> list:
    """(날짜, 시장)마다 마지막 행만 남긴다. 순서는 유지한다.

    equity_curve는 append-only다 — 감사 기록이므로 고쳐 쓰지 않는다. 대신 같은 날
    두 번 이상 기록될 수 있고(하루 두 슬롯, 재시도, 손으로 한 번 더), 그걸 그대로 세면
    누적 비용·회전이 그만큼 부풀고 추이 그래프에 같은 날 점이 여러 개 찍힌다.
    그래서 **쓸 때 지우지 않고 읽을 때 접는다**. 2026-09-08에 실제로 2행이 생겨 발견됐다.
    """
    keep = {}
    for r in rows:
        keep[(r.get("date"), r.get("market"))] = r
    return list(keep.values())


def today_cost_and_turnover(balance: dict, market: str, stamp: str = None) -> dict:
    """당일 제비용과 체결금액. 반환 {fees, turnover, source}.

    국내는 브로커가 당일 제비용(`thdt_tlex_amt`)과 체결금액을 직접 준다 — 우리가 요율을
    추정하지 않아도 되므로 그대로 쓴다.
    해외 잔고 TR에는 대응하는 항목이 없어서, 체결금액만 그날 주문 기록에서 추정하고
    비용은 None으로 둔다. 추정치를 실측인 척 적는 것보다 '모른다'가 낫다.
    """
    if market == "KR":
        return {
            "fees": balance.get("today_fees"),
            "turnover": (balance.get("today_buy_amt") or 0) + (balance.get("today_sell_amt") or 0),
            "source": "broker (thdt_tlex_amt / thdt_buy_amt+thdt_sll_amt)",
        }

    day = stamp or datetime.now(KST).strftime("%y%m%d")
    turnover = 0.0
    for p in sorted(JOURNAL_DIR.glob(f"trades_{day}_{market.lower()}*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for t in data.get("trades", []):
            # 나간 금액은 risk_guard와 같은 한 곳에서 센다(status·fill.verdict 규칙이 거기 있다).
            amt = rg.sent_amount(t) if t.get("action") == "BUY" else (
                (t.get("qty") or 0) * (t.get("price") or 0) if t.get("status") != "FAILED" else None)
            if amt:
                turnover += amt
    return {"fees": None, "turnover": turnover,
            "source": "주문기록 기준 추정(체결가 아닌 주문가). 해외 잔고 TR에 제비용 항목 없음"}


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_accum(path: Path) -> tuple:
    """누적 파일 읽기 — **(데이터, 읽었는가)**. 못 읽으면 옆으로 치워 보존한다.

    `_read_json`처럼 기본값으로 갈음해도 되는 파일과, 그러면 **이력이 사라지는** 파일이
    있다. 후자는 '아직 없음'과 '읽지 못함'을 구분해야 하고, 읽지 못했으면 **쓰지 않아야**
    한다 — `execute.py`가 거래 기록에 쓰는 것과 같은 처방(지우지 말고 치우고 알린다).
    """
    if not path.exists():
        return {}, True                      # 아직 없음 — 처음 박는 것은 정상이다
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("최상위가 dict가 아니다")
        return d, True
    except (json.JSONDecodeError, OSError, ValueError) as e:
        aside = path.with_name(f"{path.stem}.corrupt_{datetime.now(KST):%y%m%d_%H%M%S}.json")
        try:
            path.rename(aside)
            where = aside.name
        except OSError:
            where = "(보존 실패)"
        print(f"\n★ {path.name}을 읽을 수 없어 {where}로 옮겨 보존했다 ({e}).",
              file=sys.stderr)
        return {}, False


# ------------------------------------------------------------------ 일별 기록

def benchmark_price(client: KisClient, market: str, watchlist: dict) -> dict:
    bm = (watchlist.get("benchmarks") or {}).get(market) or {}
    ticker = bm.get("ticker")
    if not ticker:
        return {}
    try:
        if market == "KR":
            q = client.domestic_price(ticker)
        else:
            excd = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}.get(bm.get("excd", "AMEX"), "AMS")
            q = client.overseas_price(ticker, excd=excd)
        return {"ticker": ticker, "name": bm.get("name", ""), "price": q["price"]}
    except KisError as e:
        return {"ticker": ticker, "error": str(e)}


def update_peaks(positions: list, market: str = "KR") -> dict:
    """그 **시장의** 최고가만 갱신한다. 판 종목은 그 시장 안에서만 지운다.

    ★ 예전에는 시장 구분이 없는 평평한 dict였고 `set(peaks) - held`로 안 들고 있는
    종목을 지웠다. 두 시장을 번갈아 돌리므로 **미국 run이 국내 peak 전체를 지우고
    국내 run이 미국 peak 전체를 지웠다** — 트레일링 스톱의 고점이 매일 리셋된다.
    표기 규칙은 `peaks.py` 하나에만 둔다(읽는 쪽 risk_guard와 갈리지 않게).
    """
    return peaks_store.update(market, positions)


def _load_runs() -> list:
    """`run_log.jsonl` 전건. 못 읽는 줄은 세지 않되 **몇 줄을 못 읽었는지 알린다** —
    조용히 건너뛰면 기권율의 분모가 줄어 지표가 좋아 보인다."""
    p = JOURNAL_DIR / "run_log.jsonl"
    if not p.exists():
        return []
    out, bad = [], 0
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    if bad:
        print(f"  ※ run_log.jsonl에서 {bad}줄을 못 읽었다 — 기권율 분모가 실제보다 작다.",
              file=sys.stderr)
    return out


def _avoidance_block(days: int) -> list:
    """회피 감사 3지표 — **결정을 미루는 것은 조용히 누적되므로 세어야 보인다.**

    ★ 이 절이 없어서 주간 리포트가 성과만 재고 **회피는 재지 않았다.** 그런데 이 시스템의
    실패 양상은 손실이 아니라 기권이다(실측: 첫 실 run이 `no_trade`로 끝난 날 벤치마크는
    +2.45%였고 계좌는 0.00%였다). 재지 않는 것은 관리되지 않는다.

    세 지표의 출처가 다 다르다 — `run_log.jsonl`(기권율) · `equity_curve.jsonl`(기다린 비용) ·
    `scenarios.json`(실현됐는데 대응했나). 마지막 것이 **영속 id가 없어 계산 불가**였고,
    그래서 `scenarios.py`를 만들었다.
    """
    since = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = ["## 회피 감사 — 결정을 미루고 있지 않은가", ""]

    # ① no_trade 비율 — 절반을 넘으면 규칙이 아니라 회피가 작동하고 있다는 신호다.
    all_runs = _load_runs()
    runs = [r for r in all_runs if str(r.get("session_date") or "") >= since]
    real_runs = [r for r in runs
                 if not str(r.get("outcome") or "").startswith(("skipped", "self_test"))]
    n_nt = sum(1 for r in real_runs if r.get("no_trade"))
    if not real_runs:
        # ★ "—"로 적으면 **무인 run이 한 번도 완주 기록을 남기지 않았다는 사실**이 숨는다.
        #   기권율이 없는 것과 0인 것은 다르고, 전자는 파이프라인이 안 돈 것이다.
        n_self = sum(1 for r in all_runs if str(r.get("outcome") or "") == "self_test")
        out.append(f"- **기권율** 산정 불가 — 이 기간 완주한 무인 run이 **0건**이다"
                   f"(원장 {len(all_runs)}행 중 self_test {n_self}행). "
                   f"기권율 0%가 아니라 **파이프라인이 무인으로 돌지 않았다**는 뜻이다.")
    else:
        ratio = f"{n_nt / len(real_runs) * 100:.0f}%"
        flag = " ← **절반을 넘었다. 규칙이 아니라 회피가 작동하고 있는지 본다**" \
            if n_nt / len(real_runs) > 0.5 else ""
        out.append(f"- **기권율** {n_nt}/{len(real_runs)} run = {ratio}{flag}")

    # ② 기다린 비용 — 그 기간 벤치마크 수익률. "기다리는 비용이 낮다"는 판단의 사후 검증이다.
    for mkt in ("KR", "US"):
        hist = [r for r in latest_per_day(_load_equity())
                if r.get("market") == mkt and str(r.get("date") or "") >= since]
        pair = [r for r in hist if r.get("shadow_benchmark_equity")]
        if len(pair) >= 2:
            a, b = pair[0], pair[-1]
            bm = (b["shadow_benchmark_equity"] / a["shadow_benchmark_equity"] - 1) * 100
            eq = (b["equity"] / a["equity"] - 1) * 100 if a.get("equity") else 0.0
            out.append(f"- **기다린 비용({mkt})** 벤치마크 {bm:+.2f}% vs 계좌 {eq:+.2f}% "
                       f"→ 초과 {eq - bm:+.2f}%p")
        else:
            out.append(f"- **기다린 비용({mkt})** 비교 구간이 부족하다(기록 {len(hist)}일) — "
                       f"'좋았다'로 읽지 말 것")

    # ③ 시나리오 실현 시 실제로 행동했는가 — 실현됐는데 또 안 샀으면 연기가 규칙을 이긴 것이다.
    out += _scenario_metric(days)
    out.append("")
    return out


def _scenario_metric(days: int) -> list:
    """`scenarios.py audit`을 그대로 불러 지표 줄만 옮긴다(계산을 두 곳에 두지 않는다)."""
    try:
        import scenarios as sc
        import argparse as _a
        import contextlib
        import io as _io
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            sc.cmd_audit(_a.Namespace(days=days, market=None, out=""))
        line = next((l for l in buf.getvalue().splitlines() if l.startswith("[회피]")), "")
    except Exception as e:                                    # noqa: BLE001
        return [f"- **시나리오 실현↔대응** 계산 실패({type(e).__name__}) — 실패를 '0건'으로 "
                f"읽지 말 것"]
    if not line:
        return ["- **시나리오 실현↔대응** 원장에 이 기간 승격분이 없다"]
    return [f"- **시나리오 실현↔대응** `{line}` — 미판정이 남아 있으면 회피가 "
            f"**안 보이는** 상태다(실현됐는데 대응 안 한 건은 `scenarios.py audit`이 이름으로 짚는다)"]


def daily(market: str, real: bool = False, stamp: str = None) -> int:
    client = KisClient(svr="real" if real else "paper", allow_real=real)
    watchlist = _read_json(WATCHLIST_PATH, {}) or {}

    if market == "KR":
        balance = client.domestic_balance()
    else:
        balance = client.overseas_balance()
        # 해외 현금은 예수금이 아니라 **구매력**이다. ingest와 같은 헬퍼를 쓴다 —
        # 예전엔 여기만 예수금(USD 0)을 읽어 equity=0이 되고, 그래서
        # 아래 `equity > 0` 앵커 조건이 영영 거짓이었다.
        bp = us_buying_power(client, watchlist)
        balance["cash"] = bp["cash"]
        balance["cash_field"] = bp["cash_field"]
        balance["deposit_only"] = bp["deposit_only"]
        if bp["error"]:
            print(f"  구매력 조회 실패(앵커·수익률 계산 불가): {bp['error']}", file=sys.stderr)
    positions = balance.get("positions", [])
    cash = balance.get("cash")
    # ★ 현금을 못 읽었으면 **행을 쓰지 않는다.** `cash or 0`으로 계산하면 equity가 0이 되고
    #   누적수익이 −100%로 찍힌다 — 실측(2026-09-16): KIS 게이트웨이 500 뒤 journal이
    #   "equity 0 USD · 누적 −100.00%"를 계산했다. 실패를 데이터로 바꾸면 성공 지표 ③이 오염된다.
    if cash is None:
        print("★ 잔고·구매력을 못 읽었다 — equity 행을 **쓰지 않는다**(0으로 쓰면 누적 −100%가 "
              "기록에 남는다). 브로커가 복구된 뒤 journal을 다시 돌려라.", file=sys.stderr)
        return 3
    equity = cash + sum(p.get("eval_amt", 0) or 0 for p in positions)
    bench = benchmark_price(client, market, watchlist)

    # 기준점은 최초 1회만 박는다.
    # ★ 못 읽는 파일을 기본값 `{}`으로 갈음하면 `key not in base`가 참이 되어
    #   **기준점이 오늘로 재설정되고 누적·초과수익이 0으로 리셋된다** — 이 프로젝트의
    #   성공 지표(③벤치마크 병기 기록의 완전성) 자체를 파괴하는 자리다. 그래서 여기서는
    #   '읽지 못함'과 '아직 없음'을 반드시 구분하고, 읽지 못했으면 **쓰지 않는다.**
    base, base_readable = _read_accum(BENCHMARK_PATH)
    key = market
    if not base_readable:
        print("★ 벤치마크 기준점을 읽지 못했다 — **새 기준점을 박지 않는다.** "
              "옆으로 치워 보존했으니 복구한 뒤 다시 돌려라. 지금 박으면 누적수익이 "
              "0으로 리셋되고 그게 이 프로젝트의 성공 지표다.", file=sys.stderr)
    elif key not in base and equity > 0 and bench.get("price"):
        base[key] = {
            "started_at": datetime.now(KST).isoformat(),
            "start_equity": equity,
            "benchmark_ticker": bench["ticker"],
            "start_benchmark_price": bench["price"],
        }
        _write_json(BENCHMARK_PATH, base)
        print(f"벤치마크 기준점 기록: {key} equity={equity:,.0f} "
              f"{bench['ticker']}={bench['price']:,.2f}")

    b = base.get(key, {})
    shadow = None
    if b.get("start_benchmark_price") and bench.get("price"):
        shadow = b["start_equity"] * (bench["price"] / b["start_benchmark_price"])

    session_date = (f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:6]}" if stamp
                    else datetime.now(KST).strftime("%Y-%m-%d"))

    history = [r for r in latest_per_day(_load_equity()) if r.get("market") == market]
    # 전일 대비를 재려면 '다른 날'의 행이어야 한다. 같은 날 두 번째 기록에서 직전 행을
    # 그냥 집으면 오늘을 오늘과 비교해 day_pnl_pct가 항상 0이 된다.
    prev = next((r for r in reversed(history) if r.get("date") != session_date), None)

    # 비용·회전은 누적이 핵심이다. "수익률이 나빴다"와 "비용이 먹었다"는 다른 문제이고,
    # 후자는 매매를 줄이면 되지만 전자는 판단을 고쳐야 한다. 구분하려면 둘 다 있어야 한다.
    ct = today_cost_and_turnover(balance, market, stamp)
    same_day = next((r for r in history if r.get("date") == session_date), None)
    cum_costs = sum(r.get("costs_today") or 0 for r in history
                    if r.get("date") != session_date) + (ct["fees"] or 0)
    cum_turnover = sum(r.get("turnover_today") or 0 for r in history
                       if r.get("date") != session_date) + (ct["turnover"] or 0)
    if same_day and ct["fees"] is None:
        # 오늘 값을 못 구했으면 앞서 기록한 오늘 값을 버리지 않는다.
        cum_costs += same_day.get("costs_today") or 0
    row = {
        "date": session_date,
        "ts": datetime.now(KST).isoformat(),
        "market": market,
        "currency": balance.get("currency", "KRW"),
        "equity": round(equity, 2),
        "cash": round(cash, 2) if cash is not None else None,
        "n_positions": len(positions),
        "benchmark": bench,
        "shadow_benchmark_equity": round(shadow, 2) if shadow else None,
        "positions": [
            {"ticker": p["ticker"], "name": p.get("name", ""), "qty": p["qty"],
             "price": p.get("price"), "eval_amt": p.get("eval_amt"), "pnl_pct": p.get("pnl_pct")}
            for p in positions
        ],
        "costs_today": ct["fees"],
        "turnover_today": round(ct["turnover"], 2) if ct["turnover"] is not None else None,
        "costs_source": ct["source"],
        "cum_costs": round(cum_costs, 2),
        "cum_turnover": round(cum_turnover, 2),
    }
    if prev and prev.get("equity"):
        row["day_pnl_pct"] = round((equity - prev["equity"]) / prev["equity"] * 100, 3)
    if b.get("start_equity"):
        start = b["start_equity"]
        row["cum_pnl_pct"] = round((equity - start) / start * 100, 3)
        # 비용이 시작 자산을 얼마나 갉았는지. 손익과 나란히 놓아야 "판단이 나빴다"와
        # "비용이 먹었다"가 구분된다.
        row["cost_drag_pct"] = round(-cum_costs / start * 100, 3)
        # 회전율 — 자산 대비 몇 번을 사고팔았나. 비용은 결국 이것에 비례한다.
        row["cum_turnover_x"] = round(cum_turnover / start, 3)
        if shadow:
            row["cum_benchmark_pct"] = round((shadow - start) / start * 100, 3)
            row["excess_pct"] = round(row["cum_pnl_pct"] - row["cum_benchmark_pct"], 3)

    EQUITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EQUITY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    update_peaks(positions, market)

    print(f"[{row['date']} {market}] equity {equity:,.0f} {row['currency']} "
          f"/ 현금 {'미확인' if cash is None else format(cash, ',.0f')} / {len(positions)}종목")
    if ct["turnover"]:
        fee_str = "미확인" if ct["fees"] is None else f"{ct['fees']:,.0f}"
        pct = (f" ({ct['fees'] / ct['turnover'] * 100:.3f}%)"
               if ct["fees"] and ct["turnover"] else "")
        print(f"  당일 체결 {ct['turnover']:,.0f} · 제비용 {fee_str}{pct}")
    if row.get("cum_costs"):
        print(f"  누적 제비용 {row['cum_costs']:,.0f} "
              f"(자산의 {abs(row.get('cost_drag_pct') or 0):.3f}%) · "
              f"회전 {row.get('cum_turnover_x') or 0:.2f}배")
    if row.get("cum_pnl_pct") is not None:
        line = f"  누적 {row['cum_pnl_pct']:+.2f}%"
        if row.get("cum_benchmark_pct") is not None:
            line += (f" vs 벤치마크 {row['cum_benchmark_pct']:+.2f}% "
                     f"(초과 {row['excess_pct']:+.2f}%p)")
        print(line)
    return 0


# ------------------------------------------------------------------ 주간 리포트

def _load_equity() -> list:
    if not EQUITY_PATH.exists():
        return []
    rows = []
    for line in EQUITY_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _max_drawdown(values: list) -> float:
    peak, mdd = None, 0.0
    for v in values:
        if v is None:
            continue
        peak = v if peak is None else max(peak, v)
        if peak:
            mdd = min(mdd, (v - peak) / peak * 100)
    return mdd


def _forecast_block(days: int) -> list:
    """★ 성공 지표 ④ — 전망 선행·적중 + 아쉬움 깔때기.

    ①규율 ②무사고 ③기록에 더한다. 선행 전망의 초과수익이 0 이하면 `forecast_score`가
    첫 줄에 그렇게 쓴다 — 설계 근거 조사(비용 차감 후 초과수익 없음)와 예측 목표 중
    어느 쪽이 맞는지는 이 절의 숫자가 가른다.
    """
    import io as _io, contextlib as _ctx
    sys.path.insert(0, str(HERE))
    out = ["## 전망 — 성공 지표 ④ (앞을 보고 있는가)", ""]
    try:
        import market_map as mm
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            mm.forecast_score(mm._read(mm.MAP, {"axes": []}), days)
        out += [l for l in buf.getvalue().splitlines() if not l.startswith("## ")]
    except Exception as e:                    # noqa: BLE001 — 리포트가 이것 때문에 죽으면 안 된다
        out.append(f"*(전망 채점 실패: {type(e).__name__}: {e})*")
    out.append("")
    try:
        import regret
        out += regret.weekly(days).splitlines()
    except Exception as e:                    # noqa: BLE001
        out.append(f"*(아쉬움 집계 실패: {type(e).__name__}: {e})*")
    out.append("")
    return out


def weekly(days: int = 7) -> int:
    rows = _load_equity()
    if not rows:
        print("equity_curve.jsonl이 비어 있다 — 아직 리포트할 것이 없다.", file=sys.stderr)
        return 1

    since = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = [r for r in rows if r.get("date", "") >= since]

    # 규율 지표는 거래 기록에서 센다.
    trades, disc_sells, failed = [], 0, 0
    for p in sorted(JOURNAL_DIR.glob("trades_*.json")):
        data = _read_json(p, {}) or {}
        if data.get("generated_at", "")[:10] < since:
            continue
        for t in data.get("trades", []):
            trades.append(t)
            if t.get("source") == "discipline":
                disc_sells += 1
            if t.get("status") == "FAILED":
                failed += 1

    rejected_reasons = {}
    for p in sorted((HERE / "signals").glob("approved_*.json")):
        data = _read_json(p, {}) or {}
        if data.get("generated_at", "")[:10] < since:
            continue
        for r in data.get("rejected", []):
            key = str(r.get("why", ""))[:60]
            rejected_reasons[key] = rejected_reasons.get(key, 0) + 1

    today = datetime.now(KST).strftime("%y%m%d")
    lines = [
        f"# 주간 리포트 — {datetime.now(KST):%Y-%m-%d}",
        "",
        f"> 대상 기간: 최근 {days}일 (since {since}) · 모의투자",
        "",
        "## 성과 (벤치마크 병기)",
        "",
        "| 시장 | equity | 누적 | 벤치마크 누적 | 초과 | MDD |",
        "|---|---|---|---|---|---|",
    ]
    for market in ("KR", "US"):
        m = [r for r in rows if r.get("market") == market]
        if not m:
            continue
        last = m[-1]
        mdd = _max_drawdown([r.get("equity") for r in m])
        cum = last.get("cum_pnl_pct")
        bmk = last.get("cum_benchmark_pct")
        exc = last.get("excess_pct")
        lines.append(
            f"| {market} | {last.get('equity', 0):,.0f} {last.get('currency', '')} "
            f"| {'미확인' if cum is None else f'{cum:+.2f}%'} "
            f"| {'미확인' if bmk is None else f'{bmk:+.2f}%'} "
            f"| {'미확인' if exc is None else f'{exc:+.2f}%p'} "
            f"| {mdd:.2f}% |"
        )

    lines += [
        "",
        "## 규율 지표 (이 시스템의 실제 성공 지표)",
        "",
        f"- 총 주문: **{len(trades)}건** (그중 규율 SELL {disc_sells}건)",
        f"- 주문 실패: **{failed}건** — 0이어야 정상",
        f"- 기록된 run: {len(recent)}회",
        f"- risk_guard 거부: **{sum(rejected_reasons.values())}건**",
        "",
    ]
    if rejected_reasons:
        lines.append("거부 사유 분포:")
        lines.append("")
        for why, n in sorted(rejected_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"- {n}회 — {why}")
        lines.append("")

    lines += _avoidance_block(days)
    lines += _forecast_block(days)
    lines += [
        "## 판정",
        "",
        f"- 무사고: {'예' if failed == 0 else f'**아니오 — 실패 {failed}건, 원인 확인 필요**'}",
        "- 실계좌 논의 조건(6개월·하락구간 포함·사고 0·규율 100%)까지 남은 것은 "
        "`AI자동매매_공개사례_성능조사_v1_0.md` §5 참조",
        "",
    ]

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    out = WEEKLY_DIR / f"주간리포트_{today}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"주간 리포트: {out}")
    print("\n".join(lines[:20]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="저널 기록 및 주간 리포트")
    ap.add_argument("--daily", action="store_true", help="오늘 equity를 기록한다")
    ap.add_argument("--weekly", action="store_true", help="주간 리포트를 만든다")
    ap.add_argument("--market", default="kr", choices=["kr", "us"])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--stamp", metavar="YYMMDD",
                    help="산출물 파일명에 쓸 세션 날짜. 미국장은 KST 자정을 넘겨 "
                         "한 run이 두 날짜로 갈라지므로 호출자가 고정해 넘긴다.")
    args = ap.parse_args()

    if args.weekly:
        return weekly(args.days)
    if args.daily:
        return daily(args.market.upper(), args.real, args.stamp)
    ap.error("--daily 또는 --weekly 중 하나가 필요하다")


if __name__ == "__main__":
    sys.exit(main())
