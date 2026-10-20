#!/usr/bin/env python3
"""
지지 구간 종가 이탈 — 지지 구간(다리 basis의 `구간 lo~hi`)을 기준으로 가진 **보유** 논지의 직전 세션 종가가
구간 아랫선(lo) 아래로 끝났는지 센다.

왜(2026-10-13 차트스쿨 01 「지지와 저항」 반영 B · 사용자 승인): 지지가 깨졌는지는 장중 가격이 아니라 **종가**로 본다 —
장중에 구간 아래로 빠졌다가 종가로 회복하면 깨진 것이 아니고, 종가가 구간 아래로 끝나야 깨진 것이다.
손절은 지금처럼 장중 터치로 따로 집행된다(2026-09-16 사용자 결정). 이 점검은 **손절 전에** 종가가 구간 아래로 끝난 날을
잡아 다음 run이 그 논지의 무효 조건을 다시 판정하게 하고, 그 run에서는 그 종목을 더 사지 않게 한다(`risk_guard`).

직전 세션 종가는 스냅샷에서 읽는다(API 추가 호출 없음):
- 장중 캡처 — 전일 종가 칸 `prev_close`(국내 `stck_sdpr` 기준가 · 미국 `base`, 2026-10-14부터 `ingest`가 싣는다).
  옛 스냅샷이면 `price ÷ (1 + change_pct/100)`로 되짚는다(국내는 호가 단위로 반올림 · 오차 한 호가 안).
- 장 밖 캡처 — `price`가 곧 직전 세션 종가다.

    python3 support_break.py --market kr --snapshot data/snapshot_<YYMMDD>_kr.json     # 이어받기 절 미리보기
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

HEAD = "## 지지 이탈"


def _num(s) -> float:
    return float(str(s).replace(",", ""))


def last_close(q: dict, market: str, generated_at: str = "") -> tuple:
    """(직전 완결 세션 종가, 어떻게 얻었나). 못 구하면 (None, 사유)."""
    from kis_client import kr_tick_size, market_session
    px = q.get("price")
    try:
        px = float(px) if px is not None else None
    except (TypeError, ValueError):
        px = None
    if not px:
        return None, "시세 없음"
    try:
        at = datetime.fromisoformat(str(generated_at)) if generated_at else None
    except ValueError:
        at = None
    if at is None:
        return None, "캡처 시각 없음 — 장중인지 알 수 없다"
    if not market_session(market.upper(), at)["is_open"]:
        return px, "장 밖 캡처 — 현재가 = 직전 세션 종가"
    pc = q.get("prev_close")
    try:
        pc = float(pc) if pc else None
    except (TypeError, ValueError):
        pc = None
    if pc:
        return pc, "장중 캡처 — 전일 종가 칸"
    chg = q.get("change_pct")
    try:
        chg = float(chg)
    except (TypeError, ValueError):
        return None, "장중 캡처인데 전일 종가·등락률이 없다"
    raw = px / (1 + chg / 100)
    if market.upper() == "KR":
        tick = kr_tick_size(raw)
        return float(round(raw / tick) * tick), "장중 캡처 — 현재가÷(1+등락률) 되짚기(호가 반올림)"
    return round(raw, 2), "장중 캡처 — 현재가÷(1+등락률) 되짚기"


def zone_legs(t: dict) -> list:
    """논지의 구간 다리 — [(lo, hi, 다리 id, 'entry'|'exit')]."""
    from theses import ZONE_RE
    out = []
    for kind, key in (("entry", "entry_triggers"), ("exit", "exit_triggers")):
        for g in t.get(key) or []:
            z = ZONE_RE.search(str(g.get("basis") or ""))
            if z:
                out.append((_num(z.group(1)), _num(z.group(2)), g.get("id") or "?", kind))
    return out


def scan(market: str, snapshot: dict, theses_rows: list = None) -> tuple:
    """(이탈 행, 종가를 못 잰 행, 구간 다리를 가진 보유 논지 수)."""
    if theses_rows is None:
        import db                                        # 논지는 저장소(jd_thesis)에서
        theses_rows = db.theses_all()
    m = market.upper()
    prices = snapshot.get("prices") or {}
    gen = snapshot.get("generated_at") or ""
    broken, unmeasured, n = [], [], 0
    for t in theses_rows:
        if str(t.get("market") or "").upper() != m or t.get("status") != "held":
            continue
        zl = zone_legs(t)
        if not zl:
            continue
        n += 1
        tk = t.get("ticker")
        close, how = last_close(prices.get(tk) or {}, m, gen)
        row = {"thesis": t.get("id"), "ticker": tk, "name": t.get("name") or "", "how": how}
        if close is None:
            unmeasured.append(row)
            continue
        hit = [z for z in zl if close < z[0]]
        if hit:
            lo, hi, leg, kind = max(hit, key=lambda z: z[0])      # 깨진 구간 중 가장 위(가장 먼저 깨진 지지)
            broken.append({**row, "close": close, "lo": lo, "hi": hi, "leg": leg, "kind": kind,
                           "what": f"종가 {close:,.2f} < 구간 {lo:,.2f}~{hi:,.2f}(다리 {leg})"})
    return broken, unmeasured, n


def breaks(market: str, snapshot: dict, theses_rows: list = None) -> list:
    return scan(market, snapshot, theses_rows)[0]


def ticker_map(market: str, snapshot: dict, theses_rows: list = None) -> dict:
    """{종목: 한 줄 사유} — risk_guard가 이 run의 추가 매수를 거부할 때 쓴다."""
    return {r["ticker"]: r["what"] for r in breaks(market, snapshot, theses_rows)}


def carry_block(market: str, snapshot_path, theses_rows: list = None) -> str:
    """이어받기 파일의 `## 지지 이탈` 절."""
    head = HEAD + "\n\n"
    try:
        snap = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return head + f"★ 스냅샷을 읽지 못했다 — {type(e).__name__}: {e} · 이 run은 지지 이탈을 판정하지 못한다(노트 §10 한계에 적는다)\n"
    broken, unmeasured, n = scan(market, snap, theses_rows)
    if not n:
        return head + "(지지 구간(`구간 lo~hi`)을 기준으로 가진 보유 논지 없음)\n"
    lines = [head]
    if broken:
        lines += ["지지 구간을 기준으로 산 보유의 **직전 세션 종가가 구간 아랫선 아래로 끝났다** — 오늘 노트 §6에 종목마다 "
                  "`[지지 이탈] <종목> — <무효 조건 재판정: 유지·축소·청산과 이유>` 줄을 쓴다. 이 run에서는 그 종목을 더 사지 "
                  "않는다(`risk_guard`가 거부). 손절은 장중 터치로 따로 집행된다.\n",
                  "| 논지 | 종목 | 구간 | 직전 종가 | 어떻게 쟀나 | 오늘 판정할 것 |", "|---|---|---|---|---|---|"]
        for r in broken:
            lines.append(f"| {r['thesis']} | {r['ticker']} {r['name']} | {r['lo']:,.2f}~{r['hi']:,.2f}(다리 {r['leg']}) "
                         f"| {r['close']:,.2f} | {r['how']} | 무효 조건 재판정 |")
    else:
        lines.append(f"구간 다리를 가진 보유 논지 {n}개 — 직전 종가가 구간 아래로 끝난 종목 없음\n")
    if unmeasured:
        lines.append("\n**종가를 못 잰 보유** — 노트 §10 한계에 적는다: "
                     + " · ".join(f"{r['ticker']}({r['how']})" for r in unmeasured))
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    p = Path(a.snapshot)
    print(carry_block(a.market, p if p.is_absolute() else HERE / p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
