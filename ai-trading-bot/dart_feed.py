#!/usr/bin/env python3
"""
DART 공시 피드 — 국내 종목의 최근 공시를 재료에 붙인다.

뉴스레터만으로는 개별 종목 재료가 얇다(현재 구독 중 시장 관련은 머니레터 1종).
공시는 Tier 1 원천이고 종목 단위로 정확히 붙으므로, 분석의 사실 기반을 여기서 보강한다.

키가 없으면 조용히 빈 결과를 주는 게 아니라 **"미수집"임을 명시**해 돌려준다 —
'못 찾음'과 '없음'을 구분하는 것은 이 프로젝트 전체의 규율이다.

corp_code(고유번호)는 종목코드와 다르다. 전체 목록이 zip으로만 제공되므로 1회 받아
watchlist 종목만 추려 config/dart_corp_codes.json에 캐시한다.

사용:
    python3 dart_feed.py --refresh-codes     # corp_code 캐시 생성/갱신
    python3 dart_feed.py --days 3            # 최근 공시 조회(재료용 마크다운 출력)
"""
import argparse
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).parent
CONFIG_DIR = HERE / "config"
CODES_PATH = CONFIG_DIR / "dart_corp_codes.json"
WATCHLIST_PATH = CONFIG_DIR / "watchlist.json"
ENV_CANDIDATES = [HERE / ".env", HERE.parent / ".env"]

KST = timezone(timedelta(hours=9))
BASE = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

# OpenDART status 코드. 013(조회된 데이터 없음)은 오류가 아니라 정상적인 빈 결과다.
STATUS_OK, STATUS_EMPTY = "000", "013"


def load_env() -> dict:
    env = {}
    for path in [p for p in ENV_CANDIDATES if p.exists()]:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip("'\""))
    return env


def api_key() -> str:
    return load_env().get("DART_API_KEY", "")


def _get(path: str, params: dict, timeout: int = 30) -> bytes:
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ai-trading-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------- corp_code 캐시

ALL_CODES_PATH = CONFIG_DIR / "dart_corp_codes_all.json"


def refresh_codes(key: str, scope: str = "universe") -> dict:
    """전체 고유번호 zip을 받아 캐시한다. `scope="universe"`는 watchlist KR만,
    `"all"`은 **상장 종목 전체**(stock_code가 있는 것) — 자사주 스크린이 쓴다.

    ★ 왜 둘로 나누는가 — 유니버스 캐시는 매 run 공시 조회가 쓰고(작고 빠름),
    전체 캐시는 주 1회 스크린이 쓴다(2,700여 건). zip은 원래 전체라 필터만 다르다.
    """
    if scope == "all":
        wanted = None
    else:
        watchlist = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        wanted = {e["ticker"]: e.get("name", "") for e in watchlist.get("KR", []) if e.get("ticker")}
        if not wanted:
            print("watchlist KR이 비어 있다 — 캐시할 것이 없다.", file=sys.stderr)
            return {}

    raw = _get("corpCode.xml", {"crtfc_key": key}, timeout=90)
    # 실패 시 zip이 아니라 XML 오류 문서가 온다.
    if not raw[:2] == b"PK":
        try:
            status = ElementTree.fromstring(raw.decode("utf-8")).findtext("status")
            message = ElementTree.fromstring(raw.decode("utf-8")).findtext("message")
        except (ElementTree.ParseError, UnicodeDecodeError):
            status, message = "?", raw[:200]
        raise RuntimeError(f"corpCode 내려받기 실패 [{status}] {message}")

    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(z.namelist()[0])
    root = ElementTree.fromstring(xml)

    out = {}
    for node in root.iter("list"):
        stock = (node.findtext("stock_code") or "").strip()
        if not stock:
            continue
        if wanted is None or stock in wanted:
            out[stock] = {
                "corp_code": (node.findtext("corp_code") or "").strip(),
                "corp_name": (node.findtext("corp_name") or "").strip(),
            }
    target = ALL_CODES_PATH if wanted is None else CODES_PATH
    CONFIG_DIR.mkdir(exist_ok=True)
    target.write_text(json.dumps(
        {"updated_at": datetime.now(KST).isoformat(), "scope": scope, "codes": out},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"corp_code 캐시({scope}): {len(out)}종목 → {target}")
    if wanted is not None:
        missing = set(wanted) - set(out)
        if missing:
            print(f"  매핑 실패(미확인): {', '.join(sorted(missing))}", file=sys.stderr)
    return out


def load_all_codes(key: str = "") -> dict:
    """상장 전체 corp_code. 캐시가 없거나 30일 지났으면 새로 받는다."""
    if ALL_CODES_PATH.exists():
        try:
            d = json.loads(ALL_CODES_PATH.read_text(encoding="utf-8"))
            upd = datetime.fromisoformat(d.get("updated_at", "2000-01-01T00:00:00+09:00"))
            if (datetime.now(KST) - upd).days < 30 and d.get("codes"):
                return d["codes"]
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return refresh_codes(key or api_key(), scope="all")


# ---------------------------------------------------------- 정기보고서 주요정보
def _api_json(path: str, params: dict) -> tuple:
    """DART JSON API 한 번. 반환 (list|None, 오류문자열)."""
    try:
        res = json.loads(_get(path, params).decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None, str(e)[:120]
    st = str(res.get("status", ""))
    if st == STATUS_EMPTY:
        return [], ""
    if st != STATUS_OK:
        return None, f"[{st}] {res.get('message', '')}"
    return res.get("list") or [], ""


def stock_totals(key: str, corp_code: str, year: str, reprt: str) -> tuple:
    """주식의 총수 현황(`stockTotqySttus`) — 발행주식·자기주식·유통주식. 반환 (rows, err)."""
    return _api_json("stockTotqySttus.json", {"crtfc_key": key, "corp_code": corp_code,
                                              "bsns_year": year, "reprt_code": reprt})


def treasury_status(key: str, corp_code: str, year: str, reprt: str) -> tuple:
    """자기주식 취득·처분 현황(`tesstkAcqsDspsSttus`). 반환 (rows, err)."""
    return _api_json("tesstkAcqsDspsSttus.json", {"crtfc_key": key, "corp_code": corp_code,
                                                  "bsns_year": year, "reprt_code": reprt})


def load_codes() -> dict:
    data = json.loads(CODES_PATH.read_text(encoding="utf-8")) if CODES_PATH.exists() else {}
    return data.get("codes", {})


# ------------------------------------------------------------------ 공시 조회

def recent_filings(tickers: list, days: int = 3) -> dict:
    """반환: {status, filings:[...], skipped_reason, errors}. 키 없으면 skipped."""
    key = api_key()
    if not key:
        return {"status": "skipped", "filings": [],
                "skipped_reason": "DART_API_KEY 없음", "errors": {}}

    codes = load_codes()
    if not codes:
        try:
            codes = refresh_codes(key)
        except (RuntimeError, urllib.error.URLError, OSError) as e:
            return {"status": "error", "filings": [],
                    "skipped_reason": f"corp_code 캐시 실패: {e}", "errors": {}}

    end = datetime.now(KST)
    begin = end - timedelta(days=days)
    filings, errors = [], {}

    for ticker in tickers:
        meta = codes.get(ticker)
        if not meta:
            errors[ticker] = "corp_code 미매핑"
            continue
        try:
            raw = _get("list.json", {
                "crtfc_key": key, "corp_code": meta["corp_code"],
                "bgn_de": begin.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                "page_count": 20,
            })
            res = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            errors[ticker] = str(e)
            continue

        status = str(res.get("status", ""))
        if status == STATUS_EMPTY:
            continue
        if status != STATUS_OK:
            errors[ticker] = f"[{status}] {res.get('message', '')}"
            continue

        for item in res.get("list", []) or []:
            filings.append({
                "ticker": ticker,
                "corp_name": item.get("corp_name", meta.get("corp_name", "")),
                "date": item.get("rcept_dt", ""),
                "title": (item.get("report_nm", "") or "").strip(),
                "filer": item.get("flr_nm", ""),
                "url": VIEWER + (item.get("rcept_no", "") or ""),
            })

    filings.sort(key=lambda f: (f["date"], f["ticker"]), reverse=True)
    # ★ 전 종목이 실패했는데 `status: ok` + `filings: []`를 돌려주면, 읽는 쪽은
    #   **'공시가 없었다'로 읽는다.** 실패를 데이터 없음으로 바꾸는 전형적인 자리다.
    #   요청한 종목 전부가 실패했으면 그것은 빈 결과가 아니라 **실패**다.
    asked = len(tickers)
    if asked and len(errors) >= asked and not filings:
        return {"status": "error", "filings": [],
                "skipped_reason": f"요청한 {asked}종목 전부 조회 실패 — '공시 없음'이 아니라 "
                                  f"'확인 안 됨'이다",
                "errors": errors, "asked": asked, "failed": len(errors)}
    return {"status": "ok", "filings": filings, "skipped_reason": "",
            "errors": errors, "asked": asked, "failed": len(errors)}


def to_markdown(result: dict) -> str:
    """ingest.py가 material에 그대로 끼워 넣는 섹션."""
    L = ["## 최근 공시 (DART)", ""]
    if result["status"] == "skipped":
        L += [f"*(미수집 — {result['skipped_reason']}. '공시 없음'이 아니라 '확인 안 됨'이다.)*", ""]
        return "\n".join(L)
    if result["status"] == "error":
        L += [f"*(수집 실패 — {result['skipped_reason']})*", ""]
        return "\n".join(L)

    n_fail = len(result.get("errors") or {})
    asked = result.get("asked")
    if not result["filings"] and n_fail:
        # 일부라도 실패했으면 '없음'이라고 단정하지 않는다 — 못 본 종목에 공시가 있을 수 있다.
        L += [f"*(공시 0건 — 단 **{n_fail}종목은 조회 실패**"
              + (f" / 요청 {asked}종목" if asked else "")
              + ". 그 종목들은 '공시 없음'이 아니라 **확인 안 됨**이다.)*", ""]
    elif not result["filings"]:
        L += ["*(조회 기간 내 공시 없음 — DART가 빈 결과를 반환)*", ""]
    else:
        for f in result["filings"]:
            d = f["date"]
            pretty = f"{d[4:6]}/{d[6:8]}" if len(d) == 8 else d
            L.append(f"- [{pretty}] **{f['corp_name']}**({f['ticker']}): "
                     f"[{f['title']}]({f['url']})")
        L.append("")
    if result["errors"]:
        L += ["### 공시 조회 실패 (미확인)", ""]
        L += [f"- {t}: {e}" for t, e in result["errors"].items()] + [""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="DART 최근 공시 피드")
    ap.add_argument("--refresh-codes", action="store_true", help="corp_code 캐시를 새로 만든다")
    ap.add_argument("--days", type=int, default=3)
    args = ap.parse_args()

    key = api_key()
    if args.refresh_codes:
        if not key:
            print("DART_API_KEY가 .env에 없다. https://opendart.fss.or.kr 에서 무료 발급.",
                  file=sys.stderr)
            return 2
        refresh_codes(key)
        return 0

    watchlist = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    tickers = [e["ticker"] for e in watchlist.get("KR", []) if e.get("ticker")]
    print(to_markdown(recent_filings(tickers, args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
