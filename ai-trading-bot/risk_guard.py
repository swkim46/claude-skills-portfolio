#!/usr/bin/env python3
"""
결정론 리스크 레이어 — 시스템 전체의 안전이 걸린 단일 모듈.

LLM은 제안만 하고, 집행 여부는 전부 여기서 결정된다. 이 파일에는 LLM 호출도,
네트워크 호출도 없다. 입력은 파일뿐이고 판정은 순수 함수다 — 그래야 거부 케이스를
오프라인에서 전건 테스트할 수 있다.

설계 근거(AI자동매매_공개사례_성능조사_v1_0.md):
- TradeTrap: 단일 컴포넌트의 작은 교란이 에이전트 결정 루프를 타고 번져
  극단적 집중·폭주·대형 낙폭을 유발한다 → 스키마가 깨졌다는 것 자체를 상류 오염
  신호로 보고 부분 구제 없이 전체를 거부한다(fail-closed).
- StockBench: 하락장에서 전 LLM 에이전트가 수동 기준선에 미달 → 서킷브레이커.
- 자동화의 실증 효용은 알파가 아니라 규율 → 손절/트레일링 SELL은 LLM 제안과
  무관하게 생성되고, 일일 주문 건수 제한에서도 면제된다. 규율이 리밋에 막히면
  리밋이 규율을 잡아먹는다.

사용:
    python3 risk_guard.py signals/signal_260903_kr.json --snapshot data/snapshot_260903_kr.json
    python3 risk_guard.py --discipline-only --snapshot data/snapshot_260903_kr.json
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 거래소 규칙(호가단위)만 가져온다 — 순수 함수라 네트워크도 상태도 없다.
# 이 모듈이 오프라인 순수 판정이라는 성질은 그대로다.
import peaks as peaks_store
from kis_client import snap_kr_price, account_mode

HERE = Path(__file__).parent
CONFIG_DIR = HERE / "config"
CALENDAR_PATH = HERE / "journal" / "calendar.json"


DATA_DIR = HERE / "data"
JOURNAL_DIR = HERE / "journal"


def _acct_family(mode) -> str:
    """계좌 계열 — 'paper' 또는 'real'(real · real_readonly). 표시가 없는 옛 행·스냅샷은 모의다(실계좌 전환 전 기록)."""
    return "paper" if str(mode or "paper") == "paper" else "real"


def _latest_curve_row(market: str, family: str = None):
    """`journal/equity_curve.jsonl`에서 이 시장의 마지막 행(없으면 None). 못 읽는 줄은 건너뛴다.
    `family`(2026-10-15)를 주면 그 계좌 계열의 행만 — 실계좌 run이 모의 자산 행을 반대편으로 읽지 않게."""
    path = JOURNAL_DIR / "equity_curve.jsonl"
    if not path.exists():
        return None
    last = None
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if family and _acct_family(r.get("account_mode")) != family:
            continue
        if str(r.get("market", "")).upper() == market.upper() and r.get("cash") is not None:
            last = r
    return last


def _fx_stale(fx_at, hours: float = 8.0) -> str:
    """환율 as-of가 낡았으면 그 사실을 문구로 — 합산 분모가 그만큼 흔들린다(2026-09-23 1.9% 사례)."""
    if not fx_at or fx_at == "이번 run":
        return ""
    try:
        dt = datetime.fromisoformat(str(fx_at))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    age_h = (datetime.now(KST) - dt).total_seconds() / 3600
    if age_h < hours:
        return ""
    return (f" · ★ 환율이 {age_h:.0f}시간 전 값이다 — 합산 분모(반대편 자산)가 그만큼 흔들린다."
            f" 캡처를 다시 돌리면 오늘 값이 들어온다")


# FB-261010-5 — 새 종목을 이 시장 보유 평균보다 작게 살 때 받아 주는 사유(그 밖은 '키우거나 사지 마라').
SMALL_WHY_KINDS = ("stop_close", "binary_event", "one_share", "liquidity", "order_cap")


def order_cap_cfg(limits: dict, mode: str = None) -> dict:
    """주문당 상한 설정 — 적용 대상이면 {max_krw, max_shares}, 아니면 {}.

    2026-10-12 사용자 결정: 한 종목 한 번 매수 100만 원, 1주 값이 그보다 크면 금액 대신 최대 2주. 모의 계좌에는
    `apply_in_paper`가 true일 때만 건다(모의 자금 규모가 달라 상한이 전략을 왜곡한다).
    """
    oc = limits.get("order_cap") or {}
    try:
        cap = float(oc.get("max_krw_per_order") or 0)
    except (TypeError, ValueError):
        cap = 0.0
    if cap <= 0:
        return {}
    mode = mode or account_mode(limits)
    if mode == "paper" and not oc.get("apply_in_paper"):
        return {}
    return {"max_krw": cap, "max_shares": int(oc.get("max_shares_when_price_over_cap") or 1), "mode": mode}


def order_cap_qty(price: float, fx: float, cfg: dict) -> tuple:
    """이 가격에서 한 주문에 살 수 있는 최대 수량과 그 이유. (qty, why) — cfg가 비면 (None, '')."""
    if not cfg:
        return None, ""
    px_krw = float(price) * float(fx or 0)
    if px_krw <= 0:
        return 0, "주문당 상한 — 원화 가격을 못 잰다(환율 미확인) · 상한을 검사할 수 없어 매수를 보류한다"
    if px_krw > cfg["max_krw"]:
        return cfg["max_shares"], (f"주문당 상한 — 1주 {px_krw:,.0f}원 > {cfg['max_krw']:,.0f}원이라 "
                                   f"금액 대신 최대 {cfg['max_shares']}주")
    return int(cfg["max_krw"] // px_krw), f"주문당 상한 {cfg['max_krw']:,.0f}원(1주 {px_krw:,.0f}원)"


def apply_order_cap(orders: list, limits: dict, market: str, fx: float, mode: str = None) -> tuple:
    """★ 매수 주문마다 주문당 상한을 건다 — 크기가 다 정해진 **마지막**(gap 채우기 뒤)에. 반환 (orders, rejected, notes, trimmed_amt).

    매도(손절·추적·회전·이익실현)는 깎지 않는다 — 위험을 줄이는 주문이고, 깎으면 손절이 덜 나간다.
    깎인 행에는 `order_capped`를 남긴다 — execute가 전송 직전 같은 계산으로 다시 막는다(이중 검사).
    """
    cfg = order_cap_cfg(limits, mode)
    if not cfg:
        return orders, [], [], 0.0
    mult = 1.0 if (market or "KR").upper() == "KR" else float(fx or 0)
    out, rejected, notes, trimmed = [], [], [], 0.0
    for o in orders:
        if o.get("action") != "BUY":
            out.append(o)
            continue
        cap_q, why = order_cap_qty(float(o["price"]), mult, cfg)
        q = int(o.get("qty") or 0)
        if cap_q is None or q <= cap_q:
            out.append(o)
            continue
        if cap_q <= 0:
            rejected.append({"ticker": o["ticker"], "action": "BUY", "why": why})
            continue
        cut = (q - cap_q) * float(o["price"])
        trimmed += cut
        note = f"{why} — {q}→{cap_q}주"
        notes.append(f"{o['ticker']} {note}")
        out.append({**o, "qty": cap_q,
                    "order_capped": {"from_qty": q, "to_qty": cap_q, "cap_krw": cfg["max_krw"],
                                     "max_shares": cfg["max_shares"], "fx": mult, "trimmed_amt": round(cut, 2),
                                     "why": note},
                    "reasons": list(o.get("reasons") or []) + [note]})
    return out, rejected, notes, trimmed


def portfolio_equity(this_market: str, balance: dict) -> dict:
    """**양 시장 자산을 원화로 합산한다.** 모든 비중의 분모는 이것이어야 한다.

    ★ 왜. 실계좌는 한 풀로 쓴다. 지금 모의계좌가 둘로 갈려 있는 것은 시뮬레이터의
    사정이지 설계 기준이 아니다. 그런데 예전에는 **그 시장 `balance`만** 분모로 써서
    같은 퍼센트가 시장마다 다른 금액을 뜻했다.
    *실측(2026-09-11): KR 998만원 · US 1억 3,382만원 — 미국이 **13.4배**. 그래서
    `per_position_max_pct: 12`가 KR 120만원 · US 1,606만원이었고, 미국 한 종목이
    국내 계좌 전체의 1.6배까지 갈 수 있었다. 확신도 표도 뒤집혔다 — 미국 2%(268만원)가
    국내 8%(80만원)보다 3.3배 크다.*

    반환 `{krw, ok, asof, fx, parts, why}`. **못 재면 `ok=False`**이고 호출자는
    그 시장 자산으로 물러선다 — 그쪽이 항상 더 작아 **보수적**이다('없음'이 아니라 '못 쟀다').
    """
    this_market = (this_market or "KR").upper()
    parts, asof, fx, missing = {}, [], None, []

    def _equity_of(bal):
        return (bal.get("cash") or 0) + sum(p.get("eval_amt", 0) or 0
                                            for p in (bal.get("positions") or []))

    # 이번 시장은 손에 있는 balance를 쓴다(같은 run의 값이라 가장 정확하다).
    here_eq = _equity_of(balance)
    # ★ 환율은 **가장 최신 한 시점**을 쓴다(2026-09-23). 이 run의 스냅샷에 환율이 있으면 그것이 최신이다 —
    #   예전엔 KR run이 반대편 미국 스냅샷의 환율을 썼고 반나절 낡아 합산 분모가 1.9% 흔들렸다.
    fx_at = None
    if balance.get("exchange_rate"):
        fx = balance.get("exchange_rate")
        fx_at = balance.get("exchange_rate_at") or "이번 run"
    parts[this_market] = {"equity": here_eq, "currency": balance.get("currency"),
                          "cash": balance.get("cash") or 0,
                          "invested": sum(p.get("eval_amt", 0) or 0
                                          for p in (balance.get("positions") or [])),
                          "positions": balance.get("positions") or [],
                          "source": "이번 run 스냅샷"}

    other = "US" if this_market == "KR" else "KR"
    # ★ 파일명 정렬로 최신을 고르면 안 된다 — `snapshot_REHEARSAL_kr.json`이 'R' > '2'라서
    # 날짜 파일들보다 뒤로 정렬돼, **리허설 잔고가 실계좌 분모로 들어갔다**(2026-09-11 실측:
    # 미국 run이 삼성전자 108만원 보유를 실제 KR 계좌로 읽었다). 그래서
    # ① 날짜 스탬프(6자리) 파일만 후보로 받고 ② 파일 안의 `generated_at`으로 고른다.
    cands = [f for f in DATA_DIR.glob(f"snapshot_*_{other.lower()}.json")
             if re.fullmatch(r"snapshot_\d{6}_[a-z]{2}\.json", f.name)]
    # ★ 계좌 계열로 거른다(2026-10-15 · 실계좌 전환) — 모의 미국 스냅샷(보유 약 $80k)을 실계좌 국내 run의 반대편으로 더하면
    #   포트폴리오가 거의 찬 것으로 보여 첫 실전 run이 거의 안 산다. 표시 없는 옛 스냅샷은 모의다.
    try:
        fam = _acct_family(account_mode(json.loads(LIMITS_PATH.read_text(encoding="utf-8"))))
    except (OSError, json.JSONDecodeError):
        fam = _acct_family(account_mode())
    dated, other_mode = [], 0
    for f in cands:
        try:
            sj = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue          # 못 읽는 파일은 후보에서 빼고, 다른 후보로 계속 간다
        if _acct_family(sj.get("account_mode")) != fam:
            other_mode += 1
            continue
        dated.append((sj.get("generated_at") or "", f))
    dated.sort()
    snaps = [f for _, f in dated]
    if not snaps:
        missing.append(f"{other} 스냅샷이 없다"
                       + (f" (날짜 없는 파일 {len(cands) - len(dated) - other_mode}건은 후보가 아니다)"
                          if len(cands) - other_mode > len(dated) else "")
                       + (f" (다른 계좌 계열 {other_mode}건은 후보가 아니다 — 지금 {fam})" if other_mode else ""))
    else:
        try:
            s = json.loads(snaps[-1].read_text(encoding="utf-8"))
            ob = s.get("balance") or {}
            src_name = snaps[-1].name
            # ★ 스냅샷보다 **저널 마지막 행이 더 새로우면** 그것을 쓴다 — 9/15 US 스냅샷(23:14)은 VST 체결
            #   전이라 9/16 KR run이 투자비중 1.1%(실제 5.1%)로 계산했다. 저널 행은 체결 뒤 잔고다.
            #   환율은 저널 행에 없으니 스냅샷 것을 그대로 쓴다.
            row = _latest_curve_row(other, fam)
            if row and str(row.get("ts") or "") > str(s.get("generated_at") or ""):
                ob = {"currency": row.get("currency") or ob.get("currency"),
                      "cash": row.get("cash"), "positions": row.get("positions") or [],
                      "exchange_rate": ob.get("exchange_rate")}
                s = {"generated_at": row.get("ts")}
                src_name = f"equity_curve {str(row.get('ts'))[:16]}"
            # 며칠 지난 잔고는 쓰되 **몇 일 지났는지 반드시 사유에 적는다.**
            gen = (s.get("generated_at") or "")[:10]
            try:
                age = (datetime.now(KST).date() - date.fromisoformat(gen)).days
            except ValueError:
                age = None
            if age is not None and age > 14:
                missing.append(f"{other} 스냅샷이 {age}일 지났다 — 분모로 쓸 수 없다")
            elif age:
                asof.append(f"{other} {age}일 전")
            parts[other] = {"equity": _equity_of(ob), "currency": ob.get("currency"),
                            "cash": ob.get("cash") or 0,
                            "invested": sum(p.get("eval_amt", 0) or 0
                                            for p in (ob.get("positions") or [])),
                            "positions": ob.get("positions") or [],
                            "source": src_name}
            asof.append(f"{other} {s.get('generated_at', '?')[:16]}")
            if not fx and ob.get("exchange_rate"):
                fx = ob.get("exchange_rate")
                fx_at = (s.get("generated_at") or "")[:16]
        except (json.JSONDecodeError, OSError) as e:
            missing.append(f"{other} 스냅샷을 읽을 수 없다({type(e).__name__})")

    # ★ 통합증거금 실계좌(2026-10-12): 국내·미국 현금이 **한 주머니**다 — 국내 스냅샷의 원화 주문가능과 미국 스냅샷의
    #   달러 주문가능은 같은 돈을 두 통화로 잰 것이다. 둘 다 더하면 현금이 두 번 잡혀 자산이 부풀고 투자비중이 작게 나온다.
    #   이번 run의 값(가장 새롭다)만 세고 반대편은 보유 평가액만 센다.
    if balance.get("cash_shared") and other in parts:
        op = parts[other]
        op["shared_cash_dropped"] = op.get("cash") or 0
        op["cash"] = 0
        op["equity"] = op.get("invested") or 0
        asof.append(f"현금 공유 — {other} 현금 {op['shared_cash_dropped']:,.2f} 제외(통합증거금)")
    if not fx:
        missing.append("환율을 못 구했다(미국 스냅샷의 exchange_rate)")
    if missing:
        return {"krw": None, "ok": False, "asof": " · ".join(asof), "fx": fx, "parts": parts,
                "why": "포트폴리오 자산을 **못 쟀다** — " + " / ".join(missing)
                       + ". 그 시장 자산으로 물러선다(더 작으므로 보수적)."}

    krw = cash_krw = inv_krw = 0.0
    for m, v in parts.items():
        mult = fx if m == "US" else 1.0
        v["krw"] = v["equity"] * mult
        krw += v["krw"]
        cash_krw += (v.get("cash") or 0) * mult
        inv_krw += (v.get("invested") or 0) * mult
    return {"krw": krw, "cash_krw": cash_krw, "invested_krw": inv_krw,
            "ok": True, "asof": " · ".join(asof), "fx": fx, "parts": parts,
            "fx_at": fx_at,
            "why": (f"포트폴리오 자산 {krw:,.0f}원 = "
                    + " + ".join(f"{m} {v['krw']:,.0f}" for m, v in parts.items())
                    + f" · 현금 {cash_krw:,.0f} · 투자 {inv_krw:,.0f}"
                    + f" (환율 {fx:,.2f}, as-of {fx_at or '이번 run'}{_fx_stale(fx_at)}"
                    + f" · 잔고 as-of {' · '.join(asof) or '이번 run'})")}


def pf_local(pf: dict, market: str, fallback: dict) -> dict:
    """포트폴리오 총량을 **그 시장 통화로** 환산해 돌려준다.

    ★ 왜 통화를 맞추는가 — 분모만 포트폴리오로 바꾸고 금액은 그 시장 통화로 두면
    `target_amt = equity * pct`와 `price` 비교가 깨진다(원화 분모 × 달러 가격).
    그래서 **분모는 포트폴리오, 단위는 그 시장 통화**로 맞춘다.
    못 쟀으면 그 시장 값으로 물러선다 — 항상 더 작아 보수적이다.
    """
    market = (market or "KR").upper()
    if not pf.get("ok"):
        inv = sum(p.get("eval_amt", 0) or 0 for p in (fallback.get("positions") or []))
        cash = fallback.get("cash") or 0
        return {"equity": cash + inv, "cash": cash, "invested": inv,
                "invested_pct": (inv / (cash + inv) * 100) if (cash + inv) else 0.0,
                "scope": "이 시장만(포트폴리오를 못 쟀다)"}
    div = pf["fx"] if market == "US" else 1.0
    return {"equity": pf["krw"] / div, "cash": pf["cash_krw"] / div,
            "invested": pf["invested_krw"] / div,
            "invested_pct": (pf["invested_krw"] / pf["krw"] * 100) if pf["krw"] else 0.0,
            "scope": "포트폴리오 전체"}


def leverage_factor(name: str, ticker: str) -> tuple:
    """(배수, 인버스 여부). `config/universe_rules.json:leverage_patterns`로 이름·티커를 본다.

    ★ 왜 — 2026-09-14 사용자 결정으로 레버리지·인버스 ETF를 **허용**했다(선물형만 배제).
    막지 않는 대신 배수를 알아야 한다: 3배 ETF 4%는 기초지수 12% 베팅이고, 인버스는 같은 축의
    집중을 **줄인다**. 판별 못 하면 (1, False) — 개별주로 취급한다.
    """
    try:
        rules = json.loads((CONFIG_DIR / "universe_rules.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 1, False
    pats = rules.get("leverage_patterns") or {}
    hay = f"{name or ''} {ticker or ''}".upper()
    inv = any(x.upper() in hay for x in pats.get("inverse", []))
    for f in ("3", "2"):
        if any(x.upper() in hay for x in pats.get(f, [])):
            return int(f), inv
    return (1, inv)


def axis_index() -> dict:
    """ticker → 그 종목이 **양(+) 노출**을 갖는 축 id 목록.

    출처는 `journal/market_map.json:axes[].names[]` — 이미 양 시장 종목을
    한 축에 담고 있는 **유일한 교차 시장 조인 키**다.
    `sign == "-"`(역노출)은 집중으로 세지 않는다 — 반대로 움직이는 보유를
    같은 축의 집중으로 더하면 헤지를 집중이라고 부르는 셈이 된다.
    """
    try:
        axes = (json.loads((JOURNAL_DIR / "market_map.json")
                           .read_text(encoding="utf-8")) or {}).get("axes") or []
    except (json.JSONDecodeError, OSError):
        return {}
    # ★ 부호를 **살려서** 담는다 — 예전엔 `sign "-"`를 빼서 헤지를 집중으로 안 셌는데, 인버스 ETF를
    #   허용하면서(2026-09-14) 역노출은 '안 세는 것'이 아니라 **음수로 세는 것**이 맞아졌다.
    #   SOXS를 사면 반도체 축 집중이 줄어야 한다. 값은 {ticker: [(axis_id, ±1), …]}.
    idx = {}
    for a in axes:
        aid = a.get("id")
        if not aid:
            continue
        for n in (a.get("names") or []):
            tk = str(n.get("ticker") or "").strip()
            if tk:
                idx.setdefault(tk, []).append((aid, -1 if (n.get("sign") or "+") == "-" else 1))
    return idx


def axes_of(idx: dict, ticker: str) -> list:
    """양(+) 노출 축 id만 — 집중 상한 검사용."""
    return [a for a, s in idx.get(str(ticker).strip(), []) if s > 0]


def axis_exposure(pf: dict, market: str, idx: dict) -> dict:
    """축별 보유 금액 — **양 시장 합산**, 이 시장 통화. 반환 {axis_id: 금액}."""
    market = (market or "KR").upper()
    div = pf["fx"] if (market == "US" and pf.get("ok")) else 1.0
    exp = {}
    for m, v in (pf.get("parts") or {}).items():
        mult = pf.get("fx", 1.0) if m == "US" else 1.0
        for pos in (v.get("positions") or []):
            amt = (pos.get("eval_amt") or 0) * mult / div
            # 레버리지는 배수만큼 — 부호는 **지도의 sign**이 정한다(SOXS는 지도에 −로 실려 있다).
            # 이름의 인버스 판별은 상한·호흡 규칙용이고 여기서 또 곱하면 이중 부호가 된다.
            f, _inv = leverage_factor(pos.get("name", ""), pos.get("ticker", ""))
            for aid, sign in idx.get(str(pos.get("ticker") or "").strip(), []):
                exp[aid] = exp.get(aid, 0.0) + amt * f * sign
    return exp


# ★ 2026-09-22 — `allocation_multiplier`(미달 배수)·`halve_window`(이벤트 계수)·`other_market_reserve`(상수 예약)를
#   지웠다. 주식 비율은 상수가 아니라 **모델이 이어받아 판단하는 상태**(`allocation.py` 원장)이고, 크기는 제안의
#   `size_why`가 정하며, 판단한 비율에 못 미치면 `apply_run_limits`가 증분을 비례로 키워 채운다. 이벤트는 줄일 이유가
#   아니라 베팅할 자리다(사용자 09-22).

LIMITS_PATH = CONFIG_DIR / "limits.json"
WATCHLIST_PATH = CONFIG_DIR / "watchlist.json"
KILL_PATH = CONFIG_DIR / "KILL"
SIGNALS_DIR = HERE / "signals"
PEAKS_PATH = HERE / "journal" / "position_peaks.json"

KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = "1.0"
VALID_ACTIONS = ("BUY", "SELL", "HOLD")


class Rejection(Exception):
    """run 전체를 거부해야 하는 사유. 잡아서 '그날 거래 없음'으로 끝낸다."""


# ------------------------------------------------------------------ 로딩

def _read_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise Rejection(f"{what} 파일이 없다: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Rejection(f"{what} JSON 파싱 실패({path.name}): {e}")


def _parse_dt(value: str, what: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise Rejection(f"{what} 시각 형식 오류: {value!r}")
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------ 스키마 검증(fail-closed)

def real_latch_needs_cap(limits: dict) -> str:
    """★ 실전 래치가 열렸는데 하루에 나갈 수 있는 금액의 상한이 없으면 그 사실을 문자열로(없으면 '').

    2026-09-23에 일일 상한을 지운 것은 **모의 계좌 전제**의 결정이다(사용자: "없애"). 실계좌로 넘어갈 때
    상한 없이 열리는 경로를 막는다 — 그때는 사고의 폭발 반경이 진짜 돈이다.
    """
    if not limits.get("real_trading_enabled"):
        return ""
    # ★ 2026-10-12 사용자 결정 — 실전의 필수 상한은 일일 상한이 아니라 **주문당 상한**(한 종목 한 번 100만 원)이다.
    if order_cap_cfg(limits, "real"):
        return ""
    return ("실전 래치(`real_trading_enabled: true`)가 열렸는데 주문당 상한이 없다 — "
            "`order_cap.max_krw_per_order`(2026-10-12 사용자 결정: 100만 원)를 정하고 다시 돌려라")


def validate_signal(sig: dict, limits: dict, now: datetime, this_id: str = None, max_age: float = None) -> None:
    """하나라도 어긋나면 run 전체 거부. 부분 구제 없음.
    `max_age`는 재투자 pass(`refill.py`)가 넘긴다 — 국내 run은 매도가 오후에 나면 시그널이 180분을 넘긴다(2026-10-15)."""
    bad_latch = real_latch_needs_cap(limits)
    if bad_latch:
        raise Rejection(bad_latch)
    if not isinstance(sig, dict):
        raise Rejection("시그널 최상위가 객체가 아니다")
    if sig.get("schema_version") != SCHEMA_VERSION:
        raise Rejection(
            f"schema_version 불일치: 기대 {SCHEMA_VERSION}, 실제 {sig.get('schema_version')!r}"
        )
    for field in ("run_id", "market", "generated_at", "proposals"):
        if field not in sig:
            raise Rejection(f"필수 필드 누락: {field}")
    if sig["market"] not in ("KR", "US"):
        raise Rejection(f"market은 KR|US 여야 한다: {sig['market']!r}")

    age_min = (now - _parse_dt(sig["generated_at"], "generated_at")).total_seconds() / 60
    if max_age is None:
        max_age = limits.get("signal_max_age_minutes", 180)
    if age_min > max_age:
        raise Rejection(f"시그널이 낡았다: {age_min:.0f}분 전 생성 (허용 {max_age}분)")
    if age_min < -5:
        raise Rejection(f"시그널 생성 시각이 미래다: {age_min:.0f}분")

    proposals = sig["proposals"]
    if not isinstance(proposals, list):
        raise Rejection("proposals가 배열이 아니다")

    # ★ 배분 판단 블록 — 주식 비율은 상수가 아니라 이어받는 판단이다(2026-09-22). 없거나 직전 판단을 안 이었으면 거부.
    if this_id is not None:
        import allocation as _al
        errs = _al.validate(sig.get("allocation"), this_id)
        if errs:
            raise Rejection("allocation — " + " / ".join(errs))

    for i, p in enumerate(proposals):
        tag = f"proposals[{i}]"
        if not isinstance(p, dict):
            raise Rejection(f"{tag}가 객체가 아니다")
        for field in ("ticker", "action", "thesis", "confidence"):
            if field not in p:
                raise Rejection(f"{tag}: 필수 필드 누락 {field}")
        if p["action"] not in VALID_ACTIONS:
            raise Rejection(f"{tag}: action은 {VALID_ACTIONS} 중 하나여야 한다: {p['action']!r}")
        if not isinstance(p["confidence"], (int, float)) or not 0 <= p["confidence"] <= 1:
            raise Rejection(f"{tag}: confidence는 0~1 실수여야 한다: {p['confidence']!r}")
        if not str(p.get("thesis", "")).strip():
            raise Rejection(f"{tag}: thesis가 비어 있다 (근거 없는 제안은 받지 않는다)")
        ev = p.get("evidence", [])
        if not isinstance(ev, list):
            raise Rejection(f"{tag}: evidence가 배열이 아니다")
        for j, e in enumerate(ev):
            if not isinstance(e, dict) or "verdict" not in e:
                raise Rejection(f"{tag}.evidence[{j}]: verdict 없는 근거는 받지 않는다")

    # ★ 재투자 후보(2026-10-15) — "현금이 생기면 다음에 살 것" 순서 목록. 제안과 같은 검증을 받는다(근거·판정표·크기 사유).
    cands = sig.get("refill_candidates")
    if cands is not None:
        if not isinstance(cands, list):
            raise Rejection("refill_candidates가 배열이 아니다")
        for i, p in enumerate(cands):
            tag = f"refill_candidates[{i}]"
            if not isinstance(p, dict):
                raise Rejection(f"{tag}가 객체가 아니다")
            for field in ("ticker", "action", "thesis", "confidence"):
                if field not in p:
                    raise Rejection(f"{tag}: 필수 필드 누락 {field}")
            if p["action"] != "BUY":
                raise Rejection(f"{tag}: 재투자 후보는 BUY만 — {p['action']!r}")
            if not isinstance(p["confidence"], (int, float)) or not 0 <= p["confidence"] <= 1:
                raise Rejection(f"{tag}: confidence는 0~1 실수여야 한다: {p['confidence']!r}")
            if not str(p.get("thesis", "")).strip():
                raise Rejection(f"{tag}: thesis가 비어 있다 (근거 없는 후보는 받지 않는다)")
            ev = p.get("evidence", [])
            if not isinstance(ev, list) or any(not isinstance(e, dict) or "verdict" not in e for e in ev):
                raise Rejection(f"{tag}: evidence는 verdict가 있는 근거 배열이어야 한다")


# ------------------------------------------------------------- 규율 SELL 생성

def discipline_sells(positions: list, limits: dict, peaks: dict) -> list:
    """손절선·트레일링 위반 보유종목의 강제 SELL. LLM 제안과 무관하게 생성된다.

    ★ 둘 다 null이면 기계적 손절은 **꺼진다**(2026-09-07 사용자 결정 — 소액 운용이고
    손절은 기계가 아니라 전략 판단으로 하겠다는 뜻). 끄더라도 눈은 감지 않는다:
    `soft_alert_pct` 이하로 빠진 보유는 ingest가 재료에 '주의'로 띄우므로, 팔지 말지는
    분석 단계에서 근거를 달고 제안하게 된다.
    """
    stop_loss = limits.get("stop_loss_pct")
    trailing = limits.get("trailing_stop_from_peak_pct")
    if stop_loss is None and trailing is None:
        return []

    out = []
    for pos in positions:
        qty = int(pos.get("qty", 0))
        if qty <= 0:
            continue
        ticker = pos.get("ticker", "")
        reasons = []

        pnl = pos.get("pnl_pct")
        if stop_loss is not None and isinstance(pnl, (int, float)) and pnl <= stop_loss:
            reasons.append(f"손절선 도달 {pnl:+.2f}% ≤ {stop_loss:+.2f}%")

        peak = peaks.get(ticker, {}).get("peak_price")
        price = pos.get("price") or 0
        if trailing is not None and peak and price > 0:
            from_peak = (price - peak) / peak * 100
            if from_peak <= trailing:
                reasons.append(
                    f"고점 대비 {from_peak:+.2f}% ≤ {trailing:+.2f}% (고점 {peak:,.2f})"
                )

        if reasons:
            out.append({
                "ticker": ticker,
                "name": pos.get("name", ""),
                "market": pos.get("market", ""),
                "excd": pos.get("excd"),
                "action": "SELL",
                "qty": qty,
                "price": price,
                "source": "discipline",
                "reasons": reasons,
                "exempt_from_count_limit": True,
            })
    return out


def audit_reversal_sells(snapshot: dict, market: str, already: set = None) -> list:
    """**감사 소급 — 주문 뒤 감사가 매수 근거를 뒤집은 종목은 기계가 판다**(2026-10-13 사용자: "매매 의사결정을 바꿔야 하는
    감사 결과가 나오면 소급 적용"). `journal/audit_reversals.jsonl`의 이 시장 대기 행마다 SELL — 수량은 남은 수량과 보유 중 작은 쪽,
    보유가 0이면 행을 void로 닫는다. 같은 종목에 이미 규율 매도가 있으면 이번엔 건너뛴다(다음 run이 남은 수량을 본다).
    체결 확정은 fill이 원장에 적는다(`reversals.settle_trade`). 원장이 깨져도 승인 전체를 막지 않는다."""
    already = already or set()
    try:
        import reversals as rv
        rows = rv.pending(market, JOURNAL_DIR)
    except Exception as e:                       # noqa: BLE001 — 원장 오류가 규율 매도·승인을 막지 않게
        print(f"  ★ 감사 소급 원장을 읽지 못했다 — {type(e).__name__}: {e}", file=sys.stderr)
        return []
    positions = {p["ticker"]: p for p in (snapshot.get("balance") or {}).get("positions", []) if p.get("ticker")}
    prices = snapshot.get("prices") or {}
    out, taken = [], set(already)
    for r in rows:
        tic = r.get("ticker")
        if tic in taken:
            continue
        pos = positions.get(tic)
        pq = int((pos or {}).get("qty") or 0)
        if pq <= 0:
            rv.mark(r["id"], "void", JOURNAL_DIR, note="보유 0 — 이미 팔렸다(규율·사람)")
            continue
        qty = min(pq, int(r.get("qty") or 0))
        if qty <= 0:
            continue
        price = float((prices.get(tic) or {}).get("price") or pos.get("price") or 0)
        if market.upper() == "KR" and price > 0:
            price = float(snap_kr_price(price, "SELL"))
        out.append({
            "ticker": tic, "name": pos.get("name", "") or r.get("name", ""),
            "market": market.upper(), "excd": pos.get("excd"),
            "action": "SELL", "qty": qty, "price": price,
            "source": "audit_reversal", "full_exit": qty >= pq,
            "thesis_id": r.get("thesis_id") or None, "trigger_id": "audit_reversal", "reversal_id": r["id"],
            # 미국 주간거래(stops.py --daytime)는 손절·추적만 판다 — 소급은 손절과 같은 급(근거가 무너졌다)
            "leg_kind": "stop",
            "reasons": [f"감사 소급 {r['id']} — {str(r.get('why') or '')[:160]} "
                        f"(판정표 {', '.join(r.get('fc_rows') or []) or '재채점'}) — 근거가 무너진 매수를 되돌린다"],
            "exempt_from_count_limit": True,
        })
        taken.add(tic)
    return out


def thesis_exit_sells(snapshot: dict, market: str, already: set = None) -> list:
    """**보유 논지의 가격 매도 조건은 기계가 판다** — 손절(`price <=`·`pnl_pct <=`)은 전량,
    목표(`price >=`)는 트리거의 `size_pct`(원장 표기 · 구 `sell_pct`, 기본 50%). `judge`·시간 조건은 모델 몫으로 남긴다.

    ★ 왜(2026-09-16): 한화오션 x2 `price <= 81500`인데 13:15 시세 81,500을 보고도 그 run은
    "종가 기준 판정은 다음 run"이라며 안 팔았다. `theses_levels.md`는 "가격 조건은 LLM 재량이
    개입하지 않는다"고 했지만 집행이 모델 손에 있어 미뤄졌다. 여기서 규율 SELL로 만들면 미룰 자리가
    없다. 종가를 기다리지 않는다 — 장중 시세가 닿으면 그 run이 판다(`--live`로 시세를 다시 받는다).
    """
    already = already or set()
    store = JOURNAL_DIR / "theses.json"
    if not store.exists():
        return []
    try:
        theses = json.loads(store.read_text(encoding="utf-8")).get("theses") or []
    except (OSError, json.JSONDecodeError):
        return []
    import theses as th                       # 지연 import — theses.py가 이 모듈을 import한다
    positions = {p["ticker"]: p for p in (snapshot.get("balance") or {}).get("positions", [])
                 if p.get("ticker")}
    out, taken = [], set(already)
    for t in theses:
        if t.get("status") != "held" or str(t.get("market", "")).upper() != market.upper():
            continue
        tic = t.get("ticker")
        pos = positions.get(tic)
        if not pos or int(pos.get("qty") or 0) <= 0 or tic in taken:
            continue
        ctx = th.build_ctx(t, snapshot)
        for trg in t.get("exit_triggers") or []:
            if trg.get("fired"):
                continue
            expr = str(trg.get("check") or "")
            why = {}
            if th.eval_cond(expr, ctx, why) is not True:
                continue
            m = th.COND.match(expr)
            var, op = m.group(1), m.group(2)
            is_stop = op in ("<=", "<") and var in ("price", "pnl_pct")
            is_target = op in (">=", ">") and var == "price"
            # ★ 추적 다리(FB-261003-2) — 고점 대비 하락률이 등록 때 고정한 폭(그 종목 하루 흔들림 × 배수)을 넘으면 판다.
            #   오르는 동안은 고정 목표로 자르지 않는다(사용자 10-03: 10%는 하한, 상한 없음).
            is_trail = op in ("<=", "<") and var == "from_peak_pct"
            if not (is_stop or is_target or is_trail):
                continue                      # 시간 조건 등은 모델이 판정한다
            pq = int(pos["qty"])
            if is_stop:
                qty = pq
            elif is_trail:
                pct = float(trg.get("size_pct") or trg.get("sell_pct") or 100)
                qty = max(1, min(pq, int(round(pq * pct / 100))))
            else:
                # 원장은 `size_pct`로 쓴다(`sell_pct`는 구 표기) — 둘 다 읽고 없으면 50%.
                pct = float(trg.get("sell_pct") or trg.get("size_pct") or 50)
                qty = max(1, min(pq, int(round(pq * pct / 100))))
            price = float(ctx.get("price") or pos.get("price") or 0)
            if market.upper() == "KR" and price > 0:
                price = float(snap_kr_price(price, "SELL"))
            out.append({
                "ticker": tic, "name": pos.get("name", "") or t.get("name", ""),
                "market": market.upper(), "excd": pos.get("excd"),
                "action": "SELL", "qty": qty, "price": price,
                "source": "discipline", "full_exit": qty >= pq,
                "thesis_id": t.get("id"), "trigger_id": trg.get("id"),
                # 다리 종류 — 미국 주간거래는 손절·추적만 판다(2026-10-12 사용자 결정 · stops.py --daytime)
                "leg_kind": "stop" if is_stop else ("trail" if is_trail else "target"),
                "reasons": [f"논지 {t.get('id')} {trg.get('id')} "
                            f"{'손절' if is_stop else ('추적' if is_trail else '목표')} `{expr}` "
                            f"(시세 {ctx.get('price')}) — 가격 조건은 모델 재량 없이 즉시 판다"],
                "exempt_from_count_limit": True,
            })
            taken.add(tic)
            break                              # 한 논지에 매도 주문은 하나
    return out


def refresh_live_prices(snapshot: dict, market: str) -> list:
    """보유·관심 종목 시세를 브로커에서 **다시 받아** 스냅샷 값을 덮는다(GET만). 반환 갱신 목록.

    5단은 캡처보다 한 시간쯤 뒤다 — 그 사이 손절선에 닿은 것을 캡처 시세로는 못 본다.
    실패한 종목은 스냅샷 값을 그대로 두고 사유를 돌려준다(못 받은 것을 0으로 쓰지 않는다).
    """
    from kis_client import KisClient, KisError
    notes = []
    try:
        client = KisClient.for_mode()
    except Exception as e:                    # noqa: BLE001
        return [f"시세 재조회 불가 — {type(e).__name__}: {str(e)[:80]}"]
    prices = snapshot.setdefault("prices", {})
    positions = (snapshot.get("balance") or {}).get("positions", [])
    for p in positions:
        tic = p.get("ticker")
        if not tic:
            continue
        try:
            if market.upper() == "KR":
                q = client.domestic_price(tic)
            else:
                from kis_client import EXCD_ORDER
                excd = (p.get("excd") or "NASD").upper()
                quote = {v: k for k, v in EXCD_ORDER.items()}.get(excd, "NAS")
                q = client.overseas_price(tic, excd=quote)
            live = float(q.get("price") or 0)
        except (KisError, KeyError, TypeError, ValueError) as e:
            notes.append(f"{tic} 시세 재조회 실패({type(e).__name__}) — 캡처 값 유지")
            continue
        if live <= 0:
            continue
        old = (prices.get(tic) or {}).get("price") or p.get("price")
        prices.setdefault(tic, {})["price"] = live
        p["price"] = live
        if p.get("qty"):
            p["eval_amt"] = live * int(p["qty"])
            if p.get("avg_price"):
                p["pnl_pct"] = (live - float(p["avg_price"])) / float(p["avg_price"]) * 100
        notes.append(f"{tic} 시세 {old} → {live} (재조회 {datetime.now(KST):%H:%M})")
    return notes


# --------------------------------------------------------------- proposal 게이트

def _after_sells(balance: dict, sells: list) -> tuple:
    """같은 run의 **비회전 SELL**(규율·논지·소급·모델 매도)이 체결되면 남는 잔고 — (post_balance, 대금).

    ★ 2026-10-15 사용자: "손절 매도 이후에 매수가 없어서 의도치 않은 현금 비중 증가가 있었다 — 매도 이후에 필요 시 매수를 더 할 수
    있게." 예전 gap은 **캡처 시점** 투자비중으로 재서, 같은 run에서 판 돈이 '쓸 수 있는 돈'(apply_run_limits)에만 들어가고
    '써야 할 돈'(need)에는 안 들어갔다 — 판 만큼 현금이 남아도 미달이 아니었다. 이 잔고로 gap·미달을 재면 매도 대금이 자연히 gap에
    들어간다(따로 더하지 않는다 — 이중 계산). 회전 매도(`funding`)는 그 매수를 위해 파는 것이라 빼고 센다."""
    sells = [o for o in (sells or []) if o.get("action") == "SELL" and o.get("source") != "funding"]
    if not sells or balance.get("cash") is None:
        return balance, 0.0
    proceeds = sum(int(o.get("qty") or 0) * float(o.get("price") or 0) for o in sells)
    sold = {}
    for o in sells:
        sold[o.get("ticker")] = sold.get(o.get("ticker"), 0) + int(o.get("qty") or 0)
    pos = []
    for p in balance.get("positions") or []:
        q = int(float(p.get("qty") or 0))
        s = sold.get(p.get("ticker"), 0)
        if s <= 0 or q <= 0:
            pos.append(p)
            continue
        left = max(0, q - s)
        if left:
            pos.append({**p, "qty": left, "eval_amt": float(p.get("eval_amt") or 0) * left / q})
    return {**balance, "cash": float(balance.get("cash") or 0) + proceeds, "positions": pos}, proceeds


def _refill_need(market: str, balance: dict, sells: list) -> dict:
    """매도 전용 pass(run 사이·run 안의 `stops.py` — 손절·추적·감사 소급)가 판 **뒤** gap. 없으면 None.

    ★ 2026-10-15 — 예전엔 이 pass가 미달 검사를 아예 건너뛰었다("규율 전용 run — 검사 없음"). 10-14 미국 run은 매수 단계가
    끝난 01:42에 VST 115주(약 $17,400)·LLY 1주를 판 뒤 다시 사는 경로가 없어 투자비중이 95% → 79%로 떨어졌다.
    목표는 직전 배분 판단(`allocation.last`)을 쓴다 — 이 pass에는 시그널이 없다. 파일 기록은 `stops.py`가 전송·체결 뒤에 한다."""
    import allocation as _al
    sells = [o for o in (sells or []) if o.get("action") == "SELL" and o.get("source") != "funding"]
    if not sells:
        return None
    prev = _al.last() or {}
    tgt = prev.get("target_invested_pct")
    out = {"based_on": prev.get("id"), "target_invested_pct": tgt,
           "sold": [{"ticker": o["ticker"], "qty": int(o["qty"]), "price": float(o["price"]), "source": o.get("source")}
                    for o in sells]}
    if balance.get("cash") is None or tgt is None:
        out.update(short=None, why=("현금 미확인 — 매도 뒤 gap을 잴 수 없다" if balance.get("cash") is None
                                    else "직전 배분 판단이 없다 — 목표를 모른다"))
        return out
    post, proceeds = _after_sells(balance, sells)
    pfl = pf_local(portfolio_equity(market, post), market, post)
    gap_pct = max(0.0, float(tgt) - pfl["invested_pct"])
    gap_amt = pfl["equity"] * gap_pct / 100
    out.update(proceeds=round(proceeds, 2), invested_pct_after=round(pfl["invested_pct"], 2),
               gap_pct=round(gap_pct, 2), gap_amt=round(gap_amt, 2), cash_after=round(float(post.get("cash") or 0), 2),
               short=gap_amt > 0,
               why=(f"매도 대금 {proceeds:,.0f} · 매도 뒤 투자비중 {pfl['invested_pct']:.1f}% < 직전 판단 목표 {float(tgt):.0f}% "
                    f"(gap {gap_amt:,.0f}) — 재투자 pass(`refill.py`)로 채운다" if gap_amt > 0 else
                    f"매도 뒤 투자비중 {pfl['invested_pct']:.1f}% ≥ 직전 판단 목표 {float(tgt):.0f}% — 재투자 필요 없음"))
    return out


def allocation_context(sig: dict, market: str, balance: dict, sells: list = None) -> tuple:
    """시그널의 배분 판단 → (alloc_ctx, approved.allocation).

    alloc_ctx = {target_invested_pct, gap_pct, gap_amt, deployable, reserved_here, need, notes, error?}
    - gap = 판단 목표 − 지금 투자비중(포트폴리오) · gap_amt는 이 시장 통화
      ★ `sells`(같은 run의 비회전 SELL)가 있으면 **매도 뒤** 투자비중으로 잰다(2026-10-15 — `_after_sells`)
    - reserved_here = `allocation.reserved` 중 이 시장 몫(없으면 논지 원장 기본값) · deployable = 이 시장 현금 − reserved_here
    - need = min(gap_amt, deployable) — 일일 상한은 없다(2026-09-23)
    - 목표 < 100 − 예약% 인데 현금 명분·해제 조건이 없으면 error
    """
    import allocation as _al
    al = dict(sig.get("allocation") or {})
    market = (market or "KR").upper()
    cap_invested = pf_local(portfolio_equity(market, balance), market, balance)["invested_pct"] if sells else None
    balance, proceeds = _after_sells(balance, sells)
    pf = portfolio_equity(market, balance)
    pfl = pf_local(pf, market, balance)
    equity, invested_pct = pfl["equity"], pfl["invested_pct"]
    fx = float(pf.get("fx") or 0) if pf.get("ok") else 0.0
    notes = []
    # 시장별 자산(예약 기본값 계산용) — 포트폴리오를 못 쟀으면 이 시장만
    eq_by = {}
    if pf.get("ok"):
        for m, v in (pf.get("parts") or {}).items():
            eq_by[m] = float(v.get("krw") or 0) / (fx if (m == "US" and fx) else 1.0)
    else:
        eq_by[market] = equity
    reserved = al.get("reserved")
    # 빈 목록은 '기본값을 써라'다 — 다만 `reserved_why`를 같이 적으면 예약 0이라는 판단으로 받는다(2026-10-15).
    if reserved is None or (reserved == [] and not str(al.get("reserved_why") or "").strip()):
        buying = {str(p.get("ticker")) for p in (sig.get("proposals") or []) + (sig.get("refill_candidates") or [])
                  if p.get("action") == "BUY" and p.get("ticker")}
        reserved = _al.default_reserved(market, eq_by, buying=buying)
        if reserved:
            notes.append("예약은 논지 원장 기본값(armed 논지의 안 산 가격 칸 · 이번 run이 사는 종목·판단 칸·만기 제외) — " + " · ".join(
                f"{r['thesis_id']} {r['market']} {r['amount']:,.0f}" for r in reserved))
    al["reserved"] = reserved

    def to_here(r):
        amt = float(r.get("amount") or 0)
        if str(r.get("market")).upper() == market:
            return amt
        if not fx:
            return 0.0
        return amt / fx if market == "US" else amt * fx
    reserved_here = sum(to_here(r) for r in reserved if str(r.get("market")).upper() == market)
    reserved_all = sum(to_here(r) for r in reserved)
    reserved_pct = (reserved_all / equity * 100) if equity > 0 else 0.0
    err = _al.cash_reason_needed(al, reserved_pct)
    if err:
        return {"error": "allocation — " + err}, None

    target = float(al.get("target_invested_pct") or 0)
    gap_pct = max(0.0, target - invested_pct)
    gap_amt = equity * gap_pct / 100
    here_cash = float(balance.get("cash") or 0)
    # ★ 실계좌 — 국내·미국 현금이 한 주머니(통합증거금 · `cash_shared`)면 이 시장 몫을 판단으로 정한다(2026-10-15 사용자 결정 "run마다 시장 몫을
    #   판단으로"). gap = 목표 × 이 시장 몫 × 자산 − 이 시장 투자액 · 예약은 두 시장 합을 공유 현금에서 뺀다. 모의(두 계좌)는 기존 식.
    invested_eff, share = invested_pct, None
    ms = al.get("market_share") if isinstance(al.get("market_share"), dict) else None
    if balance.get("cash_shared"):
        if not ms:
            return {"error": "allocation — 실계좌는 국내·미국 현금이 한 주머니다(통합증거금) — 이 run의 시장 몫 판단 "
                             "`allocation.market_share` {KR, US, why}(합 100)를 실어라. 직전 판단을 이어받고, 바꾸면 change.why "
                             "(stage5_decide.md 〈배분〉 · python3 allocation.py prev)"}, None
        share = float(ms.get(market) or 0)
        inv_here = float(((pf.get("parts") or {}).get(market) or {}).get("invested") or 0) if pf.get("ok") \
            else float(pfl.get("invested") or 0)
        want_here = equity * target / 100 * share / 100
        gap_amt = max(0.0, want_here - inv_here)
        gap_pct = gap_amt / equity * 100 if equity > 0 else 0.0
        invested_eff = (inv_here / (equity * share / 100) * 100) if equity > 0 and share > 0 else 100.0
        reserved_here = reserved_all                     # 공유 현금 — 두 시장 예약이 같은 돈에서 빠진다
        notes.append(f"시장 몫 {market} {share:.0f}%(판단: {str(ms.get('why') or '')[:60]}) — 목표 {target:.0f}% × 몫 = 이 시장 몫 "
                     f"{want_here:,.0f} vs 이 시장 투자 {inv_here:,.0f} → gap {gap_amt:,.0f} · 예약은 두 시장 합 {reserved_all:,.0f}")
    deployable = max(0.0, here_cash - reserved_here)
    need = min(gap_amt, deployable)
    if proceeds:
        notes.append(f"같은 run 매도 대금 {proceeds:,.0f}을 반영한 투자비중 {invested_pct:.1f}%(캡처 {cap_invested:.1f}%) — "
                     f"판 돈도 gap에 들어간다(매도 뒤 현금이 남으면 미달이다)")
    notes.append(f"배분 판단 — 목표 {target:.0f}% vs 투자비중 {invested_pct:.1f}% → gap {gap_pct:.1f}%p({gap_amt:,.0f}) · "
                 f"이 시장 현금 {here_cash:,.0f} − 예약 {reserved_here:,.0f} = 쓸 수 있는 {deployable:,.0f} → 이번 run 필요 {need:,.0f}"
                 + (f" · 현금 명분: {str(al.get('cash_reason'))[:80]}" if str(al.get("cash_reason") or "").strip().lower() not in _al.NO_CASH_REASON else ""))
    ctx = {"target_invested_pct": target, "invested_pct": invested_pct, "gap_pct": gap_pct, "gap_amt": gap_amt,
           "reserved_here": reserved_here, "reserved_pct": reserved_pct, "deployable": deployable, "need": need,
           "notes": notes, "post_balance": balance, "sell_proceeds": proceeds,
           "invested_pct_eff": invested_eff, "market_share": share}
    out = {**al, "invested_pct": round(invested_pct, 2), "gap_pct": round(gap_pct, 2), "gap_amt": round(gap_amt),
           "reserved_here": round(reserved_here), "reserved_pct": round(reserved_pct, 2),
           "deployable": round(deployable), "need": round(need)}
    if proceeds:
        out["post_sell"] = {"sell_proceeds": round(proceeds, 2), "invested_pct_capture": round(cap_invested, 2),
                            "invested_pct_after_sells": round(invested_pct, 2)}
    return ctx, out


def _universe(watchlist: dict, market: str, positions: list) -> set:
    tickers = {e["ticker"] for e in watchlist.get(market, []) if "ticker" in e}
    tickers |= {p["ticker"] for p in positions if p.get("ticker")}
    return tickers


def _forbidden(name: str, ticker: str, patterns: list) -> str:
    hay = f"{name} {ticker}".upper()
    for pat in patterns:
        if pat.upper() in hay:
            return pat
    return ""


def thesis_by_id(tid: str):
    """원장(`JOURNAL_DIR/theses.json`)에서 논지 하나. 없으면 None."""
    if not tid:
        return None
    try:
        data = json.loads((JOURNAL_DIR / "theses.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return next((t for t in data.get("theses") or [] if t.get("id") == tid), None)


def held_theses_of(ticker: str) -> list:
    """그 종목의 닫히지 않은 논지들(원장 `JOURNAL_DIR/theses.json`)."""
    try:
        data = json.loads((JOURNAL_DIR / "theses.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [t for t in data.get("theses") or [] if t.get("ticker") == ticker and t.get("status") != "closed"]


def _open_up_axes() -> set:
    """방향 `+`인 열린 전망의 축 id — '축이 확인돼 오르는 쪽'을 가리는 데 쓴다."""
    try:
        axes = (json.loads((JOURNAL_DIR / "market_map.json").read_text(encoding="utf-8")) or {}).get("axes") or []
    except (OSError, json.JSONDecodeError):
        return set()
    return {a.get("id") for a in axes if a.get("direction") == "+" and (a.get("status") or "open") == "open"}


def rotation_candidates(positions: list, exclude: set) -> list:
    """★ FB-261003-7 — 회전 매도(자금원) 후보를 **논지가 가장 약한 순**으로. 반환 [(position, 손절까지 거리 %|None)].

    예전에는 '이 시장 최대 보유'를 팔았다. 최대 보유는 대개 논지가 맞아 가장 많이 오른 종목이라 승자를 팔아
    새 후보를 샀다(2026-09-30 테스 11/17주 → 두산퓨얼셀, 테스는 그 뒤 +13.1%). 이제는
    ① **보호**: 열린 `+` 전망 축의 논지가 있고 현재가 > 평균가인 보유는 자금원이 아니다
    ② 순서: 논지 없는 보유 → 손절 다리(`price <=`)에 가까운 보유 → 손익이 낮은 보유."""
    up = _open_up_axes()
    rows = []
    for p in positions:
        tk = p.get("ticker")
        if not tk or tk in exclude or int(p.get("qty") or 0) <= 0 or (p.get("price") or 0) <= 0:
            continue
        price, avg = float(p["price"]), float(p.get("avg_price") or 0)
        ths = held_theses_of(tk)
        if ths and avg > 0 and price > avg and any(t.get("axis_id") in up for t in ths):
            continue
        stops = []
        for t in ths:
            for x in t.get("exit_triggers") or []:
                m = re.match(r"\s*price\s*<=?\s*([\d.]+)", str(x.get("check") or ""))
                if m and not x.get("fired") and float(m.group(1)) > 0:
                    stops.append(float(m.group(1)))
        dist = min((price / v - 1) * 100 for v in stops) if stops else None
        rows.append(((1 if ths else 0, dist if dist is not None else 1e9, float(p.get("pnl_pct") or 0)), p, dist))
    rows.sort(key=lambda r: r[0])
    return [(p, dist) for _, p, dist in rows]


def tag_sell(p: dict) -> tuple:
    """★ FB-261003-9 — 모델 매도에 논지 꼬리표를 붙인다. 반환 (thesis_id, trigger_id, 거부 사유|None).

    꼬리표가 없으면 체결이 어느 다리를 소진했는지 원장이 모르고, 아쉬움 감사가 규칙 매도를 재량 매도로 센다
    (09-14~10-01 논지 있는 종목의 모델 매도 4건이 꼬리표 없이 체결). 논지가 하나면 붙이고, 여럿이면 모호하니 거부한다."""
    tid, trg = p.get("thesis_id"), p.get("trigger_id")
    if not tid:
        live = held_theses_of(p.get("ticker", ""))
        if len(live) > 1:
            return None, None, (f"논지 꼬리표 모호 — 이 종목의 열린 논지가 {len(live)}개({', '.join(t.get('id', '?') for t in live)})다. "
                                f"제안에 thesis_id를 적어라")
        if len(live) == 1:
            tid = live[0].get("id")
    if tid and not trg:
        m = re.search(r"\b(x\d+)\b", " ".join(str(p.get(k) or "") for k in ("discipline", "thesis", "reason", "why")))
        trg = m.group(1) if m else "discretion"
    return tid, trg, None


def first_target(p: dict, thesis: dict = None):
    """BUY 제안의 **1차 목표** — 제안 `exits[]`와 논지 `exit_triggers[]`의 `price >= N`(또는 `>`) 중 최솟값.
    진입 트리거(`triggers[]` — 돌파 진입도 `price >=`다)·무효 조건은 보지 않는다. 없으면 None."""
    import theses as th
    vals = []
    for trg in list(p.get("exits") or []) + list((thesis or {}).get("exit_triggers") or []):
        m = th.COND.match(str(trg.get("check") or ""))
        if m and m.group(1) == "price" and m.group(2) in (">=", ">"):
            vals.append(float(m.group(3)))
    return min(vals) if vals else None


def screen_proposals(sig: dict, limits: dict, watchlist: dict,
                     balance: dict, prices: dict, sup_break: dict = None, post_bal: dict = None) -> tuple:
    """proposal별 판정. 반환 (accepted, rejected). `sup_break` = {종목: 사유} — 지지 구간 종가 이탈(support_break.ticker_map).
    `post_bal` = 같은 run의 규율·논지·소급 매도 뒤 잔고(`_after_sells`) — gap 채우기 여유(fill_room_pct)만 이것으로 잰다."""
    sup_break = sup_break or {}
    rules = limits.get("reject_rules", {})
    market = sig["market"]
    positions = balance.get("positions", [])
    held = {p["ticker"]: p for p in positions}
    universe = _universe(watchlist, market, positions)
    cash_known = balance.get("cash") is not None
    here_equity = (balance.get("cash") or 0) + sum(p.get("eval_amt", 0) or 0 for p in positions)

    # ★ 모든 비중의 분모는 **포트폴리오 합산 자산**이다 — 계좌가 둘로 갈린 것은
    # 모의 시뮬레이터의 사정이고, 실계좌는 한 풀로 쓴다. 시장별 분모로 재면
    # 같은 confidence가 시장에 따라 다른 금액이 되어 두 시장의 숫자를 비교할 수 없다.
    # 단위는 그 시장 통화 — 분모만 합산으로 바꾸고 가격 비교는 그대로 성립한다.
    pf = portfolio_equity(market, balance)
    pfl = pf_local(pf, market, balance)
    equity = pfl["equity"]
    ax_idx = axis_index()
    ax_exp = axis_exposure(pf, market, ax_idx)
    # ★ 집중은 상한이 아니라 **명분이 붙어야 하는 선**이다(2026-09-22 사용자: "무지성 상수 제한 말고 판단").
    #   이번 주문 뒤 종목·축 비중이 이 선을 넘기는 제안은 `concentration_why`가 있어야 한다.
    disc = limits.get("concentration_disclose_pct") or {}
    disc_pos = disc.get("position")
    disc_axis = disc.get("axis")

    accepted, rejected = [], []
    notes = [pf["why"]]
    if not pf.get("ok"):
        notes.append("★ 반대편 스냅샷을 못 쟀다 — 이 시장 자산만으로 재므로 비중이 "
                     "실제보다 크게 나온다(보수적). 반대편 run을 돌리면 정확해진다.")
    elif market == "US":
        notes.append(f"분모 단위 환산 — 포트폴리오 {pf['krw']:,.0f}원 "
                     f"÷ {pf['fx']:,.2f} = {equity:,.0f}달러")
    invested_pct = pfl["invested_pct"]
    # FB-261010-5 — gap 채우기(apply_run_limits)가 매수를 키울 수 있는 폭(목표 대비 미달 · 이 시장 현금 중 작은 쪽, 포트폴리오 %).
    #   이 폭이 보유 평균 이상이면 작은 제안도 코드가 키우므로 '보유 평균보다 작은 새 종목' 검사를 건너뛴다.
    _tgt_inv = float(((sig.get("allocation") or {}).get("target_invested_pct")) or 0)
    _rb = post_bal if post_bal is not None else balance
    _room_inv = (pf_local(portfolio_equity(market, _rb), market, _rb)["invested_pct"]
                 if post_bal is not None else invested_pct)
    fill_room_pct = (min(max(0.0, _tgt_inv - _room_inv), float(_rb.get("cash") or 0) / equity * 100)
                     if equity > 0 else 0.0)
    real_shares = account_mode(limits) != "paper"   # 실계좌는 주 단위 최소 1주(2026-10-15 사용자 결정)

    def reject(p, why):
        rejected.append({"ticker": p.get("ticker", "?"), "action": p.get("action", "?"), "why": why})

    for p in sig["proposals"]:
        ticker = p["ticker"]
        action = p["action"]
        if action == "HOLD":
            continue

        # 예수금을 모르면 매수 규모를 계산할 수도, 현금 하한을 지킬 수도 없다.
        # 미확인을 0으로 뭉개면 엉뚱한 사유로 거부되어 원인 추적이 어려워진다.
        if action == "BUY" and not cash_known:
            reject(p, "예수금 미확인 — 현금 하한을 검사할 수 없어 매수를 보류한다")
            continue

        # 유니버스 — 뉴스레터 본문발 인젝션이 주문으로 번지는 유일한 통로를 막는다.
        if ticker not in universe:
            reject(p, f"유니버스 밖 종목 (watchlist 미등재·미보유). watchlist_candidates로 제안할 것")
            continue

        name = p.get("name") or held.get(ticker, {}).get("name", "")
        bad = _forbidden(name, ticker, rules.get("forbidden_name_patterns", []))
        if bad:
            reject(p, f"금지 패턴 '{bad}' 매칭 (선물형 배제 — universe_rules.forbidden_name_patterns)")
            continue

        # 지지 이탈(2026-10-14 · 차트스쿨 01 B) — 지지 구간을 기준으로 산 보유가 직전 세션 종가로 구간 아래에 끝났으면
        #   이 run은 무효 조건을 다시 판정만 하고 그 종목을 더 사지 않는다(손절은 장중 터치로 따로 집행된다).
        if action == "BUY" and ticker in sup_break:
            reject(p, f"지지 구간 종가 이탈 — {sup_break[ticker]} · 이 run은 무효 조건 재판정만(추가 매수 없음 · 이어받기 '## 지지 이탈')")
            continue

        min_conf = rules.get("min_confidence")
        if min_conf is not None and p["confidence"] < min_conf:
            reject(p, f"confidence {p['confidence']:.2f} < {min_conf}")
            continue

        verdicts = [str(e.get("verdict", "")).upper() for e in p.get("evidence", [])]
        if rules.get("reject_if_any_mismatch", True) and "MISMATCH" in verdicts:
            reject(p, "근거에 MISMATCH 포함 (fact-check 불일치)")
            continue

        # ★ 갈래 ① 주문 근거 검증(2026-10-13 사용자: "주문·보유 조건 근거 fact-check는 주문 전에 무조건 pass") —
        #   근거 판정은 모델이 적은 값이 아니라 `fc_track1.py decide`가 판정표에서 옮겨 쓴 값만 인정한다. 감사까지 통과하지 못한
        #   제안(7회 뒤에도 FAIL)은 decide가 drop에 넣는다. 예전에는 근거 행에 모델이 'MATCH'를 스스로 적었고(VST: ferc.gov
        #   루트 주소만으로 T1·MATCH), `require_match_t1t2_for_buy`는 꺼져 있었다.
        if action == "BUY" and limits.get("fc_track1_required"):
            t1 = sig.get("track1") or {}
            if not t1:
                reject(p, "주문 근거 검증 기록이 없다 — fc_track1.py extract → fact-check(감사 PASS) → decide를 먼저 돌려라")
                continue
            if ticker in (t1.get("drop") or []):
                reject(p, f"주문 근거 검증 미통과 — 감사 {t1.get('audit')}·판정에서 근거가 서지 않았다(논지는 armed 유지 · 다음 run이 다시 본다)")
                continue
            ev = p.get("evidence") or []
            if any(str(e.get("verdict", "")).upper() == "UNVERIFIED_T1" for e in ev):
                reject(p, "주문 근거 검증 미통과 — 근거 행이 판정표에 없거나 감사를 통과하지 못했다(UNVERIFIED_T1)")
                continue
            if not any(e.get("track1") == "PASS" and str(e.get("verdict", "")).upper() == "MATCH"
                       and str(e.get("tier", "")).upper() in ("T1", "T2") for e in ev):
                reject(p, "주문 근거 검증 미통과 — 판정표에서 MATCH(T1/T2)로 확인된 근거가 하나도 없다")
                continue

        if action == "BUY" and rules.get("require_match_t1t2_for_buy", True):
            ok = any(
                str(e.get("verdict", "")).upper() == "MATCH"
                and str(e.get("tier", "")).upper() in ("T1", "T2")
                for e in p.get("evidence", [])
            )
            if not ok:
                reject(p, "BUY에 MATCH(T1/T2) 근거가 없다")
                continue

        price = (prices.get(ticker, {}) or {}).get("price") or held.get(ticker, {}).get("price") or 0
        if price <= 0:
            reject(p, "현재가를 알 수 없다 (스냅샷에 시세 없음)")
            continue
        live_raw = float(price)                 # 호가 격자 맞추기 전 값 — 목표 여유 계산용

        # 국내 지정가는 호가 격자 위에만 존재한다. 시세 API가 격자 밖 값을 주므로
        # (2026-09-08 실측: 삼성전자 273,750 / 이 구간 단위 500) 여기서 맞춘다.
        # execute가 아니라 여기서 맞추는 이유: 승인서에 적힌 가격과 실제 나가는 가격이
        # 같아야 하고, 아래 수량 환산과 일일 금액 상한도 그 가격으로 계산돼야 한다.
        if market == "KR":
            price = float(snap_kr_price(price, action))

        if action == "SELL":
            pos = held.get(ticker)
            if not pos or pos.get("qty", 0) <= 0:
                reject(p, "미보유 종목 SELL (long-only — 공매도 없음)")
                continue
            qty = min(int(p.get("qty") or pos["qty"]), pos["qty"])
            s_tid, s_trg, s_bad = tag_sell(p)
            if s_bad:
                reject(p, s_bad)
                continue
            accepted.append({
                "ticker": ticker, "name": pos.get("name", ""), "market": market,
                "excd": p.get("excd") or pos.get("excd"),
                "action": "SELL", "qty": qty, "price": price,
                "source": "llm", "proposal": p, "exempt_from_count_limit": False,
                # ★ 2026-10-01: 제안이 논지·트리거를 가리키면 그 id를 행에 싣는다(판정은 그대로) — 없으면 `fill`이
                #   다리 소진·논지 종료를 못 해 6단이 원장을 손으로 맞췄다(09-30·10-01). 장중 터치 파일
                #   (`stops.py --intraday`)의 매도 행에 id가 실려 있으니 5단은 그대로 옮겨 적는다.
                "thesis_id": s_tid, "trigger_id": s_trg,
                "full_exit": bool(p["full_exit"]) if "full_exit" in p
                else bool(s_tid) and qty >= int(pos["qty"]),
            })
            continue

        # ★ 목표 여유 — 1차 목표(`price >= N`)가 현재가 +min_target_gap_pct 안이면 **이미 오른 것을 쫓는 제안**이다
        #   (SKILL.md §0-b의 기계 형태). 2026-09-21 테스: 09-18에 +7% 자리로 건 x3 147,800이 사기도 전에 +0.7%가
        #   됐는데 그대로 제안됐다 — 사자마자 절반이 팔리는 회전은 비중도 못 올린다. 유효성 거부(미달 건수에 안 셈)
        #   → 논지는 armed 유지, 다음 run이 새 과거 고정 기준으로 목표를 다시 건다(`theses.py set-trigger`).
        # ★★ 2026-10-03 사용자: "1~2% 벌 거면 벌지 마 — 최소 10%는 하한이지 목표가 아니다." 바닥을 3% → 10%로 올렸다.
        #   1차 고정 목표가 없으면(추적 매도만 건 논지) 제안의 `expected_upside_pct`(상한 없음)로 같은 바닥을 판정한다.
        sizing = limits.get("position_sizing") or {}
        min_gap = float(sizing.get("min_target_gap_pct") or 0)
        # ★ 실계좌(조회 전용 포함)는 바닥 10%를 **세후 순수익**으로 맞춘 시장별 값을 쓴다(2026-10-12 · 국내 10.3 · 미국 11.5).
        by_mkt = sizing.get("min_target_gap_pct_by_market") or {}
        if account_mode(limits) != "paper" and isinstance(by_mkt, dict) and by_mkt.get(market):
            min_gap = float(by_mkt[market])
        if min_gap > 0:
            tgt = first_target(p, thesis_by_id(p.get("thesis_id")))
            if tgt is not None and live_raw > 0:
                gap = (tgt / live_raw - 1) * 100
                if gap < min_gap:
                    fmt = ",.0f" if market == "KR" else ",.2f"
                    reject(p, f"목표 여유 없음 — 1차 목표 {tgt:{fmt}}이 현재가 {live_raw:{fmt}} 대비 {gap:+.1f}% "
                              f"(< 바닥 {min_gap:g}%) · 이미 오른 가격을 쫓는 제안(§0-b)이거나 노릴 수익이 바닥보다 작다 · 논지 재설정 후 재제안")
                    continue
            up = p.get("expected_upside_pct")
            if up is None:
                reject(p, f"expected_upside_pct 없음 — 이 자리에서 노리는 상승폭(상한 없음 · 근거는 size_why)을 적어라. "
                          f"바닥 {min_gap:g}% 미만이면 사지 않는다")
                continue
            if float(up) < min_gap:
                reject(p, f"상승 여지 바닥 미달 — expected_upside_pct {float(up):.1f}% < {min_gap:g}% · 1~2%짜리 자리는 사지 않는다")
                continue

        # BUY — **증분**이다. `weight_target_pct`는 이번 run에 살 비중(포트폴리오 자산 기준)이고 크기는 제안의
        # `size_why`가 정한다. 확신도표·미달 배수·이벤트 계수·종목당 상한은 없다(2026-09-22). 보유 종목도 증분으로 산다.
        delta_pct = float(p.get("weight_target_pct") or 0)
        if delta_pct <= 0:
            reject(p, "weight_target_pct가 없거나 0 이하")
            continue
        # ★ FB-261003-4 — 작게 시작하면 "무엇이 확인되면 얼마로 늘리나"가 같이 있어야 한다. 1~2% 시험 매수로 끝나면
        #   축이 맞아도 계좌에 기여하지 않는다(첫 피드백: 최대 비중 1.5%로 +31% 종목을 탔다).
        small = sizing.get("small_entry_needs_plan_pct")
        if small is not None and delta_pct < float(small):
            inc = p.get("increase_when") or {}
            if not (isinstance(inc, dict) and str(inc.get("when") or "").strip() and float(inc.get("to_pct") or 0) > delta_pct):
                reject(p, f"증액 조건 없음 — 증분 {delta_pct:.1f}% < {float(small):g}%이면 increase_when{{when, to_pct}}"
                          f"(무엇이 확인되면 얼마로 늘리나)를 같이 적어라")
                continue
        if not str(p.get("size_why") or "").strip():
            reject(p, "size_why 없음 — 이 크기를 왜 사는지(기대 수익·무엇이 깨면 틀리나·gap)를 적어라. 표가 정하지 않는다")
            continue
        # ★ 레버리지 ETF는 증분을 배수로 나눈다 — 3배 ETF의 6%는 기초지수 18% 베팅이다(상품 특성 명분).
        lev, lev_inv = leverage_factor(name, ticker)
        # ★ FB-261010-5(2026-10-11 사용자 승인) — 새 종목의 첫 증분이 이 시장 보유 종목의 **평균 크기**보다 작으면
        #   ① 키우거나(현금이 모자라면 아래 자금 배분이 논지가 가장 약한 보유를 회전 매도해 메운다) ② 사지 않거나
        #   ③ small_why{kind, why}로 작게 사는 사유를 적는다. 크기를 '후보끼리 순위·남은 현금'으로 정하면 축이 맞아도
        #   계좌를 못 움직인다(261010: 적용 뒤 새 진입 13건 평균 1.22% · 미국 22종목 중 12종목 2% 미만).
        #   기준은 중앙값이 아니라 평균 — 작은 보유가 많을수록 중앙값도 내려가 기준이 무너진다(261010 미국 중앙값 1.75%).
        #   투자비중이 목표보다 모자라고 현금이 있어 gap 채우기가 평균 이상 키울 수 있으면(fill_room_pct) 건너뛴다 — 그때는 코드가 키운다.
        # ★ 실계좌는 주 단위 최소 1주(2026-10-15 사용자 결정) — 칸이 1주보다 작으면 1주로 올린다(0주 거부 없음).
        #   그림자 점검 실측: 소액 계좌에서 0.5%·1% 칸은 대부분 1주가 안 돼 매수가 전부 0건이었다.
        one_share_pct = (price / equity * 100) if (real_shares and price > 0 and equity > 0) else 0.0
        eff_pct = max(delta_pct, one_share_pct)
        if sizing.get("new_entry_vs_avg_holding") and ticker not in held and lev == 1 and equity > 0:
            live = [q for q in positions if float(q.get("qty") or 0) > 0 and float(q.get("eval_amt") or 0) > 0]
            if live:
                avg_w = sum(float(q["eval_amt"]) for q in live) / equity * 100 / len(live)
                # 주문당 상한이 평균 크기보다 작으면 평균 이상으로 키울 길이 없다 — 상한을 자동 사유(small_why order_cap)로 본다.
                _cap = order_cap_cfg(limits)
                _mult = 1.0 if market == "KR" else float(pf.get("fx") or balance.get("exchange_rate") or 0)
                if _cap and _mult > 0 and equity * avg_w / 100 * _mult > _cap["max_krw"] and eff_pct < avg_w:
                    notes.append(f"{ticker} 새 종목 {eff_pct:.1f}% < 보유 평균 {avg_w:.1f}% — 평균 크기가 주문당 상한 "
                                 f"{_cap['max_krw']:,.0f}원을 넘어 키울 수 없다(small_why order_cap 자동)")
                    p = {**p, "small_why": p.get("small_why") or {"kind": "order_cap", "why": "주문당 상한(자동)"}}
                # 칸이 1주보다 작아 1주로 올린 실계좌 매수는 크기가 '작게 사려는 판단'이 아니라 최소 단위다 — 자동 사유(one_share)
                if one_share_pct and delta_pct < one_share_pct and eff_pct < avg_w:
                    p = {**p, "small_why": p.get("small_why") or {"kind": "one_share",
                                                                    "why": "실계좌 최소 1주 — 칸이 1주보다 작아 1주로 올렸다(자동)"}}
                if eff_pct < avg_w and fill_room_pct < avg_w:
                    sw = p.get("small_why") or {}
                    if not (isinstance(sw, dict) and sw.get("kind") in SMALL_WHY_KINDS and str(sw.get("why") or "").strip()):
                        reject(p, f"새 종목 첫 매수 {delta_pct:.1f}%가 이 시장 보유 평균 {avg_w:.1f}%보다 작다 — "
                                  f"① 평균 이상으로 키우거나(현금이 모자라면 가장 약한 보유를 회전 매도해 메운다) ② 사지 않거나 "
                                  f"③ small_why{{kind: {'|'.join(SMALL_WHY_KINDS)}, why}}로 작게 사는 사유를 적어라")
                        continue
                up_ = p.get("expected_upside_pct")
                if up_ is not None:
                    notes.append(f"{ticker} 계좌 기여 {delta_pct * float(up_) / 100:.2f}%p(크기 {delta_pct:.1f}% × 기대 상승 "
                                 f"{float(up_):.0f}%) · 이 시장 보유 평균 {avg_w:.1f}%")
        if lev > 1 and limits.get("leveraged_cap_divide_by_factor"):
            notes.append(f"{ticker} {lev}배 {'인버스 ' if lev_inv else ''}ETF — 증분 {delta_pct:.1f}%→{delta_pct / lev:.1f}% (배수로 나눔)")
            delta_pct = delta_pct / lev
        max_delta = rules.get("max_weight_delta_per_day_pct")
        if max_delta is not None and delta_pct > max_delta:
            delta_pct = max_delta   # 하루 증분 상한으로 깎아서 진행
        rounded_up = False
        if one_share_pct and lev == 1 and delta_pct < one_share_pct:
            notes.append(f"{ticker} 실계좌 최소 1주 — 칸 {delta_pct:.2f}% → 1주 {one_share_pct:.2f}%(집중 검사도 1주 크기로)")
            delta_pct = one_share_pct
            rounded_up = True

        current_val = held.get(ticker, {}).get("eval_amt", 0) or 0
        current_pct = (current_val / equity * 100) if equity else 0
        after_pos = current_pct + delta_pct
        # 집중 공개선 — 종목·축이 선을 넘기면 명분이 있어야 한다(없으면 그 제안만 거부).
        hit = axes_of(ax_idx, ticker) if not lev_inv else []
        after_axes = {aid: (ax_exp.get(aid, 0.0) + equity * delta_pct / 100 * lev) / equity * 100
                      for aid in hit} if equity > 0 else {}
        over_pos = disc_pos is not None and after_pos > float(disc_pos)
        over_axes = {aid: v for aid, v in after_axes.items() if disc_axis is not None and v > float(disc_axis)}
        if over_pos or over_axes:
            why_c = str(p.get("concentration_why") or "").strip()
            desc = ((f"종목 {after_pos:.1f}%" + (f"(선 {float(disc_pos):.0f}%)" if over_pos else ""))
                    + ("".join(f" · 축 {aid} {v:.1f}%(선 {float(disc_axis):.0f}%)" for aid, v in over_axes.items())))
            if not why_c:
                reject(p, f"집중 명분 없음 — 이번 주문 뒤 {desc} 예상. concentration_why를 적어라(상한이 아니라 설명이 붙어야 하는 선이다)")
                continue
            notes.append(f"{ticker} 집중 공개 — {desc}: {why_c[:120]}")

        # 목표 금액을 주가로 나눠 내리면, 1주 값이 목표보다 조금만 커도 **0주**가 되어
        # 제안이 통째로 사라진다. 목표가 1주 값의 일정 비율 이상이면 1주로 올린다.
        target_amt = equity * delta_pct / 100
        qty = int(target_amt // price)
        round_at = limits.get("round_up_to_one_share_at")
        if qty == 0 and round_at is not None and price > 0 and target_amt >= price * round_at:
            qty = 1
        if qty == 0 and rounded_up and price > 0:
            qty = 1                                  # 실계좌 최소 1주(부동소수 오차로 0.999…주가 나와도 1주)
        if qty <= 0:
            reject(p, f"매수 가능 수량 0주 (증분 {delta_pct:.1f}% = {target_amt:,.0f} / "
                      f"주가 {price:,.2f})")
            continue

        for aid, sign in ax_idx.get(ticker, []):
            ax_exp[aid] = ax_exp.get(aid, 0.0) + qty * price * lev * sign
        accepted.append({
            "ticker": ticker, "name": name, "market": market,
            "excd": p.get("excd"),
            "action": "BUY", "qty": qty, "price": price, "increment_pct": round(delta_pct, 3),
            "source": "llm", "proposal": p, "exempt_from_count_limit": False,
            "thesis_id": p.get("thesis_id"), "trigger_id": p.get("trigger_id"),
            **({"rounded_up_to_1": True} if rounded_up else {}),
        })

    for n in notes:
        print(f"  · {n}")
    return accepted, rejected


# ------------------------------------------------------------------ run 게이트

def spent_today(market: str, currency: str) -> tuple:
    """오늘 이 시장에서 이미 전송한 매수 금액·건수. 반환 (금액, 건수).

    출처는 `journal/trades_*.json`(실제로 보낸 것)이다. 여기서 세지 않으면
    하루에 두 번 실행될 때 일일 상한이 두 번 적용된다.
    기록을 못 읽으면 0을 반환한다 — 상한을 느슨하게 하는 쪽이라 보수적이진 않지만,
    trades 파일이 없다는 건 오늘 아무것도 안 보냈다는 뜻이므로 맞다.
    """
    import sessions as _ss
    today = _ss.run_stamp(market.lower())      # 이 run의 스탬프(현지 거래일 · 2026-10-14) — 같은 세션의 기록만 센다
    # glob인 이유: 그날 기록이 한 파일에 모이는 게 정상이지만, 읽을 수 없는 파일이
    # `.corrupt_*.json`으로 밀려나며 새 파일이 생기는 경우가 있다(execute.merge_day_record).
    # 그때 한 파일만 보면 이미 나간 주문이 상한 계산에서 통째로 빠진다.
    amount, count = 0.0, 0
    for path in sorted(JOURNAL_DIR.glob(f"trades_{today}_{market.lower()}*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue          # 못 읽는 파일은 건너뛴다 — 다른 파일까지 버릴 이유는 없다
        for t in data.get("trades", []):
            amt = sent_amount(t)
            if amt is None:
                continue
            amount += amt
            count += 1
    return amount, count


def sent_amount(t: dict):
    """이 거래 행이 오늘 **실제로 나간** 금액. 안 나갔으면 None.

    `status`는 전송 상태(SENT/UNKNOWN/FAILED)이고 종결 상태는 `fill.verdict`다(fill.py).
    FAILED만 '나가지 않은 것'이다 — UNKNOWN(주문번호를 못 읽음)도 나갔을 수 있으므로 센다.
    취소·만료·거부로 닫힌 건은 **체결된 만큼만** 센다(안 산 돈을 상한에서 빼 두면 하루치가 굶는다).
    *실사례(2026-09-16): 9/15 세션이 `status`를 손수 FILLED로 바꿔, SENT만 세던 예전 코드는
    그날 미국 매수 4,252달러를 상한 계산에서 빠뜨렸다.*
    """
    if t.get("action") != "BUY" or t.get("status") == "FAILED":
        return None
    f = t.get("fill") or {}
    v = f.get("verdict")
    if v in ("CANCELLED", "EXPIRED", "REJECTED", "PARTIAL"):
        q = f.get("filled_qty") or 0
        return q * (f.get("avg_price") or t.get("price") or 0) if q else None
    return (t.get("qty") or 0) * (t.get("price") or 0)


def apply_run_limits(orders: list, limits: dict, balance: dict,
                     day_pnl_pct: float, market: str = "KR", alloc_ctx: dict = None) -> tuple:
    """금액·건수·서킷브레이커 + **gap 채우기**. 규율 SELL은 건수 제한 면제.

    ★ 2026-09-22 — 투자 천장(`max_invested_pct`)·상수 예약(반대편 15% · 같은 시장 25%)·현금 하한을 지웠다.
    ★ 2026-09-23 — **일일 주문 상한도 지웠다**(사용자). 남은 한도는 이 시장 현금 − **판단된 예약**
    (`allocation.reserved`) · long-only뿐이고, 그것이 의도다 — 판단한 비율에 빠르게 도달하는 것이 목표이기 때문이다. 그리고 판단한 비율에 못 미치면(gap) 승인 매수 합이 min(gap, 쓸 수 있는 현금)에 닿을 때까지
    BUY 증분을 **비례로 키운다** — 미달은 설명이 아니라 채우는 것이다.
    `alloc_ctx` = {gap_amt, deployable, reserved_here, need}(run()이 만든다). 없으면 gap 채우기 없이 한도만 본다.
    """
    rejected = []
    alloc_ctx = alloc_ctx or {}
    currency = balance.get("currency", "KRW")
    halt_pct = limits.get("portfolio_daily_loss_halt_pct")

    positions = balance.get("positions", [])
    pf = portfolio_equity(market, balance)
    pfl = pf_local(pf, market, balance)
    equity = pfl["equity"]
    notes = [pf["why"]]

    sells = [o for o in orders if o["action"] == "SELL"]
    buys = [o for o in orders if o["action"] == "BUY"]

    if balance.get("cash") is None:
        for o in buys:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": "예수금 미확인 — 살 수 있는 현금을 알 수 없어 매수를 보류한다"})
        buys = []
    here_cash = balance.get("cash") or 0            # 이 시장 현금 — 실제로 살 수 있는 한도
    # ★ FB-261003-10(2026-10-03 승인) — 같은 승인의 **비회전 매도 대금**을 매수 예산에 넣는다. 예전에는 넣지 않아 판 돈이
    #   있는데도 '자금 부족'으로 매수가 거부됐다(14일 2건). 그 돈을 쓰는 매수에는 `needs_sell_fill`이 붙고, execute가
    #   그 매도의 **체결을 확인한 뒤에만** 보낸다(미체결 대금은 쓰지 않는다 — 체결분만큼으로 깎는다).
    own_cash = here_cash
    sell_proceeds = sum(int(o["qty"]) * float(o["price"]) for o in sells if o.get("source") != "funding")
    sell_tickers = [o["ticker"] for o in sells if o.get("source") != "funding"]
    if sell_proceeds > 0 and buys:
        here_cash += sell_proceeds
        notes.append(f"같은 승인 매도 대금 {sell_proceeds:,.0f} {currency}({', '.join(sell_tickers)})를 매수 예산에 넣는다 — "
                     f"그 돈을 쓰는 매수는 매도 체결 확인 뒤에만 나간다")
    reserved_here = float(alloc_ctx.get("reserved_here") or 0)
    if reserved_here:
        notes.append(f"판단된 예약 {reserved_here:,.0f} {currency}(이 시장 armed 논지 몫)를 현금에서 뺀다")

    if halt_pct is not None and day_pnl_pct is not None and day_pnl_pct <= halt_pct:
        for o in buys:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": f"서킷브레이커: 당일 {day_pnl_pct:+.2f}% ≤ {halt_pct:+.2f}%, 신규 매수 중지"})
        buys = []

    final = list(sells)
    # ★ 일일 상한은 **없다**(2026-09-23 사용자: "일일 상한 없애"). 오늘 이미 나간 금액은 판정에서 빠지고 **기록으로만** 남는다 —
    #   두 번 돌아도 같은 금액이 두 번 나가지 않는 근거는 상한이 아니라 ① gap이 *지금* 투자비중을 보므로 두 번째 run은
    #   이미 채운 만큼 적게 사고 ② `execute.dedupe_against_day`가 같은 승인의 같은 건을 다시 안 보내고 ③ `run_auto`의 일일 락이다.
    spent, counted = spent_today(market, currency)
    alloc_ctx["spent_today"] = {"amount": round(spent), "count": counted, "currency": currency}
    if spent:
        notes.append(f"오늘 이미 나간 매수 {spent:,.0f} {currency}({counted}건) — 기록이다(한도가 아니다)")
    counted += sum(1 for o in sells if not o.get("exempt_from_count_limit"))

    # ★ gap 채우기 — 승인 매수 합이 need(= min(gap, 쓸 수 있는 현금))에 못 미치면 증분을 비례로 키운다.
    scaled = None
    need = float(alloc_ctx.get("need") or 0)
    alloc_ctx["need_effective"] = need
    if buys and need > 0:
        buy_sum = sum(int(o["qty"]) * float(o["price"]) for o in buys)
        if 0 < buy_sum < need:
            k = need / buy_sum
            before = [(o["ticker"], int(o["qty"])) for o in buys]
            grown = []
            for o in buys:
                q = int(int(o["qty"]) * k)
                grown.append({**o, "qty": max(int(o["qty"]), q),
                              "increment_pct": round(float(o.get("increment_pct") or 0) * k, 3),
                              "scaled_from_qty": int(o["qty"])})
            buys = grown
            # 비례로 키운 뒤 내림으로 남는 자투리를 제안 순서대로 1주씩 돌려 채운다(2026-10-15 — 미달 검사가 '가장 싼 1주'로 센다)
            left = need - sum(int(o["qty"]) * float(o["price"]) for o in buys)
            added = True
            while added and left > 0:
                added = False
                for i, o in enumerate(buys):
                    px = float(o["price"])
                    if 0 < px <= left:
                        buys[i] = {**o, "qty": int(o["qty"]) + 1}
                        left -= px
                        added = True
            scaled = {"k": round(k, 3), "before": before, "after": [(o["ticker"], o["qty"]) for o in buys],
                      "why": (f"gap 채우기 — 제안 합 {buy_sum:,.0f} {currency} < 필요 {need:,.0f}"
                              f"(gap {float(alloc_ctx.get('gap_amt') or 0):,.0f} · 쓸 수 있는 현금 "
                              f"{float(alloc_ctx.get('deployable') or 0):,.0f}) → 증분 ×{k:.2f}")}
            notes.append(scaled["why"])

    funding_left = int(limits.get("funding_max_per_run") if limits.get("funding_max_per_run") is not None else 1)
    fill_ratio = float(limits.get("funding_min_fill_ratio") if limits.get("funding_min_fill_ratio") is not None else 0.5)
    selling = {o["ticker"] for o in sells}
    # ★ 이번 run에 BUY로 나가는 종목은 회전 매도 대상에서 뺀다 — 2026-09-22 KR: LG엔솔을 사는 run에서 테스 자금이
    #   모자라자 LG엔솔 11주를 팔고 8주를 되사는 판정이 나왔다.
    buying = {o["ticker"] for o in buys}

    for o in buys:
        amt = int(o["qty"]) * float(o["price"])
        # ★ 목표는 포트폴리오, 집행은 그 시장 현금(판단된 예약을 뺀 것). 부족하면 깎고, 깎아도 의도의 절반이 안 되면
        #   이 시장 최대 보유를 팔아 메운다 — 사람에게 환전·입금을 묻지 않는다(2026-09-16).
        spendable = max(0.0, here_cash - reserved_here)
        if amt > spendable:
            intended = amt
            shaved_qty = int(spendable // o["price"]) if o["price"] > 0 else 0
            if shaved_qty * o["price"] < intended * fill_ratio and funding_left > 0:
                need_f = intended - spendable
                cands = rotation_candidates(positions, selling | buying)
                if cands:
                    src, src_dist = cands[0]
                    sp = float(src["price"])
                    if market == "KR":
                        sp = float(snap_kr_price(sp, "SELL"))
                    sq = min(int(src["qty"]), int(-(-need_f // sp)))
                    proceeds = sq * sp
                    weak = "논지 없음" if not held_theses_of(src.get("ticker")) else \
                        (f"손절까지 {src_dist:.1f}%" if src_dist is not None else "손절 다리 없음")
                    why_f = (f"회전 매도 — {o['ticker']} 매수 자금 {need_f:,.0f} {currency} 부족 → "
                             f"논지가 가장 약한 보유 {src.get('ticker')}({weak} · 손익 {float(src.get('pnl_pct') or 0):+.1f}%) "
                             f"{sq}/{src['qty']}주 @{sp:,.0f} ({proceeds:,.0f}) 매도로 메운다 — 축이 확인돼 이익 중인 보유는 "
                             f"자금원에서 뺐다 · 가진 돈 안에서 해결한다")
                    sell_o = {"ticker": src["ticker"], "name": src.get("name", ""), "market": market,
                              "excd": src.get("excd"), "action": "SELL", "qty": sq, "price": sp,
                              "source": "funding", "funds": o["ticker"], "reasons": [why_f],
                              "exempt_from_count_limit": True,
                              "full_exit": sq >= int(src["qty"])}
                    final.insert(sum(1 for x in final if x["action"] == "SELL"), sell_o)
                    selling.add(src["ticker"])
                    funding_left -= 1
                    spendable += proceeds
                    here_cash += proceeds
                    own_cash += proceeds          # 회전 매도 대금은 그 흐름(execute 회전 확정)이 맡는다 — 매도 의존 표시 대상 아님
                    notes.append(why_f)
                    o = {**o, "funding": {"sold": src["ticker"], "qty": sq, "proceeds": proceeds},
                         "reasons": list(o.get("reasons") or []) + [why_f]}
                    shaved_qty = min(int(o["qty"]), int(spendable // o["price"]))
            if shaved_qty <= 0:
                rejected.append({"ticker": o["ticker"], "action": "BUY",
                                 "why": f"자금 배분 — {market} 자산으로는 1주({o['price']:,.0f})도 못 산다 "
                                        f"(쓸 수 있는 현금 {spendable:,.0f} {currency}"
                                        + (f" · 판단된 예약 {reserved_here:,.0f}" if reserved_here else "")
                                        + f" · 주문 {intended:,.0f}). 회전 매도 대상도 없다"})
                continue
            if shaved_qty < int(o["qty"]):
                why = (f"자금 배분 — {market} 쓸 수 있는 현금 {spendable:,.0f} {currency}"
                       + (f"(판단된 예약 {reserved_here:,.0f} 제외)" if reserved_here else "")
                       + f"에 맞춰 {o['qty']}→{shaved_qty}주로 깎았다 (주문 {intended:,.0f} · "
                       f"포트폴리오 기준 비중 판단은 유지 · 이 시장 자산이 상한)")
                notes.append(f"{o['ticker']} {why}")
                o = {**o, "qty": shaved_qty,
                     "shaved": {"from_qty": o["qty"], "to_qty": shaved_qty, "why": why},
                     "reasons": list(o.get("reasons") or []) + [why]}
            amt = int(o["qty"]) * o["price"]
        own_left = own_cash - reserved_here
        if sell_proceeds > 0 and amt > max(0.0, own_left):
            dep = amt - max(0.0, own_left)
            o = {**o, "needs_sell_fill": list(sell_tickers), "sell_dependent_amt": round(dep, 2),
                 "reasons": list(o.get("reasons") or []) + [f"매도 대금 의존 {dep:,.0f} {currency} — "
                                                             f"{', '.join(sell_tickers)} 체결 확인 뒤 전송"]}
        own_cash -= amt
        spent += amt
        here_cash -= amt
        counted += 1
        final.append(o)

    for n in notes:
        print(f"  · {n}")
    alloc_ctx["scaled"] = scaled
    alloc_ctx["max_buy_price"] = max([float(o["price"]) for o in final if o.get("action") == "BUY"] or [0.0])
    alloc_ctx["min_buy_price"] = min([float(o["price"]) for o in final if o.get("action") == "BUY"] or [0.0])
    return final, rejected


# ------------------------------------------------------------- 미달 강제(deficit)

# 제안이 **애초에 성립하지 않는** 거부 사유 — 이런 제안은 미달 건수에 세지 않는다.
# (한도·현금·축 상한 거부는 가드의 몫이라 센다 — 제안자는 할 일을 한 것이다.)
VALIDITY_REJECTS = ("유니버스 밖", "MISMATCH", "weight_target_pct", "현재가를 알 수 없다",
                    "금지 패턴", "confidence", "MATCH(T1/T2)", "미보유 종목 SELL", "목표 여유 없음",
                    "주문 근거 검증")


def deficit_check(sig: dict, limits: dict, market: str, invested_pct: float,
                  rejected: list, today: str, alloc_ctx: dict = None, final: list = None) -> dict:
    """판단한 비율(`allocation.target_invested_pct`) 아래일 때 **이 run이 채웠는가**를 기계로 센다.

    ① 승인 BUY 합 ≥ need(= min(gap, 쓸 수 있는 현금)) — 못 채웠고 그 원인이 한도가 아니면 short
    ② BUY 제안 ≥ `min_proposals_below_target[market]` ③ 서로 다른 축 ≥ `min_distinct_axes_below_target`
    ④ 오늘 새로 `armed`된 이 시장 논지마다 BUY 제안(e0). ②~④는 2026-09-16 사용자 결정(폭 강제)이다.
    """
    alloc_ctx = alloc_ctx or {}
    tgt = alloc_ctx.get("target_invested_pct")
    need_map = limits.get("min_proposals_below_target") or {}
    need_n = need_map.get(market.upper()) if isinstance(need_map, dict) else None
    need_axes = limits.get("min_distinct_axes_below_target")
    out = {"invested_pct": round(invested_pct, 2), "target": tgt, "required": need_n,
           "required_axes": need_axes, "counted": 0, "distinct_axes": 0, "tickers": [],
           "e0_missing": [], "gap_pct": round(float(alloc_ctx.get("gap_pct") or 0), 2),
           "gap_amt": round(float(alloc_ctx.get("gap_amt") or 0)), "deployable": round(float(alloc_ctx.get("deployable") or 0)),
           "need": round(float(alloc_ctx.get("need") or 0)), "approved_buy": 0, "covered_pct": None,
           "short": False, "why": ""}
    if tgt is None:
        out["why"] = "검사 꺼짐(allocation 판단 없음)"
        return out
    if invested_pct >= float(tgt):
        out["why"] = f"투자비중 {invested_pct:.1f}% ≥ 판단 목표 {float(tgt):.0f}% — 미달 아님"
        return out

    approved_buy = sum(int(o["qty"]) * float(o["price"]) for o in (final or []) if o.get("action") == "BUY")
    out["approved_buy"] = round(approved_buy)
    cap_trimmed = float(alloc_ctx.get("cap_trimmed") or 0)
    out["cap_trimmed"] = round(cap_trimmed)
    need = float(alloc_ctx.get("need_effective", alloc_ctx.get("need")) or 0)
    out["need"] = round(need)
    out["covered_pct"] = round(min(100.0, approved_buy / need * 100), 1) if need > 0 else 100.0

    bad = {r.get("ticker") for r in rejected
           if any(k in str(r.get("why", "")) for k in VALIDITY_REJECTS)}
    counted = []
    for p in (sig.get("proposals") or []):
        if p.get("action") == "BUY" and p.get("ticker") and p["ticker"] not in bad:
            counted.append(p["ticker"])
    idx = axis_index()
    axes = set()
    for t in counted:
        opts = axes_of(idx, t) or [f"ticker:{t}"]
        free = next((a for a in opts if a not in axes), None)
        if free:
            axes.add(free)
    out["counted"], out["distinct_axes"], out["tickers"] = len(counted), len(axes), counted

    store = JOURNAL_DIR / "theses.json"
    try:
        theses = (json.loads(store.read_text(encoding="utf-8")).get("theses") or []) if store.exists() else []
    except (OSError, json.JSONDecodeError):
        theses = []
    for th in theses:
        if (th.get("status") == "armed" and str(th.get("market", "")).upper() == market.upper()
                and str(th.get("created", "")).startswith(today)
                and th.get("ticker") not in counted):
            out["e0_missing"].append(th.get("ticker"))

    reasons = []
    # "채웠다" = 남은 필요액으로 승인된 종목 중 어느 것도 한 주 더 살 수 없다(반올림 잔여는 미달이 아니다).
    # ★ 2026-10-15 — 기준은 **가장 싼** 승인 종목 1주다(예전엔 가장 비싼 것이라 그 아래 잔여를 미달로 안 봤다). gap 채우기가
    #   자투리를 1주씩 돌려 채우므로 이 기준이 정상 run을 막지 않는다.
    remaining = need - approved_buy - cap_trimmed        # 주문당 상한이 깎은 금액은 한도 원인 — 제안은 그만큼 채웠다
    min_px = float(alloc_ctx.get("min_buy_price") or alloc_ctx.get("max_buy_price") or 0)
    unfilled = need > 0 and remaining > max(min_px, need * 0.05)
    if unfilled:
        reasons.append(f"승인 매수 {approved_buy:,.0f} < 필요 {need:,.0f}({out['covered_pct']:.0f}%) — 한도가 아니라 제안이 모자라다"
                       + (" (BUY 제안 0건)" if not counted else ""))
    if need_n is not None and len(counted) < int(need_n):
        reasons.append(f"BUY 제안 {len(counted)}건 < 요구 {int(need_n)}건")
    if need_axes is not None and len(axes) < int(need_axes):
        reasons.append(f"서로 다른 축 {len(axes)}개 < 요구 {int(need_axes)}개")
    if out["e0_missing"]:
        reasons.append(f"오늘 세운 논지에 e0 제안 없음: {', '.join(out['e0_missing'])}")
    out["short"] = bool(reasons)
    out["why"] = (f"투자비중 {invested_pct:.1f}% < 판단 목표 {float(tgt):.0f}%(gap {out['gap_pct']:.1f}%p) — "
                  + ("; ".join(reasons) if reasons else
                     f"승인 매수 {approved_buy:,.0f}/{need:,.0f}({out['covered_pct']:.0f}%) · 제안 {len(counted)}건·축 {len(axes)}개·e0 충족"))
    return out


# ---------------------------------------------------------------------- main

def run(signal_path: Path, snapshot_path: Path, out_path: Path,
        discipline_only: bool = False, live: bool = False, refill: bool = False) -> int:
    """`refill`(2026-10-15) — 재투자 pass: 승인 뒤 따로 나간 매도가 판 돈을 같은 run 안에서 다시 쓴다(`refill.py`가 부른다).
    스냅샷은 매도 뒤 잔고·새 시세(캡처 스냅샷은 쓰지 않는다) · 매수 제안 + `refill_candidates`만 심사(모델 SELL 제안은 다시 내지 않는다) ·
    시그널 나이 상한 `refill_max_age_minutes`(기본 300) · 배분 원장에 쓰지 않는다(같은 run의 판단 행을 덮지 않게)."""
    now = datetime.now(KST)

    # ① KILL은 파싱 이전에 판정한다 — 전면 중지 신호가 JSON 파싱에 의존하면 안 된다.
    if KILL_PATH.exists():
        print(f"KILL 파일 존재 ({KILL_PATH}) — 주문 0건으로 종료.")
        return 0

    limits = _read_json(LIMITS_PATH, "limits")
    if limits.get("halt_all"):
        print("limits.halt_all=true — 주문 0건으로 종료.")
        return 0
    watchlist = _read_json(WATCHLIST_PATH, "watchlist")
    snapshot = _read_json(snapshot_path, "snapshot")

    balance = snapshot.get("balance") or {}
    prices = snapshot.get("prices") or {}
    day_pnl_pct = snapshot.get("day_pnl_pct")
    # ★ 시장을 먼저 정한다 — 고점 원장이 시장별로 나뉘어 있고, 예전에는 평평한 dict를
    #   그대로 읽어 **다른 시장의 고점으로 이 시장을 판정할 수** 있었다. 표기 규칙은
    #   `peaks.py` 하나에만 둔다(쓰는 쪽 journal과 갈리지 않게).
    market = snapshot.get("market", "KR")
    peaks = peaks_store.for_market(market, PEAKS_PATH)

    live_notes = []
    if live:
        # ★ 손절선은 캡처 시세가 아니라 **지금** 시세로 본다 — 5단은 캡처보다 한 시간쯤 뒤다.
        live_notes = refresh_live_prices(snapshot, market)
        prices = snapshot.get("prices") or {}
        for n in live_notes:
            print(f"  · {n}")

    orders = discipline_sells(balance.get("positions", []), limits, peaks)
    orders += thesis_exit_sells(snapshot, market, already={o["ticker"] for o in orders})
    orders += audit_reversal_sells(snapshot, market, already={o["ticker"] for o in orders})
    rejected = []

    alloc_ctx, alloc_out, sig = {}, None, None
    if not discipline_only:
        import allocation as _al
        sig = _read_json(signal_path, "signal")
        m_id = re.search(r"_(\d{6})_(kr|us)", str(signal_path))
        this_id = _al.make_id(m_id.group(1), m_id.group(2)) if m_id else _al.make_id(now.strftime("%y%m%d"), str(sig.get("market") or market))
        validate_signal(sig, limits, now, this_id,  # 실패 시 Rejection → run 전체 거부
                        max_age=(float(limits.get("refill_max_age_minutes", 300)) if refill else None))
        market = sig["market"]
        rule_post, _ = _after_sells(balance, [o for o in orders if o.get("action") == "SELL"])
        screen_sig = sig
        if refill:
            buys = [p for p in sig.get("proposals") or [] if p.get("action") == "BUY"]
            have = {str(p.get("ticker")) for p in buys}
            screen_sig = {**sig, "proposals": buys + [dict(c, refill_candidate=True) for c in sig.get("refill_candidates") or []
                                                      if str(c.get("ticker")) not in have]}
            print(f"재투자 pass — 매수 제안 {len(buys)}건 + 재투자 후보 {len(screen_sig['proposals']) - len(buys)}건을 매도 뒤 잔고로 다시 심사한다")
        if sig.get("no_trade") and not refill:
            print("시그널 no_trade=true — 판단 보류. 규율 주문만 처리한다.")
        else:
            try:
                import support_break as _sb
                sup = _sb.ticker_map(market, snapshot)
            except Exception as e:                # noqa: BLE001 — 판정 실패가 run을 막지 않는다(이어받기 절이 같은 실패를 적는다)
                sup = {}
                print(f"  · 지지 이탈 판정 실패({type(e).__name__}: {str(e)[:80]}) — 이 run은 그 거부 없이 간다")
            if sup:
                print(f"  · 지지 이탈 {len(sup)}종목 — 이 run 추가 매수 거부: {', '.join(sup)}")
            acc, rej = screen_proposals(screen_sig, limits, watchlist, balance, prices, sup_break=sup, post_bal=rule_post)
            # 규율 SELL과 겹치는 종목은 규율 쪽을 남긴다(더 보수적).
            disc_tickers = {o["ticker"] for o in orders}
            orders += [o for o in acc if o["ticker"] not in disc_tickers]
            rejected += rej
        # ★ 배분(gap·need)은 이 run의 매도(규율·논지·소급·모델 SELL)를 **다 반영한 뒤** 잰다(2026-10-15) — 판 돈이 현금으로
        #   남으면 미달이다. 회전 매도는 apply_run_limits가 매수를 위해 고르는 것이라 여기 없다.
        alloc_ctx, alloc_out = allocation_context(sig, market, balance,
                                                  sells=[o for o in orders if o.get("action") == "SELL"])
        if alloc_ctx.get("error"):
            raise Rejection(alloc_ctx["error"])
        for n in alloc_ctx.get("notes") or []:
            print(f"  · {n}")

    final, run_rej = apply_run_limits(orders, limits, balance, day_pnl_pct, market, alloc_ctx)
    rejected += run_rej
    scaled = alloc_ctx.get("scaled")

    # ★ 주문당 상한(2026-10-12 사용자 결정) — 크기가 다 정해진 뒤 마지막에. 상한이 깎은 금액은 '제안이 모자란 것'이
    #   아니라 한도 원인이므로 미달 검사에서 채운 것으로 센다.
    cap_fx = float(balance.get("exchange_rate") or portfolio_equity(market, balance).get("fx") or 0)
    final, cap_rej, cap_notes, cap_trimmed = apply_order_cap(final, limits, market, cap_fx)
    rejected += cap_rej
    alloc_ctx["cap_trimmed"] = cap_trimmed
    for n in cap_notes:
        print(f"  · {n}")
    cap_cfg = order_cap_cfg(limits)

    # ★ 미달 강제 — 판단한 비율 아래인데 이 run이 못 채웠으면(한도 원인 제외) `deficit_short: true`. 주문은 그대로
    #   승인한다(막는 것은 게이트다 — 이 run은 4·5단으로 돌아가 제안을 채우고 다시 돈다).
    deficit = {"short": False, "why": "규율 전용 run — 미달은 refill_need가 잰다"}
    refill_need = None
    if not discipline_only:
        pb = alloc_ctx.get("post_balance") or balance        # 이 run의 매도 뒤 잔고 — 판 돈이 남으면 미달이다
        pfl = pf_local(portfolio_equity(market, pb), market, pb)
        inv_d = (alloc_ctx["invested_pct_eff"] if alloc_ctx.get("market_share") is not None   # 실계좌: 이 시장 몫 대비 투자비중
                 else pfl["invested_pct"])
        deficit = deficit_check(sig, limits, market, inv_d, rejected,
                                now.strftime("%Y-%m-%d"), alloc_ctx, final)
    elif snapshot.get("kind") in ("stops", "stops_daytime"):
        # 장중 고가·저가로 판정만 하는 pass(stops_intraday_*)는 극값이라 세지 않는다 — 실제 시세로 판 pass만
        refill_need = _refill_need(market, balance, final)

    approved = {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "generated_at": now.isoformat(),
        "signal_ref": str(signal_path) if signal_path else None,
        "snapshot_ref": str(snapshot_path),
        "limits_sha256": sha256_of(LIMITS_PATH),
        "orders": final,
        "rejected": rejected,
        "shaved": [{**o["shaved"], "ticker": o["ticker"]} for o in final if o.get("shaved")],
        "funding": [{"sold": o["ticker"], "qty": o["qty"], "price": o["price"], "funds": o.get("funds"),
                     "why": (o.get("reasons") or [""])[0]} for o in final if o.get("source") == "funding"],
        "live_prices": live_notes,
        "allocation": alloc_out,
        "scaled_up": scaled,
        "deficit": deficit,
        "deficit_short": bool(deficit.get("short")),
        # 실계좌 전환(2026-10-12): 이 승인이 어느 계좌 모드에서 나왔고 주문당 상한을 어떻게 걸었나 — execute가 대조한다.
        "account_mode": account_mode(limits),
        "order_cap": ({**cap_cfg, "fx": cap_fx if market != "KR" else 1.0, "trimmed_amt": round(cap_trimmed, 2)}
                      if cap_cfg else None),
        # 갈래 ① 주문 근거 검증 결과(시그널에서 옮김) — dispatch 게이트가 이 칸이 있는지 본다(규율 전용 run은 대상 아님)
        "track1": ((sig or {}).get("track1") or None) if not discipline_only else None,
        # 노트 필수 요소 검사(note_lint.py signal) 결과 — dispatch 게이트가 'NOTE-LINT OK'를 찾는다
        "note_lint": ((sig or {}).get("note_lint") or None) if not discipline_only else None,
        "discipline_only": bool(discipline_only),
        "refill_need": refill_need,
        "refill": bool(refill),
        "refill_of": snapshot.get("refill_of") if refill else None,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")
    if alloc_out and not refill:
        try:
            import allocation as _al
            row = _al.record(approved, str(signal_path))
            print(f"  · 배분 원장 기록 {row['id']} — 목표 {row['target_invested_pct']}% · based_on {row['based_on']}")
        except Exception as e:                       # noqa: BLE001 — 원장 쓰기 실패가 승인을 막지 않는다
            print(f"  · 배분 원장 기록 실패({type(e).__name__}: {e})", file=sys.stderr)

    print(f"승인 {len(final)}건 / 거부 {len(rejected)}건 → {out_path}")
    for o in final:
        src = "규율" if o["source"] == "discipline" else ("감사 소급" if o["source"] == "audit_reversal" else "제안")
        print(f"  [{src}] {o['action']:4} {o['ticker']} {o.get('name', '')} "
              f"x{o['qty']} @ {o['price']:,.2f}")
        for r in o.get("reasons", []):
            print(f"         └ {r}")
    for r in rejected:
        print(f"  [거부] {r['action']:4} {r['ticker']}: {r['why']}")
    if deficit.get("short") and refill:
        print(f"  · 재투자 뒤에도 미달: {deficit['why']} — 남은 몫은 `stage.py refill-defer`로 다음 run에 넘긴다")
    elif deficit.get("short"):
        print(f"\n★ 미달 강제 — deficit_short=true: {deficit['why']}. "
              f"dispatch/no_trade 게이트가 이 파일로 막는다 — 4·5단으로 돌아가 제안을 채우고 "
              f"risk_guard를 다시 돌려라(거부를 뒤집는 재실행이 아니라 부족을 채우는 재실행이다).",
              file=sys.stderr)
    elif deficit.get("why"):
        print(f"  · 미달 검사: {deficit['why']}")
    if refill_need:
        print(("★ 재투자 필요 — " if refill_need.get("short") else "  · 재투자 판정: ") + str(refill_need.get("why")))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="시그널을 검증·승인해 approved 파일을 낸다")
    ap.add_argument("signal", nargs="?", help="signals/signal_YYMMDD_kr.json")
    ap.add_argument("--snapshot", required=True, help="data/snapshot_YYMMDD_kr.json")
    ap.add_argument("--out", help="기본: signals/approved_<시그널명 뒷부분>.json")
    ap.add_argument("--discipline-only", action="store_true",
                    help="LLM 시그널 없이 손절·트레일링·논지 매도 조건만 평가한다")
    ap.add_argument("--live", action="store_true",
                    help="보유 종목 시세를 브로커에서 다시 받아(GET) 손절·목표를 지금 값으로 판정한다")
    ap.add_argument("--refill", action="store_true",
                    help="재투자 pass — 매도 뒤 잔고 스냅샷으로 매수 제안·refill_candidates만 다시 심사(보통 refill.py가 부른다)")
    args = ap.parse_args()

    if not args.discipline_only and not args.signal:
        ap.error("signal 경로가 필요하다 (또는 --discipline-only)")
    if args.refill and args.discipline_only:
        ap.error("--refill은 시그널이 있는 재투자 pass다 — --discipline-only와 같이 쓰지 않는다")

    snapshot_path = Path(args.snapshot)
    signal_path = Path(args.signal) if args.signal else None
    if args.out:
        out_path = Path(args.out)
    elif signal_path:
        out_path = SIGNALS_DIR / signal_path.name.replace("signal_", "approved_", 1)
    else:
        stem = snapshot_path.stem.replace("snapshot_", "")
        out_path = SIGNALS_DIR / f"approved_{stem}.json"

    try:
        return run(signal_path, snapshot_path, out_path, args.discipline_only, live=args.live, refill=args.refill)
    except Rejection as e:
        # fail-closed: 사유를 남기고 주문 0건으로 끝낸다. 부분 구제 없음.
        print(f"거부(전체) — 그날 거래 없음: {e}", file=sys.stderr)
        log = SIGNALS_DIR / f"rejected_{datetime.now(KST):%y%m%d_%H%M%S}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            f"{datetime.now(KST).isoformat()}\nsignal={signal_path}\nsnapshot={snapshot_path}\n"
            f"REJECT ALL: {e}\n", encoding="utf-8")
        print(f"사유 기록: {log}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
