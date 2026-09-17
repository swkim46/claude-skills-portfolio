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
from kis_client import snap_kr_price

HERE = Path(__file__).parent
CONFIG_DIR = HERE / "config"
CALENDAR_PATH = HERE / "journal" / "calendar.json"


DATA_DIR = HERE / "data"
JOURNAL_DIR = HERE / "journal"


def _latest_curve_row(market: str):
    """`journal/equity_curve.jsonl`에서 이 시장의 마지막 행(없으면 None). 못 읽는 줄은 건너뛴다."""
    path = JOURNAL_DIR / "equity_curve.jsonl"
    if not path.exists():
        return None
    last = None
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if str(r.get("market", "")).upper() == market.upper() and r.get("cash") is not None:
            last = r
    return last


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
    if this_market == "US":
        fx = balance.get("exchange_rate")
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
    dated = []
    for f in cands:
        try:
            dated.append((json.loads(f.read_text(encoding="utf-8")).get("generated_at") or "", f))
        except (json.JSONDecodeError, OSError):
            continue          # 못 읽는 파일은 후보에서 빼고, 다른 후보로 계속 간다
    dated.sort()
    snaps = [f for _, f in dated]
    if not snaps:
        missing.append(f"{other} 스냅샷이 없다"
                       + (f" (날짜 없는 파일 {len(cands) - len(dated)}건은 후보가 아니다)"
                          if cands else ""))
    else:
        try:
            s = json.loads(snaps[-1].read_text(encoding="utf-8"))
            ob = s.get("balance") or {}
            src_name = snaps[-1].name
            # ★ 스냅샷보다 **저널 마지막 행이 더 새로우면** 그것을 쓴다 — 9/15 US 스냅샷(23:14)은 VST 체결
            #   전이라 9/16 KR run이 투자비중 1.1%(실제 5.1%)로 계산했다. 저널 행은 체결 뒤 잔고다.
            #   환율은 저널 행에 없으니 스냅샷 것을 그대로 쓴다.
            row = _latest_curve_row(other)
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
            if other == "US":
                fx = ob.get("exchange_rate")
        except (json.JSONDecodeError, OSError) as e:
            missing.append(f"{other} 스냅샷을 읽을 수 없다({type(e).__name__})")

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
            "why": (f"포트폴리오 자산 {krw:,.0f}원 = "
                    + " + ".join(f"{m} {v['krw']:,.0f}" for m, v in parts.items())
                    + f" · 현금 {cash_krw:,.0f} · 투자 {inv_krw:,.0f}"
                    + f" (환율 {fx:,.2f}, as-of {' · '.join(asof) or '이번 run'})")}


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


def other_market_reserve(limits: dict, market: str, equity: float) -> tuple:
    """반대편 시장의 `armed` 논지를 위해 **포트폴리오 현금을 예약**한다.
    반환 (금액, 사유, 대상 논지 목록).

    ★ 왜 — "미국에서 눈여겨보던 종목이 있는데 국내장에서 별 거 아닌 종목을 사서
    돈이 없어 못 사는" 것을 막는 자리다. 계좌가 물리적으로 갈려 있는 동안에도
    **판단과 기록은 통합 기준으로** 해두면, 실계좌로 합칠 때 그대로 맞는다.

    예약 크기는 `armed` 논지 수 × `per_position_max_pct`(그 논지가 들어갈 수 있는
    최대치)로 추정하고 `reserve_for_other_market_pct`로 **상한을 둔다** —
    상한이 없으면 반대편 논지가 쌓일수록 이번 run이 굶는다.
    반대편 armed 논지가 0건이면 예약도 0이다.
    """
    cap_pct = limits.get("reserve_for_other_market_pct")
    if cap_pct is None or equity <= 0:
        return 0.0, "", []
    other = "US" if (market or "KR").upper() == "KR" else "KR"
    try:
        rows = (json.loads((JOURNAL_DIR / "theses.json").read_text(encoding="utf-8"))
                or {}).get("theses") or []
    except (json.JSONDecodeError, OSError) as e:
        # 못 읽으면 **예약하지 않는다**(0). 여기서 보수적으로 굴면 읽기 오류가
        # 조용히 매수를 막아, 원인이 자금 부족으로 오인된다.
        return 0.0, f"반대편 논지를 못 읽어 예약 없음 ({type(e).__name__})", []
    armed = [r for r in rows
             if (r.get("market") or "").upper() == other and r.get("status") == "armed"]
    if not armed:
        return 0.0, f"{other} armed 논지 0건 — 예약 없음", []
    per = float(limits.get("per_position_max_pct") or 0)
    pct = min(len(armed) * per, float(cap_pct))
    amt = equity * pct / 100
    names = [f"{r.get('ticker')}({r.get('id')})" for r in armed]
    return amt, (f"{other} 대기 논지 {len(armed)}건을 위해 {pct:.1f}% "
                 f"({amt:,.0f}) 예약 — {' · '.join(names)}"), names


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


def allocation_multiplier(limits: dict, invested_pct: float) -> tuple:
    """목표 투자비중에서 멀면 **종목당 목표를 키운다.** (배수, 사유)

    왜: 종목당 비중만 정하면 `종목 수 × 비중`이 천장이다. 종목 수는 냉각·편입 판정을 거쳐야
    늘어나므로 몇 주가 걸리고, 그 사이 현금이 그대로 남는다.
    *실측(2026-09-10): 4종목 × 4% = 16%가 천장이었고 실제 투자비중은 10.8%, 현금 89%였다.*
    이미 논지가 선 종목의 크기를 키우는 것이 더 빠르고, 그것이 "논지가 서 있으면 자금을 넣어라"의
    실행이다. `per_position_max_pct`가 상한을 잡으므로 무한히 커지지 않는다.
    """
    ps = limits.get("position_sizing") or {}
    tgt = limits.get("target_invested_pct")
    mx = float(ps.get("deficit_multiplier_max") or 1.0)
    if not tgt or mx <= 1.0:
        return 1.0, ""
    deficit = max(0.0, float(tgt) - invested_pct)
    m = 1.0 + min(1.0, deficit / float(tgt)) * (mx - 1.0)
    if m <= 1.0:
        return 1.0, ""
    return m, (f"투자비중 {invested_pct:.1f}% vs 목표 {tgt:.0f}% (미달 {deficit:.1f}%p) "
               f"→ 종목당 목표에 {m:.2f}배")


def halve_window(limits: dict, today: str) -> tuple:
    """이벤트 임박으로 크기를 절반으로 줄여야 하는가. (여부, 사유)

    **판정을 분석 단계에 맡기지 않는다.** 예전에는 스킬 문서가 "이벤트가 임박하면 절반"이라고만
    적어두고 실제 적용은 제안자의 재량이었다. 재량이면 매번 다르게 적용되고, 실제로 그렇게 됐다.

    그리고 절반 사유를 **논지를 뒤집을 수 있는 거시 분기점**으로 좁힌다(limits의
    halve_only_for_event_kinds). 예전에는 날짜만 있으면 무엇이든 절반이라, 실적·제품행사·
    지수변경까지 걸려 향후 15영업일 중 8일이 절반이었다 — 일정표를 성실히 채울수록 영원히
    반쪽만 사게 되는 구조였다.
    """
    ps = limits.get("position_sizing") or {}
    days = ps.get("halve_if_dated_event_within_days")
    if not days:
        return False, ""
    kinds = ps.get("halve_only_for_event_kinds")
    try:
        cal = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ""            # 일정표가 없으면 절반 사유도 없다(없는 근거로 줄이지 않는다)
    d0 = datetime.strptime(today, "%Y-%m-%d").date()
    for e in cal.get("events") or []:
        try:
            gap = (datetime.strptime(e["date"], "%Y-%m-%d").date() - d0).days
        except (KeyError, ValueError):
            continue
        if not 0 <= gap <= days:
            continue
        if kinds and e.get("kind") not in kinds:
            continue               # 거시 분기점이 아니면 절반 사유가 아니다
        return True, f"{e['date']} {e.get('event', '')}({e.get('kind', '?')}) D-{gap}"
    return False, ""
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

def validate_signal(sig: dict, limits: dict, now: datetime) -> None:
    """하나라도 어긋나면 run 전체 거부. 부분 구제 없음."""
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
    max_age = limits.get("signal_max_age_minutes", 180)
    if age_min > max_age:
        raise Rejection(f"시그널이 낡았다: {age_min:.0f}분 전 생성 (허용 {max_age}분)")
    if age_min < -5:
        raise Rejection(f"시그널 생성 시각이 미래다: {age_min:.0f}분")

    proposals = sig["proposals"]
    if not isinstance(proposals, list):
        raise Rejection("proposals가 배열이 아니다")

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


def thesis_exit_sells(snapshot: dict, market: str, already: set = None) -> list:
    """**보유 논지의 가격 매도 조건은 기계가 판다** — 손절(`price <=`·`pnl_pct <=`)은 전량,
    목표(`price >=`)는 `sell_pct`(기본 50%). `judge`·시간 조건은 모델 몫으로 남긴다.

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
            if not (is_stop or is_target):
                continue                      # 시간 조건 등은 모델이 판정한다
            pq = int(pos["qty"])
            if is_stop:
                qty = pq
            else:
                pct = float(trg.get("sell_pct") or 50)
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
                "reasons": [f"논지 {t.get('id')} {trg.get('id')} "
                            f"{'손절' if is_stop else '목표'} `{expr}` "
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
        client = KisClient()
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


def screen_proposals(sig: dict, limits: dict, watchlist: dict,
                     balance: dict, prices: dict) -> tuple:
    """proposal별 판정. 반환 (accepted, rejected)."""
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
    axis_cap = limits.get("axis_max_pct")

    accepted, rejected = [], []
    halve_on, halve_why = halve_window(limits, datetime.now(KST).strftime("%Y-%m-%d"))
    notes = [pf["why"]]
    if not pf.get("ok"):
        notes.append("★ 반대편 스냅샷을 못 쟀다 — 이 시장 자산만으로 재므로 비중이 "
                     "실제보다 크게 나온다(보수적). 반대편 run을 돌리면 정확해진다.")
    elif market == "US":
        notes.append(f"분모 단위 환산 — 포트폴리오 {pf['krw']:,.0f}원 "
                     f"÷ {pf['fx']:,.2f} = {equity:,.0f}달러")
    if halve_on:
        notes.append(f"이벤트 임박 — 목표 비중에 계수 적용 예정 ({halve_why})")

    # 투자비중 하한 — **막지 않고 드러낸다.** 강제로 사게 하면 그게 억지 매매이고,
    # 아무 말도 안 하면 기권이 조용히 누적된다. 그래서 '설명해야 할 간극'으로만 남긴다.
    invested_pct = pfl["invested_pct"]
    floor = limits.get("min_invested_pct")
    below_floor = floor is not None and invested_pct < floor
    if below_floor:
        notes.append(
            f"★ 투자비중 {invested_pct:.1f}% < 하한 {floor:.0f}% — 살아 있는 논지가 있는데 "
            f"자금이 안 나가 있으면 그 사유를 분석노트에 적어야 한다(강제 매수 아님)."
        )

    # ★ 주식/현금 비율을 의도로 맞춘다 — 목표에서 멀면 종목당 목표를 키운다.
    alloc_mult, alloc_why = allocation_multiplier(limits, invested_pct)
    if alloc_why:
        notes.append(alloc_why)

    # 이벤트 계수 — 하한 미달이면 적용하지 않는다(미달이 이벤트보다 큰 문제다).
    ps_cfg = limits.get("position_sizing") or {}
    event_factor = float(ps_cfg.get("event_factor") or 0.5)
    if halve_on and below_floor and ps_cfg.get("skip_event_factor_below_floor"):
        halve_on = False
        notes.append(f"이벤트 계수 미적용 — 투자비중이 하한 아래다({invested_pct:.1f}% < {floor:.0f}%)")

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

        min_conf = rules.get("min_confidence")
        if min_conf is not None and p["confidence"] < min_conf:
            reject(p, f"confidence {p['confidence']:.2f} < {min_conf}")
            continue

        verdicts = [str(e.get("verdict", "")).upper() for e in p.get("evidence", [])]
        if rules.get("reject_if_any_mismatch", True) and "MISMATCH" in verdicts:
            reject(p, "근거에 MISMATCH 포함 (fact-check 불일치)")
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
            accepted.append({
                "ticker": ticker, "name": pos.get("name", ""), "market": market,
                "excd": p.get("excd") or pos.get("excd"),
                "action": "SELL", "qty": qty, "price": price,
                "source": "llm", "proposal": p, "exempt_from_count_limit": False,
            })
            continue

        # BUY — 목표 비중을 수량으로 환산
        target_pct = float(p.get("weight_target_pct") or 0)
        cap = limits.get("per_position_max_pct")
        # ★ 레버리지 ETF는 종목당 상한을 배수로 나눈다 — 3배 ETF의 12%는 기초지수 36% 베팅이다.
        lev, lev_inv = leverage_factor(name, ticker)
        if lev > 1 and cap is not None and limits.get("leveraged_cap_divide_by_factor"):
            cap = float(cap) / lev
            notes.append(f"{ticker} {lev}배 {'인버스 ' if lev_inv else ''}ETF — 종목당 상한 "
                         f"{limits.get('per_position_max_pct')}%→{cap:.1f}% (배수로 나눔)")
        if target_pct <= 0:
            reject(p, "weight_target_pct가 없거나 0 이하")
            continue
        if cap is not None and target_pct > cap:
            if lev > 1:
                # 배수로 나눈 상한은 제안자가 모를 수 있다 — 거부가 아니라 **깎는다**(미달 배수와 같은 방식).
                notes.append(f"{ticker} 목표 {target_pct:.1f}%→{cap:.1f}% (레버리지 상한으로 깎음)")
                target_pct = cap
            else:
                reject(p, f"목표 비중 {target_pct:.1f}% > 종목당 상한 {cap:.1f}%")
                continue

        # 이벤트 임박이면 목표 자체를 절반으로 — 제안자 재량이 아니라 여기서 기계로 자른다.
        # 미달 배수 → 이벤트 계수 → 종목당 상한 순으로 적용한다.
        if alloc_mult > 1.0:
            target_pct *= alloc_mult
        if halve_on:
            target_pct *= event_factor
        if cap is not None and target_pct > cap:
            target_pct = cap        # 배수 때문에 넘긴 것은 거부가 아니라 상한으로 깎는다

        current_val = held.get(ticker, {}).get("eval_amt", 0) or 0
        current_pct = (current_val / equity * 100) if equity else 0
        delta_pct = target_pct - current_pct
        max_delta = rules.get("max_weight_delta_per_day_pct")
        if delta_pct <= 0:
            reject(p, f"이미 목표 비중 이상 보유 (현재 {current_pct:.1f}% ≥ 목표 {target_pct:.1f}%)")
            continue
        if max_delta is not None and delta_pct > max_delta:
            delta_pct = max_delta   # 하루 증분 상한으로 깎아서 진행

        # ★ D8-d 축 집중 상한 — **양 시장 보유를 합쳐** 축별 비중을 재고, 넘는 만큼 깎는다.
        # 거부가 아니라 축소다(미달 배수와 같은 방식). 넘지 않으면 손대지 않는다 —
        # 항상 깎으면 상한이 아니라 그냥 감쇠다.
        # 여력이 0이면 그때만 거부한다(0주를 만들어 사유 없이 사라지게 두지 않는다).
        if axis_cap is not None and equity > 0 and not lev_inv:
            hit = axes_of(ax_idx, ticker)          # 이 종목이 +로 걸린 축만(−로 걸린 축은 집중을 줄인다)
            # 여력은 **기초지수 기준**이다 — 3배 ETF면 증분을 배수로 나눠 받는다. 인버스는 집중을 줄이므로 검사하지 않는다.
            room = min((float(axis_cap) - ax_exp.get(aid, 0.0) / equity * 100) / lev
                       for aid in hit) if hit else None
            if room is not None and room <= 0:
                worst = min(hit, key=lambda a: float(axis_cap) - ax_exp.get(a, 0.0) / equity * 100)
                reject(p, f"축 집중 상한 {axis_cap:.0f}% 도달 — 축 '{worst}' 현재 "
                          f"{ax_exp.get(worst, 0.0) / equity * 100:.1f}% (양 시장 합산)")
                continue
            if room is not None and delta_pct > room:
                notes.append(f"{ticker} 증분 {delta_pct:.1f}%→{room:.1f}%로 깎았다 — "
                             f"축 {'·'.join(hit)} 집중 상한 {axis_cap:.0f}%(양 시장 합산)")
                delta_pct = room

        max_pos = limits.get("max_positions")
        if max_pos is not None and ticker not in held and len(held) >= max_pos:
            reject(p, f"보유 종목 수 상한 {max_pos}개 도달")
            continue

        # 목표 금액을 주가로 나눠 내리면, 1주 값이 목표보다 조금만 커도 **0주**가 되어
        # 제안이 통째로 사라진다. 2026-09-09 실측: 사이징 6가지 경우 중 3가지가 0주였고,
        # 그래서 확신도가 낮은 구간은 아예 진입 자체가 불가능했다.
        # 목표가 1주 값의 일정 비율 이상이면 1주로 올린다 — 한도는 아래 상한들이 계속 지킨다.
        target_amt = equity * delta_pct / 100
        qty = int(target_amt // price)
        round_at = limits.get("round_up_to_one_share_at")
        if qty == 0 and round_at is not None and price > 0 and target_amt >= price * round_at:
            one_share_pct = price / equity * 100
            # 올림 때문에 종목당 상한을 넘어서면 올리지 않는다 — 상한이 우선이다.
            if cap is None or current_pct + one_share_pct <= cap:
                qty = 1
        if qty <= 0:
            reject(p, f"매수 가능 수량 0주 (증분 {delta_pct:.1f}% = {target_amt:,.0f} / "
                      f"주가 {price:,.2f})")
            continue

        for aid, sign in ax_idx.get(ticker, []):
            ax_exp[aid] = ax_exp.get(aid, 0.0) + qty * price * lev * sign
        accepted.append({
            "ticker": ticker, "name": name, "market": market,
            "excd": p.get("excd"),
            "action": "BUY", "qty": qty, "price": price,
            "source": "llm", "proposal": p, "exempt_from_count_limit": False,
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
    today = datetime.now(KST).strftime("%y%m%d")
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


def _armed_tickers(market: str) -> list:
    """이 시장의 `armed` 논지 종목(예약 계산용). 원장이 없으면 빈 목록."""
    store = JOURNAL_DIR / "theses.json"
    if not store.exists():
        return []
    try:
        rows = json.loads(store.read_text(encoding="utf-8")).get("theses") or []
    except (OSError, json.JSONDecodeError):
        return []
    return sorted({t.get("ticker") for t in rows
                   if t.get("status") == "armed" and str(t.get("market", "")).upper() == market.upper()
                   and t.get("ticker")})


def apply_run_limits(orders: list, limits: dict, balance: dict,
                     day_pnl_pct: float, market: str = "KR") -> tuple:
    """금액·건수·현금하한·서킷브레이커. 규율 SELL은 건수 제한 면제."""
    rejected = []
    currency = balance.get("currency", "KRW")
    max_amt = (limits.get("daily_max_order_amount") or {}).get(currency)
    max_orders = limits.get("daily_max_orders", 4)
    halt_pct = limits.get("portfolio_daily_loss_halt_pct", -3.0)
    cash_floor_pct = limits.get("cash_floor_pct", 30.0)

    positions = balance.get("positions", [])
    # ★ 분모는 포트폴리오 합산 자산(이 시장 통화). 집행 가능성만 이 시장 현금이 정한다 —
    # 둘을 한 숫자로 섞으면 '자금이 갈려서 못 산 것'이 '한도에 걸려서 안 산 것'으로 기록된다.
    pf = portfolio_equity(market, balance)
    pfl = pf_local(pf, market, balance)
    equity = pfl["equity"]
    invested = pfl["invested"]
    notes = [pf["why"]]

    # 일일 상한을 **자산 대비 비율**로도 받는다. 사용자가 정하는 유일한 값이 '계좌에 넣는
    # 금액'이므로, 절대 금액으로 두면 입금할 때마다 사람이 다시 정해야 한다. 둘 다 있으면
    # 더 엄한 쪽을 쓴다 — 상한은 보수적인 쪽이 맞다.
    pct_cap = limits.get("daily_max_order_pct_of_equity")
    if pct_cap is not None and equity > 0:
        by_pct = equity * pct_cap / 100
        max_amt = by_pct if max_amt is None else min(max_amt, by_pct)

    # 투자 비중 상한 — 안전장치가 아니라 **시나리오 대응 여력**이다. 전액 투자 상태면
    # "CPI 하회 시 추가"라는 대응이 물리적으로 불가능해져 시나리오를 적어둔 의미가 사라진다.
    max_invested_pct = limits.get("max_invested_pct")

    # 규율 SELL이 항상 먼저. 서킷브레이커에도 걸리지 않는다.
    sells = [o for o in orders if o["action"] == "SELL"]
    buys = [o for o in orders if o["action"] == "BUY"]

    # 예수금 미확인(None)은 0원이 아니다. 0으로 뭉개면 현금하한 검사가 의미를 잃으므로
    # 매수만 전부 막고 매도(규율 포함)는 통과시킨다 — 파는 데는 현금이 필요없다.
    if balance.get("cash") is None:
        for o in buys:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": "예수금 미확인 — 현금 하한을 검사할 수 없어 매수를 보류한다"})
        buys = []
    cash = pfl["cash"]              # 포트폴리오 현금 — 비율(현금 하한) 검사용
    here_cash = balance.get("cash") or 0    # 이 시장 현금 — 실제로 살 수 있는 한도

    # ★ D8-c 반대편 대기 논지를 위한 헤드룸 예약 — **이번 run의 투자 천장에서 뺀다.**
    #
    # 처음에는 '현금 잔액 - 주문액 < 예약'으로 검사했는데 **한 번도 발화할 수 없었다**:
    # 일일 주문 상한(자산의 20%)이 예약선보다 항상 먼저 걸려서, 키에 리더는 있어도
    # 죽은 검사였다(2026-09-11 실측). 예약은 '현금이 바닥날 때의 안전선'이 아니라
    # **'저쪽 몫은 이쪽이 쓸 수 없다'는 배분 규칙**이므로 천장을 낮추는 것이 맞다.
    # max_invested 70% − 예약 15% = 이번 run은 55%까지만 채울 수 있다.
    reserve_pct = 0.0
    reserve, reserve_why, _reserved = other_market_reserve(limits, market, equity)
    if equity > 0 and reserve > 0:
        reserve_pct = reserve / equity * 100
        if max_invested_pct is not None:
            max_invested_pct = max(0.0, float(max_invested_pct) - reserve_pct)
    if reserve_why:
        notes.append(reserve_why
                     + (f" → 이번 run 투자 천장 {limits.get('max_invested_pct')}%"
                        f"→{max_invested_pct:.1f}%" if reserve_pct else ""))

    if halt_pct is not None and day_pnl_pct is not None and day_pnl_pct <= halt_pct:
        for o in buys:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": f"서킷브레이커: 당일 {day_pnl_pct:+.2f}% ≤ {halt_pct:+.2f}%, 신규 매수 중지"})
        buys = []

    final = list(sells)
    # ★ 오늘 이미 나간 주문을 상한에서 먼저 뺀다.
    # 예전엔 spent가 매 호출 0에서 시작해, 재시도·보충 슬롯으로 하루 두 번 돌면
    # 일일 상한이 두 배로 적용됐다(2026-09-08). 상한은 '실행당'이 아니라 '하루당'이다.
    spent, counted = spent_today(market, currency)
    counted += sum(1 for o in sells if not o.get("exempt_from_count_limit"))

    # ★ 같은 시장 대기 논지 예약(덜 사기) — 이 시장에 *다른* armed 논지가 있으면 이 시장 자산의
    #   `scenario_reserve_pct_of_market`만큼 현금을 남기고 깎는다. 2026-09-16 국내 run이 두산 98주로
    #   국내 현금을 49,582원까지 써서 시나리오 대응(삼성생명 e1 등)이 전부 막혔다.
    here_equity = (balance.get("cash") or 0) + sum(p.get("eval_amt", 0) or 0 for p in positions)
    armed_here = _armed_tickers(market)
    res_pct = limits.get("scenario_reserve_pct_of_market")
    funding_left = int(limits.get("funding_max_per_run") if limits.get("funding_max_per_run") is not None else 1)
    fill_ratio = float(limits.get("funding_min_fill_ratio") if limits.get("funding_min_fill_ratio") is not None else 0.5)
    selling = {o["ticker"] for o in sells}

    for o in buys:
        amt = o["qty"] * o["price"]
        others = [t for t in armed_here if t != o["ticker"]]
        reserve_local = (here_equity * float(res_pct) / 100) if (res_pct is not None and others) else 0.0
        if max_amt is not None and spent + amt > max_amt:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": f"일일 주문금액 상한 초과 (누적 {spent + amt:,.0f} > {max_amt:,.0f} {currency})"})
            continue
        if max_orders is not None and counted + 1 > max_orders:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": f"일일 주문 건수 상한 {max_orders}건 초과"})
            continue
        if cash_floor_pct is not None and equity > 0 and (cash - amt) / equity * 100 < cash_floor_pct:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": f"현금 하한 {cash_floor_pct:.0f}% 침범 (매수 후 {(cash - amt) / equity * 100:.1f}%)"})
            continue
        # 예약 침범 — 천장(위)이 1차 방어선이고 이건 현금이 진짜 마를 때의 2차선이다.
        if reserve > 0 and cash - amt < reserve:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": f"반대편 예약 침범 — 매수 후 현금 {cash - amt:,.0f} < "
                                    f"예약 {reserve:,.0f} ({reserve_why})"})
            continue
        if max_invested_pct is not None and equity > 0 and \
                (invested + amt) / equity * 100 > max_invested_pct:
            rejected.append({"ticker": o["ticker"], "action": "BUY",
                             "why": f"투자 비중 상한 {max_invested_pct:.1f}% 초과 "
                                    f"(매수 후 {(invested + amt) / equity * 100:.1f}%) — "
                                    + (f"반대편 예약 {reserve_pct:.1f}%p를 뺀 천장이다 "
                                       f"({reserve_why})" if reserve_pct else
                                       "시나리오 대응 여력을 남긴다")})
            continue
        # ★ D8-b 목표는 포트폴리오, 집행은 그 시장 현금. 여기까지 통과한 것은
        # **비중 판단으로는 맞는 주문**이고, 그 시장 현금이 없어 못 사는 것뿐이다.
        # 2026-09-11엔 그대로 거부했다("자금을 어디로 옮길지의 신호") — 그런데 모의 기간엔
        # 계좌를 합칠 수 없어 그 신호가 갈 곳이 없고, 국내 매수가 두 run 연속 0이 됐다
        # (9/14·9/15 삼성생명: 주문 1,100만 > 국내 현금 830만). **현금에 맞춰 깎아서 산다** —
        # 사유는 그대로 남기고, 1주도 못 살 때만 거부한다(2026-09-16).
        spendable = max(0.0, here_cash - reserve_local)
        if amt > spendable:
            intended = amt
            shaved_qty = int(spendable // o["price"]) if o["price"] > 0 else 0
            # ★ 회전 매도(팔기) — 깎아도 의도의 절반이 안 되면 이 시장에서 가장 큰 보유를 부족액만큼
            #   판다. 사람에게 환전·입금을 묻지 않는다(2026-09-16 사용자: "입금한 돈 안에서 알아서").
            if shaved_qty * o["price"] < intended * fill_ratio and funding_left > 0:
                need = intended - spendable
                cands = sorted((p for p in positions
                                if p.get("ticker") not in selling and p.get("ticker") != o["ticker"]
                                and int(p.get("qty") or 0) > 0 and (p.get("price") or 0) > 0),
                               key=lambda p: -(p.get("eval_amt") or 0))
                if cands:
                    src = cands[0]
                    sp = float(src["price"])
                    if market == "KR":
                        sp = float(snap_kr_price(sp, "SELL"))
                    sq = min(int(src["qty"]), int(-(-need // sp)))
                    proceeds = sq * sp
                    why_f = (f"회전 매도 — {o['ticker']} 매수 자금 {need:,.0f} {currency} 부족 → "
                             f"이 시장 최대 보유 {src.get('ticker')} {sq}/{src['qty']}주 @{sp:,.0f} "
                             f"({proceeds:,.0f}) 매도로 메운다 — 가진 돈 안에서 해결한다")
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
                    notes.append(why_f)
                    o = {**o, "funding": {"sold": src["ticker"], "qty": sq, "proceeds": proceeds},
                         "reasons": list(o.get("reasons") or []) + [why_f]}
                    shaved_qty = min(int(o["qty"]), int(spendable // o["price"]))
            if shaved_qty <= 0:
                rejected.append({"ticker": o["ticker"], "action": "BUY",
                                 "why": f"자금 배분 — {market} 자산으로는 1주({o['price']:,.0f})도 못 산다 "
                                        f"(쓸 수 있는 현금 {spendable:,.0f} {currency}"
                                        + (f" · 대기 논지 예약 {reserve_local:,.0f}" if reserve_local else "")
                                        + f" · 주문 {intended:,.0f}). 회전 매도 대상도 없다"})
                continue
            if shaved_qty < int(o["qty"]):
                why = (f"자금 배분 — {market} 쓸 수 있는 현금 {spendable:,.0f} {currency}"
                       + (f"(대기 논지 {len(others)}건 몫 {reserve_local:,.0f} 예약)" if reserve_local else "")
                       + f"에 맞춰 {o['qty']}→{shaved_qty}주로 깎았다 (주문 {intended:,.0f} · "
                       f"포트폴리오 기준 비중 판단은 유지 · 이 시장 자산이 상한)")
                notes.append(f"{o['ticker']} {why}")
                o = {**o, "qty": shaved_qty,
                     "shaved": {"from_qty": o["qty"], "to_qty": shaved_qty, "why": why},
                     "reasons": list(o.get("reasons") or []) + [why]}
            amt = int(o["qty"]) * o["price"]
        invested += amt
        spent += amt
        cash -= amt
        here_cash -= amt
        counted += 1
        final.append(o)

    for n in notes:
        print(f"  · {n}")
    return final, rejected


# ------------------------------------------------------------- 미달 강제(deficit)

# 제안이 **애초에 성립하지 않는** 거부 사유 — 이런 제안은 미달 건수에 세지 않는다.
# (한도·현금·축 상한 거부는 가드의 몫이라 센다 — 제안자는 할 일을 한 것이다.)
VALIDITY_REJECTS = ("유니버스 밖", "MISMATCH", "weight_target_pct", "현재가를 알 수 없다",
                    "금지 패턴", "confidence", "MATCH(T1/T2)", "미보유 종목 SELL")


def deficit_check(sig: dict, limits: dict, market: str, invested_pct: float,
                  rejected: list, today: str) -> dict:
    """투자비중이 목표 아래일 때 **이 run이 제안 의무를 다했는가**를 기계로 센다.

    왜: '미달이면 설명 의무 → 연속 N회면 강제 제안' 규칙이 무인 경로(`run_auto.py`)에만 있어
    수동·루틴 run에서는 한 번도 작동하지 않았다. 9/8~9/15 투자비중 5%·미국 집행 6회 중 1회·
    run당 제안 1건이 그 결과다. 이 판정을 approved에 `deficit_short`로 박아 **dispatch·no_trade
    게이트가 파일에서 읽게** 한다 — 각서가 아니라 숫자다(2026-09-16 사용자 결정: 최소 건수 강제).

    요구 셋(전부 `limits.json`): ① BUY 제안 ≥ `min_proposals_below_target[market]`
    ② 서로 다른 축 ≥ `min_distinct_axes_below_target` ③ 오늘 새로 `armed`된 이 시장 논지마다
    BUY 제안이 있다(= e0 선진입 강제 — 미달 상태에서는 e0를 생략할 수 없다).
    """
    tgt = limits.get("target_invested_pct")
    need_map = limits.get("min_proposals_below_target") or {}
    need = need_map.get(market.upper()) if isinstance(need_map, dict) else None
    need_axes = limits.get("min_distinct_axes_below_target")
    out = {"invested_pct": round(invested_pct, 2), "target": tgt, "required": need,
           "required_axes": need_axes, "counted": 0, "distinct_axes": 0, "tickers": [],
           "e0_missing": [], "short": False, "why": ""}
    if tgt is None or need is None:
        out["why"] = "검사 꺼짐(limits에 target_invested_pct 또는 min_proposals_below_target 없음)"
        return out
    if invested_pct >= float(tgt):
        out["why"] = f"투자비중 {invested_pct:.1f}% ≥ 목표 {float(tgt):.0f}% — 미달 아님"
        return out

    bad = {r.get("ticker") for r in rejected
           if any(k in str(r.get("why", "")) for k in VALIDITY_REJECTS)}
    counted = []
    for p in (sig.get("proposals") or []):
        if p.get("action") == "BUY" and p.get("ticker") and p["ticker"] not in bad:
            counted.append(p["ticker"])
    # 서로 다른 축 — 종목마다 아직 안 쓴 축 하나를 배정해 센다. 한 종목이 두 축에 걸려 있다고
    # 그 종목 하나로 축 둘을 채운 것으로 세지 않는다(분산은 종목 수로 실현된다).
    idx = axis_index()
    axes = set()
    for t in counted:
        opts = axes_of(idx, t) or [f"ticker:{t}"]       # 축이 없는 종목은 자기 자신이 한 축
        free = next((a for a in opts if a not in axes), None)
        if free:
            axes.add(free)
    out["counted"], out["distinct_axes"], out["tickers"] = len(counted), len(axes), counted

    # ③ 오늘 세운 논지에 e0가 있는가 — armed인데 제안이 없으면 되돌림만 기다리는 것이다.
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
    if len(counted) < int(need):
        reasons.append(f"BUY 제안 {len(counted)}건 < 요구 {int(need)}건")
    if need_axes is not None and len(axes) < int(need_axes):
        reasons.append(f"서로 다른 축 {len(axes)}개 < 요구 {int(need_axes)}개")
    if out["e0_missing"]:
        reasons.append(f"오늘 세운 논지에 e0 제안 없음: {', '.join(out['e0_missing'])}")
    out["short"] = bool(reasons)
    out["why"] = (f"투자비중 {invested_pct:.1f}% < 목표 {float(tgt):.0f}% — "
                  + ("; ".join(reasons) if reasons else
                     f"제안 {len(counted)}건·축 {len(axes)}개·e0 충족"))
    return out


# ---------------------------------------------------------------------- main

def run(signal_path: Path, snapshot_path: Path, out_path: Path,
        discipline_only: bool = False, live: bool = False) -> int:
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
    rejected = []

    if not discipline_only:
        sig = _read_json(signal_path, "signal")
        validate_signal(sig, limits, now)          # 실패 시 Rejection → run 전체 거부
        market = sig["market"]
        if sig.get("no_trade"):
            print("시그널 no_trade=true — 판단 보류. 규율 주문만 처리한다.")
        else:
            acc, rej = screen_proposals(sig, limits, watchlist, balance, prices)
            # 규율 SELL과 겹치는 종목은 규율 쪽을 남긴다(더 보수적).
            disc_tickers = {o["ticker"] for o in orders}
            orders += [o for o in acc if o["ticker"] not in disc_tickers]
            rejected += rej

    final, run_rej = apply_run_limits(orders, limits, balance, day_pnl_pct, market)
    rejected += run_rej

    # ★ 미달 강제 — 목표 아래인데 제안이 모자라면 `deficit_short: true`. 주문은 그대로 승인한다
    #   (막는 것은 게이트다 — 이 run은 4·5단으로 돌아가 제안을 채우고 다시 돈다).
    deficit = {"short": False, "why": "규율 전용 run — 검사 없음"}
    if not discipline_only:
        pfl = pf_local(portfolio_equity(market, balance), market, balance)
        deficit = deficit_check(sig, limits, market, pfl["invested_pct"], rejected,
                                now.strftime("%Y-%m-%d"))

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
        "deficit": deficit,
        "deficit_short": bool(deficit.get("short")),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"승인 {len(final)}건 / 거부 {len(rejected)}건 → {out_path}")
    for o in final:
        src = "규율" if o["source"] == "discipline" else "제안"
        print(f"  [{src}] {o['action']:4} {o['ticker']} {o.get('name', '')} "
              f"x{o['qty']} @ {o['price']:,.2f}")
        for r in o.get("reasons", []):
            print(f"         └ {r}")
    for r in rejected:
        print(f"  [거부] {r['action']:4} {r['ticker']}: {r['why']}")
    if deficit.get("short"):
        print(f"\n★ 미달 강제 — deficit_short=true: {deficit['why']}. "
              f"dispatch/no_trade 게이트가 이 파일로 막는다 — 4·5단으로 돌아가 제안을 채우고 "
              f"risk_guard를 다시 돌려라(거부를 뒤집는 재실행이 아니라 부족을 채우는 재실행이다).",
              file=sys.stderr)
    elif deficit.get("why"):
        print(f"  · 미달 검사: {deficit['why']}")
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
    args = ap.parse_args()

    if not args.discipline_only and not args.signal:
        ap.error("signal 경로가 필요하다 (또는 --discipline-only)")

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
        return run(signal_path, snapshot_path, out_path, args.discipline_only, live=args.live)
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
