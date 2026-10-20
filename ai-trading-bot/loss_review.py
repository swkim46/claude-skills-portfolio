#!/usr/bin/env python3
"""
손실·시나리오 오판 차선 — 토요일 `trade-feedback` 수집이 놓친 급등과 나란히 센다(FB-261012-9).

    python3 loss_review.py --week 261012 [--days 20]      # 표만 찍는다(수집은 feedback.py collect가 부른다)

왜 있나 (2026-10-12 사용자): "trade-feedback에 손실 혹은 시나리오 오판단에 대한 피드백도 같이 할 수 있는 기능이 있으면
좋겠어. 다만 시나리오 오판단에 대한 기준은 너무 단기간만 보고 빡빡하게 하면 안 될 것 같아 — 지금 현 상황이 일시적으로
안 좋은 건지 뭔가 제대로 잘못 예상한 건지에 대한 판단을 하고 그 부분에 대한 피드백을 같이 해서 trade-run을 개선하는 식으로."
그 전엔 손실 분석을 사람이 시켜야 나왔고, 수기 분석은 손실 매매만 더해 순실현의 3.5배를 '손실'이라 불렀다.

★ **가격 하락만으로 '오판'을 주지 않는다.** 시계는 논지가 등록될 때 스스로 정한 것뿐이다:
  ⓐ 무효 조건(판정 다리 — 훼손·무효·반증)이 충족돼 집행됐다
  ⓑ 기한(horizon_days)이 지났는데 손실이다
  ⓒ 축 전망이 기한을 넘겨 `miss`로 판정됐다
  ⓓ 손절·추적 다리가 손실로 발화했다 — 논지가 말한 동인(축 연쇄)이 멀쩡하면 '동인 불일치', 꺾였으면 '방향'
  넷 다 아니면 **일시적**(가격만 빠졌다), 축 연쇄에 반증이 섞이는 등 근거가 엇갈리면 **관찰** — 둘 다 다음 주로 이월한다.
이 모듈은 **판정 입력과 제안 판정**을 낸다. 최종 판정·오판 유형·진입 전 단서 인용은 피드백 보고서(§2-b)가 쓴다.
새 상수를 만들지 않는다 — 약세 문턱은 그 논지의 추적 다리 폭(등록 때 그 종목 흔들림으로 정한 값)의 절반이다.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db  # noqa: E402 — 논지·축·포지션 고점·손실 판정은 저장소에서

KST = timezone(timedelta(hours=9))
OUTDIR = HERE / "data" / "feedback"
INVALIDATION_WORDS = re.compile(r"훼손|무효|반증|invalidat|thesis broken", re.I)
TRAIL = re.compile(r"from_peak_pct\s*<=?\s*-?\s*([\d.]+)")
FILLED = ("FILLED", "PARTIAL")
VERDICTS = ("오판", "일시적", "관찰", "이익")
MISJUDGE_TYPES = ("방향", "동인", "시점", "가지 누락", "집중", "크기")


def _j(p, default=None):
    """run 파일(거래 기록·스냅샷·손실 판정 파일) 읽기."""
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _d(stamp: str) -> date:
    return datetime.strptime(stamp, "%y%m%d").date()


def _iso_date(s) -> date | None:
    try:
        return datetime.fromisoformat(str(s)[:19]).date() if "T" in str(s) else datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ───────────────────────────────────────────────────────── 체결 → 실현 손익(FIFO)

def _trades_same(f) -> list:
    """거래 기록의 trades — 지금 계좌 계열의 파일만(2026-10-15 · 계좌 전환에 대비해 모의 거래를 실계좌 지표로 세지 않게 · 표시 없는 옛 파일은 모의)."""
    import portfolio
    from kis_client import acct_family
    d = _j(f, {}) or {}
    return (d.get("trades") or []) if portfolio.file_family(d) == acct_family() else []   # account_mode → svr → runs[](2026-10-19 R07)


def _lot_price_from_snaps(market: str, stamp: str, ticker: str, qty: float) -> float:
    """체결가 없이 잔고 증감으로 확정된 매수의 실제 단가 — 그 run 캡처(매수 전)와 다음 캡처(매수 뒤)의 수량·평단으로
    (뒤 수량 × 뒤 평단 − 앞 수량 × 앞 평단) ÷ 늘어난 수량. 수량 증가가 이 체결과 다르거나 스냅샷이 없으면 0(부르는 쪽이 주문가로).
    국내 체결은 잔고 증감으로 확정되는 일이 많아 주문 지정가로 손익을 쟀다(2026-10-17 피드백 FB-261017-1)."""
    mk = market.lower()
    try:
        before = db.q1("SELECT qty, avg_price FROM ex_position WHERE market=? AND ticker=? AND phase='capture' AND run_id=?",
                       (mk, ticker, f"{stamp}_{mk}"))
        after = db.q1("SELECT qty, avg_price FROM ex_position WHERE market=? AND ticker=? AND phase='capture' AND run_id>?"
                      " AND run_id LIKE ? ORDER BY run_id LIMIT 1", (mk, ticker, f"{stamp}_{mk}", f"%_{mk}"))
    except Exception:                                # noqa: BLE001 — 조회 실패는 주문가로 돌아간다
        return 0.0
    if not after:
        return 0.0
    qb, ab = (float(before["qty"] or 0), float(before["avg_price"] or 0)) if before else (0.0, 0.0)
    qa, aa = float(after["qty"] or 0), float(after["avg_price"] or 0)
    if qa - qb <= 0 or abs((qa - qb) - qty) > 1e-9 or aa <= 0:
        return 0.0
    return round((qa * aa - qb * ab) / (qa - qb), 4)


def fills(market: str) -> list:
    """그 시장 체결을 시간순으로 — `fill`(체결 확정)이 있으면 그것, 없으면(초기 기록) 주문 수량·가격.
    반환 [{date, ticker, name, side, qty, price, thesis_id}]. 취소·만료·미체결은 뺀다."""
    out = []
    for f in sorted(glob.glob(str(HERE / "journal" / f"trades_*_{market.lower()}.json"))):
        m = re.search(r"trades_(\d{6})_", f)
        if not m:
            continue
        for t in _trades_same(f):
            fl = t.get("fill") or {}
            if fl:
                if fl.get("verdict") not in FILLED:
                    continue
                # 매도 확정은 `filled_qty` 없이 잔고 증감(`delta`)만 남기는 경우가 있다 — 그때는 증감 절댓값 → 주문 수량 순.
                q = float(fl.get("filled_qty") or abs(float(fl.get("delta") or 0)) or t.get("qty") or 0)
                p = float(fl.get("avg_price") or 0)
                if not p and str(t.get("action") or "").upper() == "BUY":
                    p = _lot_price_from_snaps(market, m.group(1), str(t.get("ticker")), q)   # 잔고 증감으로 확정된 국내 매수
                p = p or float(t.get("price") or fl.get("limit_price") or 0)
            else:
                if str(t.get("status") or "").upper() not in ("SENT", "FILLED", "PARTIAL"):
                    continue
                q, p = float(t.get("qty") or 0), float(t.get("price") or 0)
            if q <= 0 or p <= 0:
                continue
            out.append({"date": _d(m.group(1)), "ts": str(t.get("ts") or ""), "ticker": str(t.get("ticker")),
                        "name": t.get("name") or "", "side": str(t.get("action") or "").upper(), "qty": q, "price": p,
                        "thesis_id": t.get("thesis_id") or ""})
    return sorted(out, key=lambda x: (x["date"], x["ts"]))


def realized_events(market: str) -> list:
    """매도마다 FIFO 실현 손익 — [{date, ticker, qty, pnl, cost, thesis_id}]."""
    lots, ev = defaultdict(deque), []
    for x in fills(market):
        if x["side"] == "BUY":
            lots[x["ticker"]].append([x["qty"], x["price"]])
            continue
        rem, pnl, cost = x["qty"], 0.0, 0.0
        while rem > 0 and lots[x["ticker"]]:
            lq, lp = lots[x["ticker"]][0]
            u = min(rem, lq)
            pnl += u * (x["price"] - lp)
            cost += u * lp
            lots[x["ticker"]][0][0] -= u
            rem -= u
            if lots[x["ticker"]][0][0] <= 0:
                lots[x["ticker"]].popleft()
        ev.append({"date": x["date"], "ticker": x["ticker"], "qty": x["qty"] - rem, "pnl": pnl, "cost": cost,
                   "thesis_id": x["thesis_id"]})
    return ev


# ───────────────────────────────────────────────────────── 논지·축·스냅샷

def _closed_on(t: dict) -> date | None:
    for h in reversed(t.get("history") or []):
        if h.get("status") in ("closed", "expired", "invalidated"):
            return _iso_date(h.get("ts") or h.get("at"))
    return None


def _entered(t: dict) -> date | None:
    return _iso_date(t.get("entered_on")) or _iso_date(t.get("created"))


def _axes() -> dict:
    return {a.get("id"): a for a in db.market_map_load().get("axes") or [] if a.get("id")}


def driver_state(ax: dict) -> str:
    """논지가 말한 동인(축 연쇄)의 상태 — `intact`(반증 없음·확인 있음) · `broken`(반증 있음) · `unknown`."""
    st = [str(c.get("status") or "") for c in (ax or {}).get("chain") or []]
    if not st:
        return "unknown"
    if "반증" in st:
        return "broken"
    return "intact" if "확인" in st else "unknown"


def _latest_positions() -> dict:
    """{(MARKET, ticker): position} — 각 시장 최신 스냅샷(생성 시각 기준)."""
    out = {}
    for mk in ("kr", "us"):
        best = None
        for f in glob.glob(str(HERE / "data" / f"snapshot_*_{mk}.json")):
            if not re.search(r"snapshot_\d{6}_[a-z]{2}\.json$", f):
                continue
            d = _j(f, {}) or {}
            from kis_client import same_account
            if not same_account(d):                      # 지금 계좌 계열의 스냅샷만(2026-10-15)
                continue
            if best is None or str(d.get("generated_at") or "") > str(best.get("generated_at") or ""):
                best = d
        for p in ((best or {}).get("balance") or {}).get("positions") or []:
            out[(mk.upper(), str(p.get("ticker")))] = p
    return out


def _peaks() -> dict:
    """{_schema, KR: {ticker: {peak_price, peak_at}}, US: …} — ex_position_peak."""
    return db.peaks_all()


# ───────────────────────────────────────────────────────── 후보와 판정 입력

def candidates(week: str, days: int = 20) -> list:
    """손실·약세 후보. ① 창 안에 닫힌 논지 전부(이익도 — 손실만 더하지 않는다) ② 보유 중 고점 대비 되돌림이 추적 다리 폭의
    절반을 넘은 것 ③ 보유 중 추적 다리가 없는 것. 행마다 판정 입력(울린 시계)과 제안 판정을 붙인다."""
    wk = _d(week)
    lo = wk - timedelta(days=int(days * 7 / 5) + 2)
    th = db.theses_all()
    axes = _axes()
    pos = _latest_positions()
    peaks = _peaks()
    ev = {mk: realized_events(mk) for mk in ("kr", "us")}
    # ★ 매도 한 건은 논지 하나에만 귀속한다 — 꼬리표(thesis_id)가 있으면 그 논지, 없으면 그 날짜를 창(진입~닫힘)에 품은
    #   같은 종목 논지 중 가장 먼저 진입한 것. 사다리 논지처럼 창이 겹치면 같은 매도를 두 번 세게 된다.
    owner = {}
    for mk in ("kr", "us"):
        for i, e in enumerate(ev[mk]):
            if e["thesis_id"]:
                owner[(mk, i)] = e["thesis_id"]
                continue
            cands = sorted((t for t in th if str(t.get("market") or "").lower() == mk and str(t.get("ticker")) == e["ticker"]
                            and _entered(t) and _entered(t) <= e["date"] <= (_closed_on(t) or wk)),
                           key=lambda t: _entered(t))
            if cands:
                owner[(mk, i)] = cands[0]["id"]
    rows = []
    for t in th:
        mk = str(t.get("market") or "").upper()
        if mk not in ("KR", "US"):
            continue
        st = t.get("status")
        ent, closed = _entered(t), _closed_on(t)
        legs = t.get("exit_triggers") or []
        trail = next((float(TRAIL.search(str(g.get("check"))).group(1)) for g in legs if TRAIL.search(str(g.get("check") or ""))), None)
        row = None
        if st in ("closed", "expired", "invalidated") and closed and lo <= closed <= wk:
            evs = [e for i, e in enumerate(ev[mk.lower()]) if owner.get((mk.lower(), i)) == t["id"]]
            pnl, cost = sum(e["pnl"] for e in evs), sum(e["cost"] for e in evs)
            if not evs or cost <= 0:
                continue                                     # 체결 없이 닫힌 논지(사다리·대기만 하다 닫힘)는 손익이 없다
            row = {"kind": "closed", "pnl": pnl, "pnl_pct": pnl / cost * 100, "from_peak_pct": None}
        elif st == "held":
            p = pos.get((mk, str(t.get("ticker")))) or {}
            pk = ((peaks.get(mk) or {}).get(str(t.get("ticker"))) or {}).get("peak_price")
            price = float(p.get("price") or 0)
            fp = (price / float(pk) - 1) * 100 if pk and price else None
            weak = trail is None or (fp is not None and fp <= -trail / 2)
            if weak:
                row = {"kind": "held_no_trail" if trail is None else "held_weak",
                       "pnl": float(p.get("eval_amt") or 0) * float(p.get("pnl_pct") or 0) / (100 + float(p.get("pnl_pct") or 0))
                              if p else None,
                       "pnl_pct": p.get("pnl_pct"), "from_peak_pct": fp}
        if row is None:
            continue
        ax = axes.get(t.get("axis_id")) or {}
        pdax = axes.get(t.get("price_driver")) if t.get("price_driver") else None
        inval = any(g.get("fired") and str(g.get("check") or "") in ("", "judge")
                    and (g.get("id") == "x2" or INVALIDATION_WORDS.search(str(g.get("when") or ""))) for g in legs)
        price_leg = next((g for g in legs if g.get("fired") and str(g.get("check") or "") not in ("", "judge")
                          and not str(g.get("check")).startswith("days_since_entry")), None)
        hz = int(t.get("horizon_days") or 0)
        elapsed = None
        if ent and hz:
            end = closed or wk
            wd = sum(1 for i in range((end - ent).days) if (ent + timedelta(days=i + 1)).weekday() < 5)
            elapsed = round(100 * wd / hz)
        fc_status = ax.get("status") if ax.get("status") in ("hit", "miss", "expired") else None
        fc_due = None
        if ax.get("direction") and ax.get("horizon_weeks") and _iso_date(ax.get("opened")):
            fc_due = _iso_date(ax.get("opened")) + timedelta(weeks=int(ax["horizon_weeks"]))
        drv = driver_state(ax)
        loss = (row.get("pnl") or 0) < 0
        clocks = []
        if inval:
            clocks.append("ⓐ무효 조건 집행")
        if loss and elapsed is not None and elapsed >= 100:
            clocks.append("ⓑ기한 경과·손실")
        if fc_status == "miss":
            clocks.append("ⓒ전망 miss")
        if price_leg is not None and loss:
            clocks.append(f"ⓓ{price_leg.get('id')} 발화·동인 {'멀쩡' if drv == 'intact' else '꺾임' if drv == 'broken' else '미상'}")
        if row["kind"] == "closed" and not loss:
            verdict, mtype = "이익", ""
        elif clocks:
            if drv == "unknown" and not inval and fc_status != "miss" and not (loss and elapsed and elapsed >= 100):
                verdict, mtype = "관찰", ""
            else:
                verdict = "오판"
                mtype = ("동인" if (price_leg is not None and drv == "intact") else
                         "시점" if any(c.startswith("ⓑ") for c in clocks) else "방향")
        elif drv == "broken":
            verdict, mtype = "관찰", ""
        else:
            verdict, mtype = "일시적", ""
        rows.append({**row, "market": mk, "ticker": str(t.get("ticker")), "name": t.get("name") or "", "thesis_id": t["id"],
                     "axis_id": t.get("axis_id") or "", "price_driver": t.get("price_driver") or "",
                     "price_driver_differs": bool(t.get("price_driver") and t.get("price_driver") != t.get("axis_id")),
                     "entered": str(ent or ""), "closed": str(closed or ""), "horizon_elapsed_pct": elapsed,
                     "trail_width": trail, "invalidation_met": inval, "leg_fired": (price_leg or {}).get("id"),
                     "driver_state": drv, "price_driver_state": driver_state(pdax) if pdax else "",
                     "forecast_status": fc_status, "forecast_due": str(fc_due or ""), "clocks": clocks,
                     "suggested": verdict, "suggested_type": mtype})
    order = {"closed": 0, "held_weak": 1, "held_no_trail": 2}
    return sorted(rows, key=lambda r: (order[r["kind"]], r.get("pnl") or 0))


def forecasts_due(week: str) -> list:
    """기한이 도래한 열린 전망 — `opened + horizon_weeks`가 지났는데 아직 hit/miss로 판정되지 않은 것."""
    wk = _d(week)
    out = []
    for aid, ax in _axes().items():
        if not ax.get("direction") or not ax.get("horizon_weeks") or not _iso_date(ax.get("opened")):
            continue
        due = _iso_date(ax.get("opened")) + timedelta(weeks=int(ax["horizon_weeks"]))
        if due <= wk and (ax.get("status") or "open") == "open":     # 판정된 것·기각은 빼고 미판정만(2026-10-19 · Codex U06 — 설명문과 맞춤)
            out.append({"axis_id": aid, "direction": ax.get("direction"), "due": str(due), "status": ax.get("status") or "open",
                        "confidence": ax.get("confidence"), "expected": str(ax.get("expected") or "")[:160]})
    return sorted(out, key=lambda r: r["due"])


def stale_scenarios() -> list:
    try:
        import scenarios as sc
        return [{"id": r["id"], "market": r.get("market"), "why": r.get("_why"), "condition": str(r.get("condition") or "")[:110]}
                for r in sc.stale()]
    except Exception as e:                                   # noqa: BLE001
        return [{"id": "?", "market": "", "why": f"조회 실패 — {type(e).__name__}: {e}", "condition": ""}]


def sync_verdicts() -> int:
    """손실 판정 파일(`data/feedback/손실판정_<week>.json` — 토요일 보고서 §2-b가 쓴다)의 행을 저장소(op_loss_review)에 옮긴다.
    멱등(키는 (주, 논지) — 같은 주 한 종목에 논지가 둘이면 행도 둘). 반환 옮긴 행 수.
    판정 읽기(보정표·오판 비율)는 표에서 한다 — 읽기 전에 이 함수가 파일을 먼저 맞춘다."""
    n = 0
    for f in sorted(OUTDIR.glob("손실판정_*.json")):
        m = re.search(r"_(\d{6})\.json$", f.name)
        d = _j(f, None) if m else None
        if not d:
            continue
        rows = [r for r in d.get("rows") or [] if isinstance(r, dict) and r.get("ticker")]
        db.loss_review_replace(str(d.get("week") or m.group(1)), rows)   # 그 주는 파일이 원본 — 파일에서 지운 행은 표에서도 지운다
        n += len(rows)
    return n


def _verdict_week(week: str, inclusive: bool) -> str | None:
    """저장소에 판정이 있는 주 가운데 `week` 이전(inclusive면 그 주 포함) 가장 최근 주."""
    sync_verdicts()
    weeks = sorted({str(r.get("week")) for r in db.q("SELECT DISTINCT week FROM op_loss_review")})
    weeks = [w for w in weeks if (w <= week if inclusive else w < week)]
    return weeks[-1] if weeks else None


def verdict_flips(week: str, rows: list) -> dict:
    """지난주 손실 판정이 이번 주에 뒤집혔나 — 보정표. 일시적 → 다리 발화(손실로 닫힘)면 너무 느슨,
    오판 → 아직 보유 중이고 고점 재돌파(고점 대비 ≥ 0)면 너무 빡빡."""
    pw = _verdict_week(week, inclusive=False)
    if not pw:
        return {"file": "", "judged": 0, "loose": [], "strict": [], "rate": None}
    # 판정 때 **보유 중이던** 행만 — 이미 닫힌 손실을 '일시적'으로 적은 행은 다음 주에 뒤집힐 수가 없다.
    prev = [r for r in db.loss_review_rows(pw) if r.get("verdict") in ("오판", "일시적", "관찰")
            and str(r.get("kind") or "").startswith("held")]
    now = {r["thesis_id"]: r for r in rows}
    th = {t["id"]: t for t in db.theses_all()}
    loose, strict = [], []
    for r in prev:
        cur, t = now.get(r.get("thesis_id")), th.get(r.get("thesis_id")) or {}
        if r["verdict"] == "일시적" and t.get("status") in ("closed", "expired") and cur and (cur.get("pnl") or 0) < 0:
            loose.append(r["thesis_id"])
        if r["verdict"] == "오판" and t.get("status") == "held" and cur and (cur.get("from_peak_pct") or -1) >= 0:
            strict.append(r["thesis_id"])
    n = len(prev)
    return {"file": f"손실판정_{pw}.json", "judged": n, "loose": loose, "strict": strict,
            "rate": round(100 * (len(loose) + len(strict)) / n, 1) if n else None}


def misjudged_rate(week: str) -> float | None:
    """가장 최근 손실 판정(이번 주 것 포함 · op_loss_review)에서 오판 비율 — 오판 / (오판+일시적+관찰)."""
    w = _verdict_week(week, inclusive=True)
    rows = db.loss_review_rows(w) if w else []
    j = [r for r in rows if r.get("verdict") in ("오판", "일시적", "관찰")]
    return round(100 * sum(r["verdict"] == "오판" for r in j) / len(j), 1) if j else None


def collect(week: str, days: int = 20) -> dict:
    sync_verdicts()
    rows = candidates(week, days)
    net = {mk: sum(r["pnl"] for r in rows if r["market"] == mk and r["kind"] == "closed") for mk in ("KR", "US")}
    return {"rows": rows, "net_realized": net, "forecasts_due": forecasts_due(week),
            "stale_scenarios": stale_scenarios(), "flips": verdict_flips(week, rows)}


def _money(v, mk):
    if v is None:
        return "—"
    return f"{v:+,.0f}원" if mk == "KR" else f"{v:+,.2f}$"


def render(res: dict) -> str:
    rows = res["rows"]
    n = {k: sum(r["kind"] == k for r in rows) for k in ("closed", "held_weak", "held_no_trail")}
    sug = {v: sum(r["suggested"] == v for r in rows) for v in VERDICTS}
    L = [f"## 손실·약세 후보 — 닫힌 논지 {n['closed']} · 보유 약세 {n['held_weak']} · 추적 다리 없음 {n['held_no_trail']}", "",
         f"> 창 안 **닫힌 논지의 순실현** KR {_money(res['net_realized']['KR'], 'KR')} · US {_money(res['net_realized']['US'], 'US')} "
         f"(이익 난 논지도 센다 — 손실만 더하지 않는다). 제안 판정 오판 {sug['오판']} · 일시적 {sug['일시적']} · 관찰 {sug['관찰']} · "
         f"이익 {sug['이익']}. **가격 하락만으로 오판을 주지 않는다** — '울린 시계'가 논지가 등록 때 정한 것(ⓐ무효 조건 ⓑ기한 "
         f"ⓒ전망 miss ⓓ손절·추적 다리+동인)이 아니면 일시적이다. 최종 판정·유형·진입 전 단서는 보고서 §2-b가 쓴다.", "",
         "| 시장 | 종목 | 논지 | 축(동인) | 진입 | 기한 경과 | 손익 | 고점 대비 | 울린 시계 | 제안 판정 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        axis = r["axis_id"] + (f" · 거래 축 {r['price_driver']}" if r["price_driver_differs"] else "")
        pp = f"{r['pnl_pct']:+.1f}%" if isinstance(r.get("pnl_pct"), (int, float)) else "—"
        fp = f"{r['from_peak_pct']:+.1f}%" if isinstance(r.get("from_peak_pct"), (int, float)) else "—"
        tag = {"closed": f"닫힘 {r['closed']}", "held_weak": "보유·약세", "held_no_trail": "보유·추적 다리 없음"}[r["kind"]]
        L.append(f"| {r['market']} | {r['ticker']} {r['name']} | {r['thesis_id']} ({tag}) | {axis} ({r['driver_state']}) | "
                 f"{r['entered']} | {r['horizon_elapsed_pct'] if r['horizon_elapsed_pct'] is not None else '—'}% | "
                 f"{_money(r.get('pnl'), r['market'])} ({pp}) | {fp} | {' · '.join(r['clocks']) or '없음'} | "
                 f"{r['suggested']}{(' · ' + r['suggested_type']) if r['suggested_type'] else ''} |")
    if not rows:
        L.append("| — | *(없음)* | | | | | | | | |")
    fd = res["forecasts_due"]
    L += ["", f"## 기한 도래 전망 — {len(fd)}건 (opened + horizon_weeks가 지났다 · `forecast judge`로 hit/miss를 매긴다)", ""]
    L += [f"- {f['axis_id']} {f['direction']} · 기한 {f['due']} · 상태 {f['status']} · 확신도 {f['confidence']} — {f['expected']}" for f in fd] or ["*(없음)*"]
    ss = res["stale_scenarios"]
    L += ["", f"## 오래 열린 시나리오 — {len(ss)}건 (지연·무소식 가지 누락 후보 · `scenarios.py list --stale`)", ""]
    L += [f"- {s['id']} [{s['market']}] {s['why']} · {s['condition']}" for s in ss[:40]] or ["*(없음)*"]
    if len(ss) > 40:
        L.append(f"- … 외 {len(ss) - 40}건")
    fl = res["flips"]
    L += ["", "## 지난주 판정 보정 — 빡빡함·느슨함", ""]
    if fl["file"]:
        L.append(f"지난주 `{fl['file']}` 판정 {fl['judged']}건 중 뒤집힘 {len(fl['loose']) + len(fl['strict'])}건 "
                 f"(`loss_verdict_flip_rate` {fl['rate']}%) — 일시적→손실로 닫힘(느슨) {', '.join(fl['loose']) or '없음'} · "
                 f"오판→고점 재돌파(빡빡) {', '.join(fl['strict']) or '없음'}")
    else:
        L.append("지난주 손실 판정 파일 없음 — 첫 회차(보정은 다음 주부터)")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", required=True, help="YYMMDD")
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    print(render(collect(a.week, a.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
