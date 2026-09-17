#!/usr/bin/env python3
"""보유 최고가 원장 — **시장별로 나눠 담는다.**

왜 있는가: `position_peaks.json`이 시장 구분 없는 평평한 dict였고, `journal.update_peaks`가
`set(peaks) - held`로 "안 들고 있는 종목"을 지웠다. 그래서 **미국 run이 국내 peak 전체를,
국내 run이 미국 peak 전체를 매번 지웠다** — 두 시장을 번갈아 돌리므로 트레일링 스톱의
고점이 매일 리셋된다. 현재 `stop_loss_pct: null`(기계적 손절 OFF)이라 잠복 상태지만,
되살리는 순간 트레일링 스톱이 아무것도 못 지킨다.

**읽는 쪽(risk_guard)과 쓰는 쪽(journal)이 표기 규칙을 각자 갖고 있으면 조용히 갈린다.**
그래서 두 곳이 이 모듈 하나를 쓴다.

구조 `{"_schema": 2, "KR": {티커: {peak_price, peak_at}}, "US": {...}}`
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PEAKS_PATH = HERE / "journal" / "position_peaks.json"
KST = timezone(timedelta(hours=9))
SCHEMA = 2
MARKETS = ("KR", "US")


def market_of(ticker: str) -> str:
    """티커 모양으로 시장을 가른다 — 국내는 6자리 숫자, 미국은 알파벳."""
    return "KR" if re.fullmatch(r"\d{6}", str(ticker or "")) else "US"


def load(path: Path = None) -> tuple:
    """(원장, 오류). 평평한 구버전은 **티커 모양으로 시장을 붙여 이관**한다.

    못 읽으면 옆으로 치워 보존하고 빈 원장을 준다 — 고점 이력은 오늘 시세로 다시
    쌓을 수 있으므로 치명적이진 않지만, **덮어쓰기 전에 보존하고 크게 알린다.**
    """
    p = path or PEAKS_PATH
    if not p.exists():
        return {"_schema": SCHEMA, "KR": {}, "US": {}}, None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("최상위가 dict가 아니다")
    except (json.JSONDecodeError, OSError, ValueError) as e:
        aside = p.with_name(f"{p.stem}.corrupt_{datetime.now(KST):%y%m%d_%H%M%S}.json")
        try:
            p.rename(aside)
            note = f"{aside.name}로 옮겨 보존했다"
        except OSError:
            note = "보존도 실패했다"
        return ({"_schema": SCHEMA, "KR": {}, "US": {}},
                f"고점 원장을 읽을 수 없다({e}) — {note}. 트레일링 스톱의 고점이 "
                f"오늘부터 다시 쌓인다(손절선 판정은 영향 없다).")
    if d.get("_schema") == SCHEMA:
        for m in MARKETS:
            d.setdefault(m, {})
        return d, None
    # 구버전(평평) 이관 — 값 형태가 맞는 항목만 옮긴다.
    out = {"_schema": SCHEMA, "KR": {}, "US": {}}
    for k, v in d.items():
        if k.startswith("_") or k in MARKETS:
            continue
        if isinstance(v, dict) and "peak_price" in v:
            out[market_of(k)][k] = v
    # 이미 시장별로 담긴 부분이 섞여 있으면 그것도 흡수한다.
    for m in MARKETS:
        if isinstance(d.get(m), dict):
            out[m].update({k: v for k, v in d[m].items()
                           if isinstance(v, dict) and "peak_price" in v})
    return out, None


def save(d: dict, path: Path = None) -> None:
    p = path or PEAKS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def for_market(market: str, path: Path = None) -> dict:
    """그 시장의 {티커: 레코드}. 읽기 실패는 stderr로 알린다."""
    d, err = load(path)
    if err:
        print(f"★ {err}", file=sys.stderr)
    return d.get(str(market).upper(), {})


def update(market: str, positions: list, path: Path = None) -> dict:
    """그 시장의 고점만 갱신한다. **다른 시장 칸은 손대지 않는다.**"""
    market = str(market).upper()
    d, err = load(path)
    if err:
        print(f"★ {err}", file=sys.stderr)
    book = d.setdefault(market, {})
    now = datetime.now(KST).isoformat()
    held = set()
    for pos in positions:
        t = pos.get("ticker")
        price = pos.get("price") or 0
        if not t or price <= 0:
            continue
        held.add(t)
        prev = (book.get(t) or {}).get("peak_price", 0)
        if price > prev:
            book[t] = {"peak_price": price, "peak_at": now}
        else:
            book.setdefault(t, {"peak_price": price, "peak_at": now})
    # 판 종목은 그 시장 안에서만 지운다(재매수 시 새로 시작).
    for gone in set(book) - held:
        book.pop(gone, None)
    save(d, path)
    return book


if __name__ == "__main__":
    d, err = load()
    if err:
        print(f"★ {err}", file=sys.stderr)
    print(f"■ 고점 원장 (schema {d.get('_schema')})")
    for m in MARKETS:
        book = d.get(m, {})
        print(f"\n  {m} — {len(book)}종목")
        for t, v in sorted(book.items()):
            print(f"    {t:8s} 고점 {v.get('peak_price'):>12,.2f}  {v.get('peak_at','')[:19]}")
