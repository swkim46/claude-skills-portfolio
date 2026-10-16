#!/usr/bin/env python3
"""dart-analysis · 데이터 수집 모듈 (★ 유일하게 네트워크에 의존)
────────────────────────────────────────────────────────────────────
DART Open API에서 기업 식별 + 정형 재무제표(fnlttSinglAcntAll)를 수집해 raw JSON 출력.

설계:
  - 표준 라이브러리(urllib)만 사용 → 2단계(claude.ai 챗) 이식성 확보.
  - 네트워크 호출은 전부 이 파일에 격리. 2단계엔 이 수집부만 교체.
  - 키 로딩은 load_api_key() 한 곳으로 추상화(1단계 .env → 2단계 input 교체 지점).

Phase 0 안전장치:
  - --reprt 로 보고서 종류 선택(연간 11011 기본 / 반기 11012 / 1분기 11013 / 3분기 11014).
  - 금융업·지주사 감지: 핵심계정(매출액·영업이익) 부재 시 warning 부여(표준분석 부적합).

사용:
  python3 collect.py --ident "삼성전자"            --out raw.json
  python3 collect.py --ident 005930                --out raw.json   # 종목코드(6)
  python3 collect.py --ident 00126380              --out raw.json   # 고유번호(8)
  python3 collect.py --ident "현대"  --pick 2      --out raw.json   # 동명 후보 선택
  python3 collect.py --ident "삼성전자" --reprt 11012 --out raw.json # 반기

raw JSON 스키마:
  {company:{...}, fs_div, currency, rcept_no, reprt_code, warning,
   years:[전전기,전기,당기], accounts:[{sj_div,sj_nm,account_nm,values:{year:float|null}}]}
"""
import argparse, io, json, os, sys, time, zipfile
import xml.etree.ElementTree as ET
import urllib.request, urllib.parse
from datetime import datetime

API = "https://opendart.fss.or.kr/api"
CACHE_DIR = os.path.expanduser("~/.cache/dart-analysis")

# 보고서 종류 코드 → 라벨
REPRT = {"11011": "사업보고서(연간)", "11012": "반기보고서", "11013": "1분기보고서", "11014": "3분기보고서"}


def load_api_key():
    """API 키 로딩. 1단계: 환경변수/.env의 DART_API_KEY.
    (2단계 챗 이식 시 이 함수만 대화 input 방식으로 교체)"""
    key = os.environ.get("DART_API_KEY")
    if key:
        return key.strip()
    here = os.getcwd()
    for _ in range(6):
        envp = os.path.join(here, ".env")
        if os.path.exists(envp):
            for line in open(envp, encoding="utf-8"):
                if line.strip().startswith("DART_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    sys.exit("ERROR: DART_API_KEY를 찾을 수 없음. repo 루트 .env에 'DART_API_KEY=...' 설정.")


def _get(path, params, binary=False):
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        data = r.read()
    return data if binary else json.loads(data.decode("utf-8"))


def load_corp_map(key):
    """corpCode.xml(전체 기업 매핑)을 7일 캐시로 로드 → [{corp_code,corp_name,stock_code}]."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, "corpcode.xml")
    fresh = os.path.exists(cache) and (time.time() - os.path.getmtime(cache) < 7 * 86400)
    if not fresh:
        raw = _get("corpCode.xml", {"crtfc_key": key}, binary=True)
        zf = zipfile.ZipFile(io.BytesIO(raw))
        with open(cache, "wb") as f:
            f.write(zf.read(zf.namelist()[0]))
    root = ET.parse(cache).getroot()
    out = []
    for li in root.iter("list"):
        out.append({
            "corp_code": (li.findtext("corp_code") or "").strip(),
            "corp_name": (li.findtext("corp_name") or "").strip(),
            "stock_code": (li.findtext("stock_code") or "").strip(),
        })
    return out


def company_info(corp_code, key):
    try:
        d = _get("company.json", {"crtfc_key": key, "corp_code": corp_code})
        return d if d.get("status") == "000" else {}
    except Exception:
        return {}


def identify(corps, ident, key, pick=None):
    """입력을 타입 분기해 corp_code 확정. 동명 다수면 후보 제시 후 종료(--pick 유도)."""
    ident = ident.strip()
    if ident.isdigit() and len(ident) == 8:                 # 고유번호
        return ident
    if ident.isdigit() and len(ident) == 6:                 # 종목코드
        m = [c for c in corps if c["stock_code"] == ident]
        if m:
            return m[0]["corp_code"]
        sys.exit(f"종목코드 {ident} 해당 기업 없음.")
    exact = [c for c in corps if c["corp_name"] == ident]   # 회사명: 정확 우선
    cands = exact or [c for c in corps if ident in c["corp_name"]]
    cands.sort(key=lambda c: (c["stock_code"] == "", len(c["corp_name"])))  # 상장·짧은이름 우선
    if not cands:
        sys.exit(f"'{ident}' 검색 결과 없음.")
    if len(cands) == 1:
        return cands[0]["corp_code"]
    if pick is not None:
        if not (1 <= pick <= len(cands)):
            sys.exit(f"--pick 범위 오류(1~{len(cands)}).")
        return cands[pick - 1]["corp_code"]
    # 동명 다수 → 업종·대표자 보강해 제시 (★ 잘못 식별 = 분석 전체 오염)
    print(f"\n'{ident}' 후보 {len(cands)}건 — 아래에서 골라 --pick N 으로 다시 실행:", file=sys.stderr)
    for i, c in enumerate(cands[:20], 1):
        info = company_info(c["corp_code"], key)
        listed = f"종목 {c['stock_code']}" if c["stock_code"] else "비상장"
        print(f"  {i}. {c['corp_name']} | {listed} | 고유 {c['corp_code']} | "
              f"대표 {info.get('ceo_nm', '?')} | 업종코드 {info.get('induty_code', '?')}", file=sys.stderr)
    sys.exit("→ 모호하므로 임의 선택하지 않음. --pick N 으로 확정해 주세요.")


def fetch_fs(corp_code, year, key, fs_div="CFS", reprt="11011"):
    return _get("fnlttSinglAcntAll.json", {
        "crtfc_key": key, "corp_code": corp_code, "bsns_year": str(year),
        "reprt_code": reprt, "fs_div": fs_div})


def _base_years(end, start):
    """[start..end] 전체를 덮는 base-year 목록(내림차순). 한 호출이 base·base-1·base-2 3개년 제공."""
    bases, b = [], end
    while True:
        bases.append(b)
        if b - 2 <= start:
            break
        b -= 3
    return bases


def _num(s):
    s = (s or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _detect_warning(accounts):
    """금융업·지주사·특수구조 감지: 표준 손익 핵심계정(매출/영업이익) 부재 시 경고.
    (은행·증권·보험은 '매출액'이 없고 영업수익/이자수익 구조라 자동으로 걸림)"""
    nms = {(a["account_nm"] or "").replace(" ", "") for a in accounts}
    has_sales = any(s in nms for s in ("매출액", "수익(매출액)", "영업수익", "매출", "매출및지분법손익"))
    has_op = any("영업이익" in n for n in nms)
    if not (has_sales and has_op):
        return ("표준 손익 핵심계정(매출액·영업이익) 부재 — 은행·증권·보험 등 금융업이거나 "
                "지주사·특수 구조일 수 있음. 1단계 표준 재무분석 대상 아님. "
                "FI/INS PA 전문가를 통해 별도 고도화 필요.")
    return None


def _extract_items(items, base):
    """API list(한 호출=base·base-1·base-2 3개년) → {(sj_div, account_nm): {sj_nm, values:{year:v}}} + 출처/통화."""
    y0, y1, y2 = base, base - 1, base - 2
    rcept, currency, out = None, "KRW", {}
    for it in items:
        rcept = rcept or it.get("rcept_no")
        currency = it.get("currency") or currency
        nm = (it.get("account_nm") or "").strip()
        key = (it.get("sj_div"), nm)
        d = out.setdefault(key, {"sj_nm": it.get("sj_nm"), "values": {}})
        for y, fld in ((y0, "thstrm_amount"), (y1, "frmtrm_amount"), (y2, "bfefrmtrm_amount")):
            d["values"][str(y)] = _num(it.get(fld))
    return out, rcept, currency


def collect(ident, year=None, fs_div="CFS", pick=None, reprt="11011", years_n=3):
    """정형 재무제표 수집. years_n개년(기본 3)을 base-year 타일링으로 병합.
    한 호출이 3개년만 주므로 3 초과면 base를 3씩 내려 여러 번 호출 후 병합."""
    key = load_api_key()
    corps = load_corp_map(key)
    corp_code = identify(corps, ident, key, pick)
    info = company_info(corp_code, key)
    end = year if year else datetime.now().year - 1   # 사업보고서는 익년 3월 제출 → 기본 작년

    # 종료연도 확정: 연결 시도 → 별도 폴백 → 최신 미제출이면 1년 내려 재시도
    def _probe(yr):
        data = fetch_fs(corp_code, yr, key, fs_div, reprt)
        if data.get("status") == "000":
            return yr, fs_div, data
        if fs_div == "CFS":
            alt = fetch_fs(corp_code, yr, key, "OFS", reprt)
            if alt.get("status") == "000":
                return yr, "OFS", alt
        return None
    probe = _probe(end) or _probe(end - 1)
    if not probe:
        sys.exit(f"재무제표 조회 실패: {corp_code} {end}년 {reprt} 데이터 없음.")
    end, used_fs, first_data = probe
    start = end - years_n + 1

    # base-year 타일링: end 호출은 probe 결과 재사용, 나머지만 추가 호출
    merged, rcept_map, currency = {}, {}, "KRW"
    for b in _base_years(end, start):
        data = first_data if b == end else fetch_fs(corp_code, b, key, used_fs, reprt)
        if data.get("status") != "000":
            continue                                  # 오래된 base 누락 → 해당 연도 공란 처리
        ex, rcept, cur = _extract_items(data.get("list", []), b)
        currency = cur or currency
        if rcept:
            rcept_map[str(b)] = rcept
        for k, v in ex.items():
            m = merged.setdefault(k, {"sj_nm": v["sj_nm"], "values": {}})
            for yr, val in v["values"].items():
                if m["values"].get(yr) is None:       # 먼저 채운 값(당기 우선) 유지
                    m["values"][yr] = val

    years = [str(y) for y in range(start, end + 1)]
    accounts = [{
        "sj_div": sj_div, "sj_nm": v["sj_nm"], "account_nm": nm,
        "values": {y: v["values"].get(y) for y in years},
    } for (sj_div, nm), v in merged.items()]
    rcepts = [rcept_map[k] for k in sorted(rcept_map)]
    return {
        "company": {
            "corp_code": corp_code, "corp_name": info.get("corp_name", ""),
            "stock_code": info.get("stock_code", ""), "ceo_nm": info.get("ceo_nm", ""),
            "corp_cls": info.get("corp_cls", ""), "induty_code": info.get("induty_code", ""),
        },
        "fs_div": used_fs, "reprt_code": reprt, "currency": currency,
        "rcept_no": rcepts[-1] if rcepts else None, "rcept_nos": rcepts,
        "rcept_map": rcept_map,                       # {base연도: rcept} — 경로B 다년 매핑용(peer)
        "warning": _detect_warning(accounts),
        "years": years, "accounts": accounts,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="DART 재무제표 수집 → raw JSON")
    ap.add_argument("--ident", required=True, help="회사명 | 종목코드(6) | 고유번호(8)")
    ap.add_argument("--year", type=int, default=None, help="종료 사업연도(기본: 작년)")
    ap.add_argument("--years", type=int, default=3, help="수집 연수(기본 3 / 비교는 5 권장)")
    ap.add_argument("--fs", default="CFS", choices=["CFS", "OFS"], help="연결 CFS / 별도 OFS")
    ap.add_argument("--reprt", default="11011", choices=list(REPRT),
                    help="보고서: 11011 연간(기본) / 11012 반기 / 11013 1분기 / 11014 3분기")
    ap.add_argument("--pick", type=int, default=None, help="동명 후보 선택 번호")
    ap.add_argument("--key", default=None, help="DART 인증키 직접 전달(챗 대화 input용)")
    ap.add_argument("--out", default=None, help="출력 JSON 경로(미지정 시 stdout)")
    a = ap.parse_args()
    if a.key:
        os.environ["DART_API_KEY"] = a.key   # 챗: .env 대신 대화 input → 환경변수 주입
    raw = collect(a.ident, a.year, a.fs, a.pick, a.reprt, a.years)
    text = json.dumps(raw, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        c = raw["company"]
        print(f"✅ 저장: {a.out}")
        src = raw.get("rcept_nos") or ([raw["rcept_no"]] if raw.get("rcept_no") else [])
        print(f"   {c['corp_name']}(종목 {c['stock_code'] or '-'} / 고유 {c['corp_code']}) "
              f"· {raw['fs_div']} · {REPRT.get(raw['reprt_code'], raw['reprt_code'])} "
              f"· {raw['years'][0]}–{raw['years'][-1]}({len(raw['years'])}개년) · 출처 {len(src)}건 {', '.join(src)}")
        if raw["warning"]:
            print(f"   ⚠️ {raw['warning']}")
    else:
        print(text)
