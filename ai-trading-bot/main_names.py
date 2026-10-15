#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""메인 종목 목록 — 시가총액 상위를 **브로커 시세로 재서** 고른다.

    python3 main_names.py --refresh          # 후보 풀의 시총을 조회해 config/main_names.json을 다시 쓴다
    python3 main_names.py --show             # 현재 목록

왜 있는가 (2026-10-02 사용자): "삼성전자 같은 메인 종목인데 그걸 캐치 못하는 게 말이 안 된다."
아쉬움 감사는 유니버스와 지도가 아는 이름만 쟀다. 시총 상위가 유니버스 밖이면 아예 감시 밖이었다.
그래서 국내·미국 각 상위 N을 **같이 잰다**(`regret.py --fetch`가 이 파일을 읽는다).

순위는 손으로 정하지 않는다 — 아래 후보 풀(넉넉한 상위)의 시가총액을 KIS 현재가 TR에서 읽어
정렬한다(국내 `hts_avls` 억원 · 미국 `tomv` USD). 풀은 순위가 아니라 **그물**이다: 풀 밖에서 새 대형주가
나오면 풀에 이름만 보탠다. 갱신은 분기마다 `trade-feedback`이 제안한다.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "config" / "main_names.json"
KST = timezone(timedelta(hours=9))
TOP_N = 30

# 후보 풀 — 순위가 아니라 그물. 상장 거래소는 주문용 코드(시세 조회 때 quote_excd로 바꾼다).
POOL_KR = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("373220", "LG에너지솔루션"), ("207940", "삼성바이오로직스"),
    ("005380", "현대차"), ("000270", "기아"), ("068270", "셀트리온"), ("105560", "KB금융"), ("055550", "신한지주"),
    ("035420", "NAVER"), ("028260", "삼성물산"), ("012450", "한화에어로스페이스"), ("329180", "HD현대중공업"),
    ("034020", "두산에너빌리티"), ("032830", "삼성생명"), ("012330", "현대모비스"), ("005490", "POSCO홀딩스"),
    ("006400", "삼성SDI"), ("051910", "LG화학"), ("086790", "하나금융지주"), ("035720", "카카오"),
    ("096770", "SK이노베이션"), ("009540", "HD한국조선해양"), ("138040", "메리츠금융지주"), ("000810", "삼성화재"),
    ("015760", "한국전력"), ("010130", "고려아연"), ("402340", "SK스퀘어"), ("316140", "우리금융지주"),
    ("011200", "HMM"), ("033780", "KT&G"), ("010140", "삼성중공업"), ("042660", "한화오션"), ("066570", "LG전자"),
    ("003550", "LG"), ("034730", "SK"), ("017670", "SK텔레콤"), ("030200", "KT"), ("018260", "삼성에스디에스"),
    ("259960", "크래프톤"), ("047810", "한국항공우주"), ("267260", "HD현대일렉트릭"), ("064350", "현대로템"),
    ("009150", "삼성전기"), ("010950", "S-Oil"), ("024110", "기업은행"), ("003670", "포스코퓨처엠"),
    ("086280", "현대글로비스"), ("000100", "유한양행"), ("298040", "효성중공업"), ("079550", "LIG넥스원"),
]
POOL_US = [
    ("AAPL", "Apple", "NASD"), ("MSFT", "Microsoft", "NASD"), ("NVDA", "NVIDIA", "NASD"), ("AMZN", "Amazon", "NASD"),
    ("GOOGL", "Alphabet", "NASD"), ("META", "Meta", "NASD"), ("AVGO", "Broadcom", "NASD"), ("TSLA", "Tesla", "NASD"),
    ("TSM", "TSMC", "NYSE"), ("LLY", "Eli Lilly", "NYSE"), ("JPM", "JPMorgan", "NYSE"), ("V", "Visa", "NYSE"),
    ("WMT", "Walmart", "NASD"), ("ORCL", "Oracle", "NYSE"), ("MA", "Mastercard", "NYSE"), ("XOM", "Exxon Mobil", "NYSE"),
    ("UNH", "UnitedHealth", "NYSE"), ("COST", "Costco", "NASD"), ("NFLX", "Netflix", "NASD"), ("HD", "Home Depot", "NYSE"),
    ("PG", "Procter & Gamble", "NYSE"), ("JNJ", "Johnson & Johnson", "NYSE"), ("BAC", "Bank of America", "NYSE"),
    ("ABBV", "AbbVie", "NYSE"), ("AMD", "AMD", "NASD"), ("PLTR", "Palantir", "NASD"), ("KO", "Coca-Cola", "NYSE"),
    ("CRM", "Salesforce", "NYSE"), ("CVX", "Chevron", "NYSE"), ("MU", "Micron", "NASD"), ("ASML", "ASML", "NASD"),
    ("CSCO", "Cisco", "NASD"), ("WFC", "Wells Fargo", "NYSE"), ("IBM", "IBM", "NYSE"), ("GE", "GE Aerospace", "NYSE"),
    ("AMAT", "Applied Materials", "NASD"), ("LRCX", "Lam Research", "NASD"), ("GS", "Goldman Sachs", "NYSE"),
    ("MS", "Morgan Stanley", "NYSE"), ("CAT", "Caterpillar", "NYSE"), ("INTC", "Intel", "NASD"), ("QCOM", "Qualcomm", "NASD"),
    ("TMUS", "T-Mobile US", "NASD"), ("PEP", "PepsiCo", "NASD"), ("AZN", "AstraZeneca", "NASD"), ("SAP", "SAP", "NYSE"),
    ("TXN", "Texas Instruments", "NASD"), ("GEV", "GE Vernova", "NYSE"), ("ANET", "Arista Networks", "NYSE"),
    ("APP", "AppLovin", "NASD"), ("MRK", "Merck", "NYSE"), ("ISRG", "Intuitive Surgical", "NASD"), ("LIN", "Linde", "NASD"),
    ("TM", "Toyota", "NYSE"), ("KLAC", "KLA", "NASD"), ("BABA", "Alibaba", "NYSE"),
]
ORDER_CODES = ("NASD", "NYSE", "AMEX")


def _kr_mcap(c, tk: str) -> float:
    res = c._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
                     headers=c._headers(c.tr["dom_price"]),
                     params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": tk})
    c._check(res, f"국내 현재가({tk})")
    return float((res.get("output") or {}).get("hts_avls") or 0)          # 억원


def _us_mcap(c, tk: str, excd: str) -> tuple:
    """(시총 USD, 실제로 응답한 주문용 거래소 코드). 상장 거래소가 바뀐 종목도 있으니 셋을 차례로 묻는다."""
    from kis_client import quote_excd
    for ex in [excd] + [e for e in ORDER_CODES if e != excd]:
        res = c._request("GET", "/uapi/overseas-price/v1/quotations/price-detail",
                         headers=c._headers("HHDFS76200200"),
                         params={"AUTH": "", "EXCD": quote_excd(ex), "SYMB": tk})
        o = res.get("output") or {}
        if str(o.get("last") or "").strip() and float(o.get("tomv") or 0) > 0:
            return float(o["tomv"]), ex
    return 0.0, excd


def refresh(top_n: int = TOP_N) -> dict:
    sys.path.insert(0, str(HERE))
    from kis_client import KisClient, KisError
    c = KisClient.for_mode()
    kr, us, errors = [], [], []
    for tk, nm in POOL_KR:
        try:
            v = _kr_mcap(c, tk)
            if v > 0:
                kr.append({"ticker": tk, "name": nm, "mcap_100m_krw": v})
            else:
                errors.append(f"KR {tk} {nm}: 시총 공백")
        except KisError as e:
            errors.append(f"KR {tk} {nm}: {str(e)[:60]}")
    for tk, nm, ex in POOL_US:
        try:
            v, ex2 = _us_mcap(c, tk, ex)
            if v > 0:
                us.append({"ticker": tk, "name": nm, "excd": ex2, "mcap_usd": v})
            else:
                errors.append(f"US {tk} {nm}: 세 거래소 모두 빈 응답")
        except KisError as e:
            errors.append(f"US {tk} {nm}: {str(e)[:60]}")
    kr.sort(key=lambda r: -r["mcap_100m_krw"]); us.sort(key=lambda r: -r["mcap_usd"])
    doc = {"as_of": datetime.now(KST).isoformat(timespec="seconds"),
           "source": "KIS 현재가 TR 시가총액 — 국내 inquire-price hts_avls(억원) · 미국 HHDFS76200200 tomv(USD)",
           "pool_size": {"KR": len(POOL_KR), "US": len(POOL_US)}, "top_n": top_n,
           "KR": kr[:top_n], "US": us[:top_n], "errors": errors}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return doc


def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"KR": [], "US": []}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--top", type=int, default=TOP_N)
    a = ap.parse_args()
    d = refresh(a.top) if a.refresh else load()
    print(f"■ 메인 종목 — as_of {d.get('as_of', '?')} · 상위 {d.get('top_n', '?')}")
    for mk, unit in (("KR", "mcap_100m_krw"), ("US", "mcap_usd")):
        rows = d.get(mk) or []
        print(f"  {mk} {len(rows)}: " + " · ".join(f"{r['ticker']} {r['name']}" for r in rows))
    if d.get("errors"):
        print("  조회 실패:", " / ".join(d["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
