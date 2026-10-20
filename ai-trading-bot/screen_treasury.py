#!/usr/bin/env python3
"""자사주 소각 창 스크린 — **법이 행동을 강제하는 자리**를 공시보다 먼저 본다.

왜 있는가. 2026-03-06 시행 상법 제341조의4는 회사가 자기주식을 취득한 날부터 **1년 안에 소각**하도록
강제하고, 시행 전에 취득한 자기주식은 **시행일 + 6개월(2026-09-06)부터 1년 안에** 소각해야 한다
(법무부 길라잡이 2026-03-11). 소각은 발행주식 수를 줄여 1주 몫을 키우므로 공시 당일 값이 뛴다 —
*실사례: 샘표가 9/9 자사주 29.92% 전량 소각을 공시하고 9/10 상한가(+29.99%).*
그런데 **누가 소각해야 하는지는 법이 이미 정해 두었다** — 자사주 비율이 높은 회사다. 공시가 뉴스이고,
이 스크린이 공시보다 앞선다. 이것이 "리서치가 뉴스보다 앞서는" 유형 중 가장 구조적인 것이다.

정직한 한계 — 이 스크린은 *누가*를 알지 *언제*를 모른다(창이 1년이고 주총 승인으로 보유를 이어갈
예외도 있다). 그래서 산출은 매수가 아니라 **전망·논지 후보**다. 후보는 `universe_apply.py`의 규칙
(KOSPI200·시총·유동성)을 그대로 거친다 — 스크린이 유니버스 파일을 직접 쓰지 않는다.

사용:
    python3 screen_treasury.py                       # 캐시 갱신 + 스크린 (주 1회)
    python3 screen_treasury.py --min-ratio 5 --top 30
    python3 screen_treasury.py --limit 50            # 시험용 — 앞 50종목만 조회
출력: analysis/스크린_자사주소각_<YYMMDD>.md · 저장소 co_treasury_holding(캐시 — 예전 data/treasury_scan.json) ·
      signals/candidates_treasury_<YYMMDD>.json(watchlist_candidates 형식)

저장(2026-10-17): 조회 결과는 저장소 한 표(`co_treasury_holding` · 키 시장·종목·결산일)에 행 단위로 쌓는다 — `db.treasury_latest`가
종목별 최근 행을, `db.treasury_put`이 한 행을 쓴다. 조회 실패는 결산일 ''·`error` 칸에 사유로 남고 30일 뒤 다시 조회한다(예전과 같다).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dart_feed as d
import db                            # 저장소를 여는 것은 db.py뿐이다

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
MARKET = "KR"                        # 상법 341조의4 스크린 — 국내 상장사만
CACHE_DAYS = 90                      # 정기보고서는 분기마다 바뀐다 — 그보다 자주 부를 이유가 없다
LAW_EFFECTIVE = "2026-03-06"
GRACE_START = "2026-09-06"           # 기존 자사주 소각 창 시작(시행일 + 6개월)
GRACE_END = "2027-09-05"             # 그로부터 1년
PERIODS = (("2026", "11012", "2026 반기"), ("2026", "11013", "2026 1분기"), ("2025", "11011", "2025 사업"))


def _int(s) -> int:
    try:
        return int(str(s).replace(",", "").strip())
    except ValueError:
        return 0


def load_cache() -> dict:
    """종목별 최근 스냅숏 {종목: 행} — 저장소 `co_treasury_holding`(`db.treasury_latest`). 예전 캐시 파일과 같은 모양으로
    돌려준다(빈 칸은 뺀다 · 조회 실패 행은 `error` 칸이 있다)."""
    out = {}
    for tk, r in db.treasury_latest(MARKET).items():
        row = {k: v for k, v in dict(r).items() if v not in (None, "") and k != "market"}
        row["ticker"] = tk
        out[tk] = row
    return out


def save_row(r: dict) -> None:
    """한 종목 결과를 저장소에 한 행으로(같은 결산일이면 덮어쓴다) — 조회 실패는 결산일 ''·error 칸."""
    db.treasury_put({"market": MARKET, **r})


def scan_one(key: str, ticker: str, meta: dict) -> dict:
    """한 회사의 최신 주식총수 현황. 반기 → 1분기 → 사업보고서 순으로 있는 것을 쓴다."""
    for year, rc, label in PERIODS:
        rows, err = d.stock_totals(key, meta["corp_code"], year, rc)
        if rows is None:
            return {"ticker": ticker, "name": meta["corp_name"], "error": err}
        if not rows:
            continue
        common = next((r for r in rows if r.get("se") == "보통주"), None) or \
                 next((r for r in rows if r.get("se") == "합계"), None)
        if not common:
            continue
        issued = _int(common.get("istc_totqy"))
        treasury = _int(common.get("tesstk_co"))
        if issued <= 0:
            continue
        return {"ticker": ticker, "name": meta["corp_name"],
                "corp_cls": common.get("corp_cls", ""), "period": label,
                "stlm_dt": common.get("stlm_dt", ""), "issued": issued,
                "treasury": treasury, "ratio_pct": round(treasury / issued * 100, 2),
                "rcept_no": common.get("rcept_no", "")}
    return {"ticker": ticker, "name": meta["corp_name"], "error": "정기보고서에 주식총수 없음"}


def scan(key: str, codes: dict, limit: int = 0, sleep: float = 0.06) -> tuple:
    """전 종목 조회(캐시 우선). 반환 (결과 dict, 새로 부른 수, 실패 수)."""
    cache = load_cache()
    cutoff = (datetime.now(KST) - timedelta(days=CACHE_DAYS)).strftime("%Y-%m-%d")
    items = sorted(codes.items())
    if limit:
        items = items[:limit]
    n_call = n_fail = 0
    for i, (tk, meta) in enumerate(items, 1):
        hit = cache.get(tk)
        # 실패도 30일은 캐시한다 — 정기보고서가 없는 회사(신규상장·결산기 상이)는 매주 다시
        # 불러도 같은 답이다. 매번 ~1,000콜을 낭비하면 한도(1만/일)의 10%가 사라진다.
        err_cut = (datetime.now(KST) - timedelta(days=30)).strftime("%Y-%m-%d")
        if hit and ((not hit.get("error") and hit.get("scanned", "") >= cutoff)
                    or (hit.get("error") and hit.get("scanned", "") >= err_cut)):
            continue
        r = scan_one(key, tk, meta)
        r["scanned"] = datetime.now(KST).strftime("%Y-%m-%d")
        cache[tk] = r
        save_row(r)                      # 행마다 바로 쓴다 — 중간에 끊겨도 받은 만큼은 남는다(예전 200건마다 저장)
        n_call += 1
        n_fail += 1 if r.get("error") else 0
        if n_call % 200 == 0:
            print(f"  … {i}/{len(items)} 조회 (호출 {n_call} · 실패 {n_fail})", file=sys.stderr)
        time.sleep(sleep)
    return cache, n_call, n_fail


def universe_gate(rules: dict, r: dict, price) -> str:
    """유니버스 규칙을 **미리** 대조해 적는다 — 후보가 왜 안 들어갈지를 스크린이 먼저 말한다."""
    kr = rules.get("KR") or {}
    why = []
    if r.get("corp_cls") != "Y":
        why.append("코스피 아님")
    if price and r.get("issued"):
        cap_100m = price * r["issued"] / 1e8
        if cap_100m < (kr.get("min_market_cap_100m_krw") or 0):
            why.append(f"시총 {cap_100m:,.0f}억 < {kr.get('min_market_cap_100m_krw'):,}억")
    if "KOSPI200" in (kr.get("require_market") or []):
        why.append("KOSPI200 여부는 편입 시 KIS로 확인")
    return " · ".join(why) if why else "규칙 통과 가능"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-ratio", type=float, default=5.0, help="자사주/발행주식 %% 하한")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--limit", type=int, default=0, help="시험용 — 앞 N종목만")
    ap.add_argument("--stamp", default="")
    ap.add_argument("--no-price", action="store_true", help="시총 계산용 KIS 조회를 건너뛴다")
    a = ap.parse_args()

    key = d.api_key()
    if not key:
        print("DART_API_KEY 없음 — 스크린 불가", file=sys.stderr)
        return 2
    codes = d.load_all_codes(key)
    print(f"상장 {len(codes)}종목 · 캐시 {CACHE_DAYS}일 · 소각 창 {GRACE_START} ~ {GRACE_END}")
    cache, n_call, n_fail = scan(key, codes, a.limit)
    rows = [r for r in cache.values() if not r.get("error") and r.get("ratio_pct", 0) >= a.min_ratio]
    rows.sort(key=lambda r: -r["ratio_pct"])
    rows = rows[:a.top]

    rules = json.loads((HERE / "config" / "universe_rules.json").read_text(encoding="utf-8"))
    prices = {}
    if rows and not a.no_price:
        try:
            from kis_client import KisClient, KisError
            c = KisClient.for_mode()
            for r in rows:
                try:
                    prices[r["ticker"]] = float(c.domestic_price(r["ticker"]).get("price") or 0)
                except KisError as e:
                    prices[r["ticker"]] = None
        except Exception as e:                    # noqa: BLE001
            print(f"  시세 조회 불가({type(e).__name__}) — 시총 없이 낸다", file=sys.stderr)

    if not a.stamp:
        import sessions as _ss
        a.stamp = _ss.run_stamp("kr")          # 국내 도구 — 현재 국내 run 스탬프(현지 거래일)
    st = a.stamp
    n_err = sum(1 for r in cache.values() if r.get("error"))
    L = [f"# 자사주 소각 창 스크린 — {st}", "",
         f"> 근거: 상법 제341조의4(2026-03-06 시행) — 취득 1년 내 소각. 기존 자사주는 "
         f"**{GRACE_START}부터 1년 내**. 지금은 창의 {(datetime.now(KST) - datetime.fromisoformat(GRACE_START + 'T00:00:00+09:00')).days + 1}일째.",
         f"> 조회 {len(cache)}종목(이번 호출 {n_call} · 실패 누적 {n_err}) · 자사주 비율 ≥ {a.min_ratio}% 상위 {len(rows)}건.",
         "> **한계**: 누가 소각해야 하는지는 알지만 *언제* 공시할지는 모른다(창 1년 · 주총 승인 예외). "
         "그래서 매수가 아니라 **전망·논지 후보**다. 유니버스 편입은 `universe_apply.py` 규칙을 그대로 거친다.", "",
         "| # | 종목 | 자사주 비율 | 자사주 | 발행주식 | 기준 | 시장 | 시총(억) | 유니버스 규칙 |",
         "|---|---|---|---|---|---|---|---|---|"]
    cands = []
    for i, r in enumerate(rows, 1):
        p = prices.get(r["ticker"])
        cap = f"{p * r['issued'] / 1e8:,.0f}" if p else "—"
        gate = universe_gate(rules, r, p)
        L.append(f"| {i} | {r['ticker']} {r['name']} | **{r['ratio_pct']:.2f}%** | {r['treasury']:,} | "
                 f"{r['issued']:,} | {r['period']} | {'KOSPI' if r.get('corp_cls') == 'Y' else r.get('corp_cls', '?')} | "
                 f"{cap} | {gate} |")
        cands.append({"ticker": r["ticker"], "name": r["name"], "market": "KR",
                      "axis": "treasury_cancel_window",
                      "why": f"자사주 {r['ratio_pct']:.1f}% — 상법 341조의4 소각 창({GRACE_START}~) 안. "
                             f"기준 {r['period']}. 유니버스 규칙: {gate}",
                      "source_url": d.VIEWER + r.get("rcept_no", "") if r.get("rcept_no") else "",
                      "evidence": [{"claim": f"자기주식 {r['treasury']:,}주 / 발행 {r['issued']:,}주",
                                    "source_url": d.VIEWER + r.get("rcept_no", ""), "tier": "T1",
                                    "verdict": "MATCH"}]})
    L += ["", "## 전망으로 세울 때", "",
          "- 축 `treasury_cancel_window` — 방향 +, horizon은 창의 남은 기간(최대 52주), 1차 수혜 = 위 목록.",
          "- 2차 수혜: 소각이 잇따르면 **자사주 비율이 높은 지주사·중소형 가치주 전체의 재평가** — "
          "이건 통념일 수 있으니 첫 공시 3~5건의 주가 반응으로 확인한 뒤 적는다.",
          "- 반증: 주총 승인으로 보유를 이어가는 회사가 다수면 이 창은 뉴스가 안 된다."]
    out = HERE / "analysis" / f"스크린_자사주소각_{st}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    cpath = HERE / "signals" / f"candidates_treasury_{st}.json"
    cpath.write_text(json.dumps({"generated_at": datetime.now(KST).isoformat(),
                                 "source": "screen_treasury.py", "watchlist_candidates": cands},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(L[:6]))
    for line in L[6:6 + min(12, len(rows))]:
        print(line)
    print(f"\n→ {out}\n→ {cpath} (후보 {len(cands)}건 — universe_apply.py가 규칙으로 거른다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
