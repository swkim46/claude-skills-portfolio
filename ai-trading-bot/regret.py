#!/usr/bin/env python3
"""아쉬움 감사 — "저거 미리 샀어야 했는데"를 **깔때기 숫자**로 바꾼다.

왜 있는가. 급등 뉴스를 볼 때마다 아쉽다는 말은 감정이고, 감정으로는 무엇을 고칠지 알 수 없다.
크게 움직인 종목마다 다섯 칸을 세면 어느 단에서 새는지가 나온다:
    축 있었나 → 전망 있었나 → 논지 있었나(그 시점에 살아 있던 것만) → 포지션 있었나 → 무엇이 막았나(유형)
"막은 것"이 기저율·규칙이면 그건 결함이 아니라 **맞은 판단**일 수 있다(9/9 두산에너빌리티 제외는 그 뒤
4일 연속 하락으로 맞았다). 그것과 진짜 결함을 가르는 판정은 `trade-feedback`이 주간에 한다.

★ 2026-10-03 — 세 가지를 바로잡았다.
1. **움직임은 종가 대 종가가 아니라 창 안 저점→고점 최대 상승폭**으로 잰다("창 안 어느 날 종가에 샀어도
   그 뒤 고가까지 몇 %"). 종가 대 종가는 창 안에서 올랐다가 되돌린 종목을 놓친다(사용자 지적). 고점일과
   고점 대비 되돌림을 같이 적는다 — 되돌린 종목은 진입도 놓치고, 보유 중이었다면 매도도 놓친 것이다.
2. **미국 종목이 통째로 빠지던 버그** — 일봉 TR에 주문용 거래소 코드(NASD/NYSE)를 넘겨 빈 응답을 받고
   조용히 건너뛰었다. 시세용 코드로 바꾸고, 응답이 비면 다른 두 거래소로 다시 묻는다.
3. **"막은 것" 54%가 '기록 없음'이었다** — 거부 사유만 봤기 때문이다. 이제 유형으로 가른다(아래 TYPE_KO).
   `no_record`는 논지가 살아 있었는데 제안·거부·주문 흔적이 하나도 없을 때만 남는다.

사용:
    python3 regret.py --market kr --stamp 261002 --fetch --record --out data/run_evidence/regret_261002_kr.md
    python3 regret.py --market us --stamp 261001 --fetch --days 20 --threshold 10     # 주간 창
    python3 regret.py --weekly --days 7                                                # 깔때기 집계
"""
import argparse
import glob
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
LEDGER = HERE / "journal" / "regret.jsonl"
MAIN_NAMES = HERE / "config" / "main_names.json"
MOVE_PCT = 8.0                      # 창 안 저점→고점 최대 상승폭 이 이상이면 '크게 움직였다'
LOOKBACK_DAYS = 5                   # 매 run 창(거래일). 주간 피드백은 20을 넘긴다
TINY_POS_PCT = 2.0                  # 보유 평가액이 그 시장 자산의 이 % 미만이면 '극소 크기'
ORDER_CODES = ("NASD", "NYSE", "AMEX")

# 막은 것의 유형 — 순서가 표시 우선순위다. **장부 기록(보유·편입·매도·진입 다리·주문·거부)이 앞, 노트 문구 매칭이 뒤**다 —
# 노트 줄에 이름과 '추격'이 같이 있다는 것은 약한 근거라, 규칙 매도 같은 확정 기록을 가리면 안 된다(2026-10-03: 삼성전자의
# '규칙 매도 뒤 재진입 없음'이 노트 문구 '추격 금지'에 가려 표에 안 보였다).
TYPE_KO = {
    "held": "보유", "tiny_size": "극소 크기", "universe_out": "유니버스 밖", "pending": "후보 대기",
    "rule_sell": "규칙 매도", "funding_sell": "회전 매도", "discretionary_sell": "재량 매도",
    "entry_unreached": "진입가 미도달", "unsent": "미전송·미체결", "funds": "자금", "target_cap": "목표 비중 도달",
    "target_gap": "목표 여유 없음", "rejected": "거부", "anti_chase": "추격 금지", "note_excluded": "노트 제외",
    "no_thesis": "논지 없음", "no_record": "기록 없음",
}


def _j(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _st(d: str) -> str:
    """'2026-09-21' · '20260921' · '260921' · ISO 시각 → '260921'."""
    s = re.sub(r"[^0-9]", "", str(d or ""))[:8]
    return s[2:] if len(s) == 8 else s[:6]


def _prev_stamp(stamp: str, days: int) -> str:
    d = datetime.strptime("20" + stamp, "%Y%m%d") - timedelta(days=days)
    return d.strftime("%y%m%d")


def snapshots(market: str) -> list:
    out = []
    for f in sorted(glob.glob(str(HERE / "data" / f"snapshot_2*_{market}.json"))):
        m = re.search(r"snapshot_(\d{6})_", f)
        if m and (d := _j(f)):
            out.append((m.group(1), d))
    return out


def run_up(rows: list) -> dict:
    """날짜 오름차순 `[{date, close, high?, low?}]` → 창 안 **최대 상승폭**.

    ret = max_j( max(high_j, close_j) / min_{i<j} close_i − 1 ) — 창 안 어느 날 종가에 샀어도 그 뒤 고가까지 몇 %.
    같은 날의 저가→고가는 세지 않는다(그날 종가 전에는 그 저가에 샀다고 말할 수 없다).
    """
    rows = [r for r in rows if r.get("close")]
    if len(rows) < 2:
        return {}
    lo, lo_d = float(rows[0]["close"]), rows[0]["date"]
    best = None
    for r in rows[1:]:
        hi = max(float(r.get("high") or 0), float(r["close"]))
        g = (hi / lo - 1) * 100
        if best is None or g > best[0]:
            best = (g, lo, lo_d, hi, r["date"])
        if float(r["close"]) < lo:
            lo, lo_d = float(r["close"]), r["date"]
    g, trough, td, peak, pd = best
    last, first = float(rows[-1]["close"]), float(rows[0]["close"])
    lows = [float(r.get("low") or r["close"]) for r in rows if float(r.get("low") or r["close"]) > 0]
    return {"ret": g, "trough": trough, "trough_date": _st(td), "peak": peak, "peak_date": _st(pd),
            "retrace_pct": (last / peak - 1) * 100, "ret_close": (last / first - 1) * 100,
            "low_min": min(lows) if lows else trough, "last": last,
            "from": _st(rows[0]["date"]), "to": _st(rows[-1]["date"])}


def moves(market: str, stamp: str, lookback: int = LOOKBACK_DAYS) -> list:
    """스냅샷 시계열(오프라인 — 호출 0). run이 돈 날의 가격뿐이라 고가가 없다 → 근사(`approx`)."""
    snaps = [(s, d) for s, d in snapshots(market) if s <= stamp][-(lookback + 1):]
    if len(snaps) < 2:
        return []
    wl = _j(HERE / "config" / "watchlist.json", {}) or {}
    names = {r["ticker"]: r.get("name", "") for r in (wl.get(market.upper()) or [])}
    series = {}
    for s, d in snaps:
        for tk, v in (d.get("prices") or {}).items():
            if (v or {}).get("price"):
                series.setdefault(tk, []).append({"date": s, "close": float(v["price"])})
    out = []
    for tk, rows in series.items():
        ru = run_up(rows)
        if ru:
            out.append({"ticker": tk, "name": names.get(tk, ""), "approx": True, "src": ["universe"], **ru})
    return sorted(out, key=lambda r: -r["ret"])


def watch_names(market: str) -> dict:
    """잴 이름 — 유니버스 + 지도 축의 이름·수혜 종목 + 메인 종목(시총 상위). ticker → {name, excd, src[]}."""
    sys.path.insert(0, str(HERE))
    import market_map as mm
    mk = market.upper()
    out = {}

    def put(tk, nm, ex, src):
        tk = str(tk or "").strip()
        if not tk:
            return
        r = out.setdefault(tk, {"name": nm or "", "excd": ex or "", "src": []})
        r["name"] = r["name"] or nm or ""
        r["excd"] = r["excd"] or ex or ""
        if src not in r["src"]:
            r["src"].append(src)

    for r in ((_j(HERE / "config" / "watchlist.json", {}) or {}).get(mk) or []):
        put(r.get("ticker"), r.get("name"), r.get("excd"), "universe")
    _, by_id = mm.axes_index()
    for a in by_id.values():
        for n in (a.get("names") or []) + (a.get("beneficiaries") or []):
            tk = str(n.get("ticker") or "").strip()
            if tk and mm._market_of(tk) == mk:
                put(tk, n.get("name"), n.get("excd"), "map")
    for r in ((_j(MAIN_NAMES, {}) or {}).get(mk) or []):
        put(r.get("ticker"), r.get("name"), r.get("excd"), "main")
    return out


def daily_rows(client, ticker: str, market: str, excd: str, end: str, days: int) -> list:
    """일봉 `days+1`행(날짜 오름차순). 미국은 시세용 거래소 코드로 묻고, 비면 나머지 두 곳도 묻는다."""
    from kis_client import KisError, quote_excd
    end_d = datetime.strptime("20" + end, "%Y%m%d")
    endS = end_d.strftime("%Y%m%d")
    rows = []
    if market.upper() == "KR":
        start = end_d - timedelta(days=days * 2 + 12)
        rows = client.domestic_daily(ticker, start.strftime("%Y%m%d"), endS)
    else:
        first = (excd or "NASD").upper()
        first = first if first in ORDER_CODES else {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}.get(first, "NASD")
        for ex in [first] + [e for e in ORDER_CODES if e != first]:
            try:
                rows = client.overseas_daily(ticker, quote_excd(ex), endS)
            except KisError:
                rows = []
            if rows:
                break
    rows = [r for r in rows if r.get("close") and r.get("date", "") <= endS]
    return list(reversed(rows[:days + 1]))


def moves_fetch(market: str, stamp: str, days: int = LOOKBACK_DAYS, errors: list = None, client=None,
                keep: dict = None) -> list:
    """KIS 일봉으로 **진짜 N거래일 창**의 최대 상승폭 — 유니버스 + 지도 + 메인 종목(유니버스 밖 포함)."""
    sys.path.insert(0, str(HERE))
    from kis_client import KisClient, KisError
    c = client or KisClient.for_mode()
    uni = {r["ticker"] for r in ((_j(HERE / "config" / "watchlist.json", {}) or {}).get(market.upper()) or [])}
    out = []
    for tk, info in watch_names(market).items():
        try:
            rows = daily_rows(c, tk, market, info["excd"], stamp, days)
        except KisError as e:
            if errors is not None:
                errors.append(f"{tk}: {str(e)[:60]}")
            continue
        if len(rows) <= days // 2:
            if errors is not None:
                errors.append(f"{tk}: 일봉 {len(rows)}행")
            continue
        if keep is not None:
            keep[tk] = rows                      # 일봉 재사용(조기·늦은 매도 판정) — 같은 호출을 두 번 하지 않는다
        ru = run_up(rows)
        if ru:
            out.append({"ticker": tk, "name": info["name"], "src": info["src"], "in_universe": tk in uni,
                        "main_name": "main" in info["src"], "approx": False, **ru})
    return sorted(out, key=lambda r: -r["ret"])


class Ctx:
    """깔때기·유형 판정이 읽는 원장 묶음. 테스트는 dict로 바로 만든다(`Ctx(market, stamp, data={...})`)."""

    def __init__(self, market: str, stamp: str, data: dict = None):
        self.market, self.stamp = market, stamp
        if data is not None:
            self.__dict__.update({"approved": [], "trades": [], "notes": [], "materials": [], "theses": [],
                                  "snaps": [], "pending": {}, "uni": set(), "by_tk": {}, "by_id": {}, **data})
            return
        sys.path.insert(0, str(HERE))
        import market_map as mm
        self.by_tk, self.by_id = mm.axes_index()
        # ★ 축 색인은 names[]만 읽는다 — 전망 수혜 표(beneficiaries[])에만 있는 종목도 그 축에 걸린 것이다(FB-261010-1).
        #   빼면 2·3차 수혜가 깔때기에서 '축 없음·전망 없음'으로 빠져 가장 많이 새는 단이 뒤바뀐다(261010: 56.4% → 정정 10.7%).
        #   측정 대상(watch_names)은 이미 둘 다 읽는다 — 깔때기도 같은 집합을 본다.
        for aid, a in self.by_id.items():
            for b in a.get("beneficiaries") or []:
                tk = str(b.get("ticker") or "").strip()
                if tk and aid not in self.by_tk.setdefault(tk, []):
                    self.by_tk[tk].append(aid)
        lo = _prev_stamp(stamp, 45)

        def dated(pattern, rx):
            for f in sorted(glob.glob(str(HERE / pattern))):
                m = re.search(rx, f)
                if m and lo <= m.group(1) <= stamp:
                    yield m.group(1), f
        from kis_client import same_account                 # 지금 계좌 계열의 승인·거래만(2026-10-15 · 표시 없는 옛 파일은 모의)
        self.approved = [(s, d) for s, f in dated(f"signals/approved_*_{market}.json", r"approved_(\d{6})_")
                         for d in [_j(f, {}) or {}] if same_account(d)]
        self.trades = [(s, d) for s, f in dated(f"journal/trades_*_{market}.json", r"trades_(\d{6})_")
                       for d in [_j(f, {}) or {}] if same_account(d)]
        notes = {}
        for s, f in dated(f"analysis/분석노트_*_{market}_v*.md", r"분석노트_(\d{6})_"):
            notes[s] = f                                   # 정렬상 마지막 = 가장 높은 판
        self.notes = [(s, Path(f).read_text(encoding="utf-8", errors="ignore")) for s, f in sorted(notes.items())]
        self.materials = []
        for f in sorted(glob.glob(str(HERE / "data" / "material_2*_*.md"))):
            m = re.search(r"material_(\d{6})_", f)
            if m:
                try:
                    self.materials.append((m.group(1), Path(f).read_text(encoding="utf-8", errors="ignore")))
                except OSError:
                    pass
        self.theses = (_j(HERE / "journal" / "theses.json", {}) or {}).get("theses") or []
        self.snaps = [(s, d) for s, d in snapshots(market) if s <= stamp]
        self.pending = _j(HERE / "journal" / "universe_pending.json", {}) or {}
        self.uni = {r["ticker"] for r in ((_j(HERE / "config" / "watchlist.json", {}) or {}).get(market.upper()) or [])}


# 티커가 금융 일반 낱말과 같은 종목 — 일반 낱말로 쓰인 **문맥만 지우고** 나머지는 티커로 센다.
# 실사례(2026-10-03): 이튼(ETN)의 평가 이력이 'ETF·ETN 제외'(국내 애프터마켓 안내) 줄에 걸렸다. 회사 이름을 요구하면
# 'GEV · ETN · CMI'처럼 티커로 실제 언급한 줄을 놓친다 — 그래서 이름 요구가 아니라 문맥 제거다.
AMBIGUOUS_TICKERS = {"ETN": r"ETF\s*[·/,]\s*ETN|ETN\s*[·/,]\s*ETF|ETN\s*제외|ETN\s*\(상장지수"}


def _mentions(text: str, ticker: str, name: str) -> bool:
    if ticker in AMBIGUOUS_TICKERS:
        text = re.sub(AMBIGUOUS_TICKERS[ticker], " ", text)
    if re.fullmatch(r"\d{6}", ticker):
        hit = ticker in text
    else:
        hit = re.search(rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])", text) is not None
    return hit or (len(name or "") >= 2 and name in text)


def _closed_at(t: dict) -> str:
    for h in reversed(t.get("history") or []):
        if h.get("status") == "closed":
            return _st(h.get("ts"))
    return _st(t.get("closed_at") or t.get("expires") or "") if t.get("status") == "closed" else ""


def thesis_state(ticker: str, ctx: Ctx, trough: str, peak: str) -> dict:
    """그 창에 **살아 있던** 논지만 '논지 있음'으로 센다 — 닫힌 논지를 세면 깔때기가 거짓으로 통과한다."""
    mine = [t for t in ctx.theses if t.get("ticker") == ticker and _st(t.get("created")) <= peak]
    live = [t for t in mine if t.get("status") != "closed" or (_closed_at(t) or "999999") >= trough]
    return {"thesis": bool(mine), "thesis_live": bool(live), "thesis_ids": [t.get("id") for t in live],
            "thesis_closed_before": bool(mine) and not live, "_live": live}


def _weight(ctx: Ctx, ticker: str, lo: str, hi: str) -> tuple:
    """창 안 스냅샷에서 (보유했나, 최대 비중 %, 지금 보유하나).

    분모는 **종목 평가금액 합 + 현금**이다 — `eval_total`을 쓰지 않는다. 해외 잔고 TR의 요약 필드는 평가손익(원화)이라
    미국 비중이 수십~수백 분의 1로 계산됐다(CEG 실제 19.8% → 0.84%로 '극소 크기' · FB-261010-2). 국내는 두 값이 같다.
    """
    held, w = False, 0.0
    window = [(s, d) for s, d in ctx.snaps if lo <= s <= hi] or [x for x in ctx.snaps if x[0] <= hi][-1:]
    for _, d in window:
        b = d.get("balance") or {}
        eq = (sum(float(p.get("eval_amt") or 0) for p in b.get("positions") or [])
              + max(0.0, float(b.get("cash") or 0)))
        for p in b.get("positions") or []:
            if p.get("ticker") == ticker and float(p.get("qty") or 0) > 0:
                held = True
                if eq > 0:
                    w = max(w, float(p.get("eval_amt") or 0) / eq * 100)
    now = any(p.get("ticker") == ticker and float(p.get("qty") or 0) > 0
              for p in ((ctx.snaps[-1][1].get("balance") or {}).get("positions") or [])) if ctx.snaps else False
    return held, w, now


def _reject_type(why: str) -> str:
    if re.search(r"자금|현금|환전", why):
        return "funds"
    if "추격" in why:
        return "anti_chase"
    if "이미 목표 비중" in why:
        return "target_cap"
    if "목표 여유" in why:
        return "target_gap"
    if "매수 가능 수량 0주" in why:
        return "tiny_size"
    return "rejected"


def blockers(mv: dict, ctx: Ctx, ts: dict = None) -> list:
    """무엇이 막았나 — `[{type, when, detail}]`. 하나도 안 걸릴 때만 `no_thesis`(논지 없음) 또는 `no_record`."""
    tk, nm = mv["ticker"], mv.get("name", "")
    trough, peak = mv.get("trough_date") or mv.get("from", ""), mv.get("peak_date") or ctx.stamp
    lo = _prev_stamp(mv.get("from") or trough, 7)
    ts = ts or thesis_state(tk, ctx, trough, peak)
    out = []
    held, w, _ = _weight(ctx, tk, trough, peak)
    if held:
        out.append({"type": "tiny_size" if w < TINY_POS_PCT else "held", "when": peak,
                    "detail": f"창 안 보유 최대 비중 {w:.1f}%"})
    if tk not in ctx.uni:
        out.append({"type": "universe_out", "when": "", "detail": "감시 대상이 아니었다"})
    key = f"{ctx.market.upper()}:{tk}"
    if key in ctx.pending:
        d = ctx.pending[key].get("dates") or []
        out.append({"type": "pending", "when": _st(d[-1]) if d else "",
                    "detail": f"제안일 {', '.join(d)} · 냉각 {len(set(d))}/2일"})
    trades_by = {s: t for s, t in ctx.trades}
    for s, a in ctx.approved:
        if not (lo <= s <= peak):
            continue
        for r in a.get("rejected") or []:
            if r.get("ticker") == tk:
                why = str(r.get("why") or "")
                out.append({"type": _reject_type(why), "when": s, "detail": f"거부: {why[:60]}"})
        for o in a.get("orders") or []:
            if o.get("ticker") != tk or o.get("action") != "BUY":
                continue
            rows = [t for t in (trades_by.get(s) or {}).get("trades") or []
                    if t.get("ticker") == tk and t.get("action") == "BUY"]
            ok = [t for t in rows if (t.get("fill") or {}).get("verdict") in ("FILLED", "PARTIAL")
                  or t.get("status") == "FILLED"]
            if not ok:
                last = rows[-1] if rows else {}
                v = (last.get("fill") or {}).get("verdict")
                why = "전송 기록 없음" if not rows else \
                    "전송됨 · 체결 미확인" if (last.get("status") == "SENT" and not v) else f"{last.get('status')}/{v}"
                out.append({"type": "unsent", "when": s, "detail": f"승인 {o.get('qty')}주 → {why}"})
    for s, d in ctx.trades:
        if not (lo <= s <= peak):
            continue
        for t in d.get("trades") or []:
            if t.get("ticker") != tk or t.get("action") != "SELL":
                continue
            if (t.get("fill") or {}).get("verdict") not in ("FILLED", "PARTIAL") and t.get("status") != "FILLED":
                continue
            src = t.get("source")
            typ = "funding_sell" if src == "funding" else \
                "rule_sell" if (src == "discipline" or t.get("trigger_id")) else "discretionary_sell"
            again = any(x.get("ticker") == tk and _st(x.get("created")) > s for x in ctx.theses)
            out.append({"type": typ, "when": s,
                        "detail": f"SELL {t.get('qty')}주 {t.get('trigger_id') or src} · "
                                  f"{'재진입 논지 있음' if again else '재진입 논지 없음'}"})
    for t in ts.get("_live") or []:
        for e in t.get("entry_triggers") or []:
            if e.get("fired"):
                continue
            m = re.search(r"price\s*<=\s*([\d.]+)", str(e.get("check") or ""))
            if m and float(mv.get("low_min") or 0) > float(m.group(1)) > 0:
                lvl = float(m.group(1))
                out.append({"type": "entry_unreached", "when": _st(t.get("created")),
                            "detail": f"{t.get('id')} {e.get('id')} ≤ {lvl:,.2f} · 창 최저 {float(mv['low_min']):,.2f} "
                                      f"(+{(float(mv['low_min']) / lvl - 1) * 100:.1f}% 위)"})
    for s, txt in ctx.notes:
        if not (lo <= s <= peak):
            continue
        got = set()
        for line in txt.splitlines():
            if not _mentions(line, tk, nm):
                continue
            typ = "anti_chase" if "추격" in line else "note_excluded" if re.search(r"제외|기각|냉각", line) else ""
            if typ and typ not in got:
                got.add(typ)
                out.append({"type": typ, "when": s, "detail": f"노트: {line.replace('|', '·').strip()[:70]}"})
    if not [b for b in out if b["type"] not in ("held",)]:
        if held:
            pass
        elif tk in ctx.uni and not ts["thesis_live"]:
            out.append({"type": "no_thesis", "when": "", "detail": "유니버스 안인데 살아 있는 논지가 없었다 — 아무도 사자고 하지 않았다"})
        elif not out:
            out.append({"type": "no_record", "when": "", "detail": "논지는 있었는데 제안·거부·주문 흔적이 없다"})
    order = list(TYPE_KO)
    return sorted(out, key=lambda b: (order.index(b["type"]), b.get("when") or ""))


def first_mention(ctx: Ctx, ticker: str, name: str) -> str:
    for s, txt in ctx.materials:
        if _mentions(txt, ticker, name):
            return s
    return ""


def blocked_text(bs: list) -> str:
    """서로 다른 유형을 먼저 보인다 — 같은 유형이 날짜만 바꿔 세 칸을 다 차지하면 다른 원인이 가려진다."""
    seen, pick = set(), []
    for b in bs:
        if b["type"] not in seen:
            seen.add(b["type"]); pick.append(b)
    return " / ".join(f"{TYPE_KO[b['type']]}: {b['detail']}" for b in pick[:3]) or "기록 없음"


def funnel(market: str, stamp: str, threshold: float = MOVE_PCT, fetch: bool = False,
           days: int = LOOKBACK_DAYS, mvs: list = None, ctx: Ctx = None) -> list:
    ctx = ctx or Ctx(market, stamp)
    if mvs is None:
        mvs = moves_fetch(market, stamp, days) if fetch else moves(market, stamp, days)
    rows = []
    for mv in mvs:
        if mv["ret"] < threshold:
            continue
        tk = mv["ticker"]
        axes = ctx.by_tk.get(tk, [])
        fc = [a for a in axes if (ctx.by_id.get(a) or {}).get("direction")
              and any(b.get("ticker") == tk for b in (ctx.by_id[a].get("beneficiaries") or []))]
        ts = thesis_state(tk, ctx, mv.get("trough_date", ""), mv.get("peak_date", stamp))
        held, w, now = _weight(ctx, tk, mv.get("trough_date", ""), mv.get("peak_date", stamp))
        bs = blockers(mv, ctx, ts)
        rows.append({"stamp": stamp, "market": market, "ticker": tk, "name": mv.get("name", ""),
                     "ret": round(mv["ret"], 2), "ret_close": round(mv.get("ret_close", mv["ret"]), 2),
                     "retrace_pct": round(mv.get("retrace_pct", 0.0), 2),
                     "trough_date": mv.get("trough_date", ""), "peak_date": mv.get("peak_date", ""),
                     "window": f"{mv.get('trough_date', mv.get('from'))}→{mv.get('peak_date', mv.get('to'))}",
                     "approx": bool(mv.get("approx")), "days": days,
                     "in_universe": tk in ctx.uni, "main_name": bool(mv.get("main_name")),
                     "axis": bool(axes), "axis_ids": axes, "forecast": bool(fc),
                     "thesis": ts["thesis"], "thesis_live": ts["thesis_live"], "thesis_ids": ts["thesis_ids"],
                     "thesis_closed_before": ts["thesis_closed_before"],
                     "position": held, "position_max_pct": round(w, 2), "position_now": now,
                     "first_mention": first_mention(ctx, tk, mv.get("name", "")) or "없음",
                     "blockers": bs, "blocked": blocked_text(bs)})
    return rows


def _leak(rows: list) -> dict:
    th = lambda r: r.get("thesis_live", r.get("thesis"))
    return {"축 없음": sum(not r["axis"] for r in rows),
            "축은 있는데 전망 없음": sum(r["axis"] and not r["forecast"] for r in rows),
            "전망은 있는데 논지 없음": sum(r["forecast"] and not th(r) for r in rows),
            "논지는 있는데 포지션 없음": sum(th(r) and not r["position"] for r in rows)}


def _primary(r: dict) -> str:
    bs = r.get("blockers")
    if bs:
        miss = [b for b in bs if b["type"] != "held"]
        return (miss or bs)[0]["type"]
    return "no_record" if r.get("blocked") == "기록 없음" else "legacy"


def render(rows: list, market: str, stamp: str, days: int = LOOKBACK_DAYS, threshold: float = MOVE_PCT) -> str:
    th = lambda r: r.get("thesis_live", r.get("thesis"))
    approx = any(r.get("approx") for r in rows)
    L = [f"## 아쉬움 감사 — {stamp} · {market.upper()}", "",
         f"[아쉬움] 급등 {len(rows)}건 · 축 {sum(r['axis'] for r in rows)} · 전망 {sum(r['forecast'] for r in rows)} · "
         f"논지 {sum(bool(th(r)) for r in rows)} · 포지션 {sum(r['position'] for r in rows)}", "",
         f"> 최근 {days}거래일 창 안 **저점→고점 최대 상승폭** ≥ +{threshold:.0f}%(유니버스 + 지도 + 시총 상위). "
         f"종가 대 종가가 아니므로 **올랐다가 되돌린 종목도 들어온다**(되돌림 열). 논지는 그 창에 살아 있던 것만 센다. "
         f"'막은 것'이 기저율·규칙이면 맞은 판단일 수 있다 — 결함 판정은 `trade-feedback`이 한다."
         + (" ※ 스냅샷 근사(고가 없음 — run일 가격만)." if approx else ""), ""]
    if not rows:
        L.append("*(해당 없음)*")
        return "\n".join(L) + "\n"
    L += ["| 종목 | 최대 상승 | 저점→고점 | 되돌림 | 유니버스 | 축 | 전망 | 논지 | 포지션 | 재료 첫 등장 | 막은 것 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    y = lambda b: "✓" if b else "✗"
    for r in rows:
        uni = "안" if r.get("in_universe", True) else "**밖**"
        if r.get("main_name"):
            uni += "·시총상위"
        thx = y(th(r)) + (" (닫힘)" if r.get("thesis_closed_before") else "")
        pos = y(r["position"]) + (f" {r['position_max_pct']:.1f}%" if r.get("position") else "")
        L.append(f"| {r['ticker']} {r['name']} | {r['ret']:+.1f}% | {r['window']} | {r.get('retrace_pct', 0):+.1f}% | {uni} | "
                 f"{y(r['axis'])} {','.join(r['axis_ids'][:2])} | {y(r['forecast'])} | {thx} | {pos} | "
                 f"{r['first_mention']} | {r['blocked']} |")
    leak = _leak(rows)
    top = max(leak.items(), key=lambda kv: kv[1])
    cnt = {}
    for r in rows:
        cnt[_primary(r)] = cnt.get(_primary(r), 0) + 1
    L += ["", "**막은 것 — 1순위 유형별**: " + " · ".join(f"{TYPE_KO.get(k, k)} {v}" for k, v in
                                                     sorted(cnt.items(), key=lambda kv: -kv[1])),
          "", f"**가장 많이 새는 단: {top[0]} ({top[1]}건)** — " +
          {"축 없음": "지도가 그 사건을 몰랐다 → 3단 전망 질문 1번(돈이 어디로 가는가)",
           "축은 있는데 전망 없음": "지도는 알았는데 앞을 안 봤다 → `forecast add`로 방향·수혜 순서를 세워라",
           "전망은 있는데 논지 없음": "수혜를 적고 안 샀다 → 미반영 2차 수혜에 e0 선진입",
           "논지는 있는데 포지션 없음": "사려 했는데 막혔다 → '막은 것' 유형이 기저율·규칙이면 맞은 것, 자금·냉각·미도달이면 결함 후보"}[top[0]]]
    return "\n".join(L) + "\n"


def record(rows: list) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_ledger(since: str = "") -> list:
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("stamp", "") >= since:
                rows.append(r)
    return rows


def weekly(days: int) -> str:
    """종목 단위(같은 종목은 가장 큰 움직임 한 행)로 깔때기와 막은 유형을 센다."""
    since = (datetime.now(KST) - timedelta(days=days)).strftime("%y%m%d")
    best = {}
    for r in load_ledger(since):
        k = (r.get("market"), r.get("ticker"))
        if k not in best or r.get("ret", 0) > best[k].get("ret", 0):
            best[k] = r
    rows = list(best.values())
    n = len(rows)
    th = lambda r: r.get("thesis_live", r.get("thesis"))
    L = ["## 아쉬움 깔때기 — 성공 지표 ④의 뒷면", "",
         f"[아쉬움] 급등 {n}종목 · 축 {sum(r['axis'] for r in rows)} · 전망 {sum(r['forecast'] for r in rows)} · "
         f"논지 {sum(bool(th(r)) for r in rows)} · 포지션 {sum(r['position'] for r in rows)}", ""]
    if not n:
        L.append("*(최근 급등 기록 없음 — `regret.py`가 아직 run에서 안 돌았거나 급등이 없었다)*")
        return "\n".join(L) + "\n"
    L += ["| 단 | 통과 | 비율 |", "|---|---|---|"]
    for k, ko in (("axis", "축"), ("forecast", "전망"), ("thesis", "논지(살아 있던 것)"), ("position", "포지션")):
        c = sum(bool(th(r)) if k == "thesis" else bool(r[k]) for r in rows)
        L.append(f"| {ko} | {c}/{n} | {100 * c / n:.0f}% |")
    cnt = {}
    for r in rows:
        cnt[_primary(r)] = cnt.get(_primary(r), 0) + 1
    L += ["", "| 막은 것(1순위) | 종목 |", "|---|---|"]
    for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]):
        L.append(f"| {TYPE_KO.get(k, '구형식 행(유형 없음)' if k == 'legacy' else k)} | {v} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--stamp", default=None, help="기본: 그 시장의 현재 run 스탬프(현지 거래일 · sessions.run_stamp)")
    ap.add_argument("--threshold", type=float, default=MOVE_PCT)
    ap.add_argument("--days", type=int, default=None, help=f"창 길이(거래일, 기본 {LOOKBACK_DAYS}) · --weekly면 집계 기간(일, 기본 7)")
    ap.add_argument("--out", default="")
    ap.add_argument("--record", action="store_true", help="journal/regret.jsonl에 적재")
    ap.add_argument("--fetch", action="store_true", help="KIS 일봉(고가 포함) — 유니버스 밖 축 종목·시총 상위 포함")
    ap.add_argument("--weekly", action="store_true")
    a = ap.parse_args()
    if not a.stamp:
        import sessions as _ss
        a.stamp = _ss.run_stamp(a.market)
    if a.weekly:
        print(weekly(a.days or 7)); return 0
    days = a.days or LOOKBACK_DAYS
    errors = []
    mvs = moves_fetch(a.market, a.stamp, days, errors) if a.fetch else moves(a.market, a.stamp, days)
    rows = funnel(a.market, a.stamp, a.threshold, a.fetch, days, mvs=mvs)
    s = render(rows, a.market, a.stamp, days, a.threshold)
    if errors:
        s += f"\n> 조회 실패 {len(errors)}건(급등 여부 미확인 — '없음'으로 읽지 말 것): " + " · ".join(errors[:12]) + "\n"
    print(s)
    if a.record:
        record(rows)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(s, encoding="utf-8"); print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
