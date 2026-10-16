#!/usr/bin/env python3
"""dart-analysis · 경로 B 수집: 사업보고서 원문(document.xml) 주석/세그먼트 파서
────────────────────────────────────────────────────────────────────
정형 API(fnlttSinglAcntAll)에 없는 세부 항목을 원문에서 추출.
  - parse_sga    : 판매비와관리비 명세(항목별 금액) — 정형적 표라 정밀 파싱
  - parse_revenue: 부문별 매출/영업이익/영업이익률 3개년 (영업부문 P&L 표)
                   표가 없으면 매출실적 섹션 텍스트로 폴백.

설계:
  - 네트워크 의존(document.xml) → collect 계열. 2단계 챗 이식 시 교체 대상.
  - document는 ~/.cache/dart-analysis/doc_<rcept>.txt 캐시(재사용).
  - DART 원문 셀 태그는 <TE>(비표준)라 셀 분해 대신 **행/섹션 텍스트 기반** 파싱.
  - 단위: 판관비 명세=백만원, 매출/부문=억원 (정형 API='원'과 다름 → 분석 시 통일 필요).
  - △(세모)는 음수(적자) 표기 → 부호 변환.

사용:
  python3 parse_notes.py --rcept 20260310002820 --what all --out notes.json
"""
import argparse, io, json, os, re, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import load_api_key, _get, CACHE_DIR

clean = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def get_document(rcept_no, key):
    """사업보고서 원문 전체 텍스트(캐시 우선)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"doc_{rcept_no}.txt")
    if os.path.exists(cache):
        return open(cache, encoding="utf-8").read()
    raw = _get("document.xml", {"crtfc_key": key, "rcept_no": rcept_no}, binary=True)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    full = "".join(zf.read(n).decode("utf-8", "ignore") for n in zf.namelist())
    open(cache, "w", encoding="utf-8").write(full)
    return full


def _amts(s, min_digits):
    """문자열에서 금액 추출(△→음수, 콤마 제거). 앞 3개(당기/전기/전전기)."""
    return [int(x.replace(",", "").replace("△", "-")) for x in re.findall(r"△?[\d,]{%d,}" % min_digits, s)][:3]


# ── 판관비(SG&A) 명세 ────────────────────────────────────────────
_SGA_ANCHORS = ("판매비와관리비의 내역", "판매비와관리비 내역", "판매관리비의 내역",
                "판매비와관리비의 세부", "판매비와관리비의세부", "판매비와관리비 명세")
_SGA_BAD = ("원부재료", "원재료", "저장품", "재공품", "상품의판매", "상품매출원가",
            "재고자산변동", "재고자산의변동", "조정영업이익", "당기순이익", "주당")
_SGA_GOOD = ("급여", "지급수수료", "복리후생", "감가상각", "광고선전", "임차")
_SGA_JUNK = re.compile(r"매출|영업이익|당기순이익|사업연도|제\s*\d+\s*기|매입|재고자산|조정영업|"
                       r"처분이익|처분손실|손상차손|환입|기부금|잡이익|잡손실|주당|^[\-–−·*]")


def _sga_ok(c):
    """표 텍스트가 판관비 명세로 적합한가: 나쁜 마커(성격별비용·하이라이트) 없고 SG&A 키워드 3개 이상."""
    if any(b in c.replace(" ", "") for b in _SGA_BAD):
        return False
    return sum(1 for g in _SGA_GOOD if g in c) >= 3


def _unit_near(full, pos, default="백만원"):
    """표 캡션의 단위 라벨(천원/백만원/억원…). 단위 캡션은 표 바로 앞/안에 오므로
    **표 시작에 가장 가까운** 단위 토큰을 채택(주변 다른 표의 단위에 오염되지 않게)."""
    lo = max(0, pos - 400)
    win = full[lo:pos + 300]
    anchor = pos - lo                                 # 창 내 표 시작 위치
    best, bestd = default, 10 ** 9
    for u in ("천원", "백만원", "십억원", "억원", "조원"):
        start = 0
        while True:
            k = win.find(u, start)
            if k < 0:
                break
            if abs(k - anchor) < bestd:
                best, bestd = u, abs(k - anchor)
            start = k + 1
    return best


def _find_sga_table(full):
    """판관비 명세 표 탐색: '판매비와관리비의 내역' 앵커 뒤 첫 적합 표 우선 → 없으면 소형·SG&A다수 표 스캔.
    성격별비용(매입·재고변동·원재료)·재무하이라이트(매출액·당기순이익) 표는 _sga_ok로 거부.
    반환: (표 텍스트, 시작위치) — 위치는 단위 캡션 감지용."""
    for a in _SGA_ANCHORS:
        i = full.find(a)
        while i >= 0:
            ts = full.find("<TABLE", i)
            te = full.find("</TABLE>", ts) if ts >= 0 else -1
            if 0 <= ts and te > ts and _sga_ok(clean(full[ts:te])):
                return full[ts:te + 8], ts
            i = full.find(a, i + 1)
    for m in re.finditer("<TABLE", full):
        ts = m.start(); te = full.find("</TABLE>", ts)
        if te < 0:
            continue
        c = clean(full[ts:te])
        if len(c) <= 4000 and "지급수수료" in c and _sga_ok(c):
            return full[ts:te + 8], ts
    return None


def parse_sga(full):
    """판매비와관리비 명세 → {items:{항목:[당기,전기,…]}, total, rnd_excl_subtotal, periods, unit}.
    항목별 **다기간**(당기/전기/전전기) 추출 + **단위 감지**(천원/백만원…). 잡음 행은 제거."""
    found = _find_sga_table(full)
    if not found:
        return None
    table, tpos = found
    unit = _unit_near(full, tpos)
    items, total, subtotal, periods = {}, None, None, 0
    for row in re.findall(r"<TR[^>]*>(.*?)</TR>", table, re.S | re.I):
        t = clean(row)
        vals = _amts(t, 4)                            # [당기,전기,전전기] (△→음수, 앞 3개)
        if not vals:
            continue
        label = re.split(r"△?[\d,]{4,}", t)[0].strip()
        for kw in ("소계 ", "합계 "):                 # ROWSPAN 병합 잔여 라벨 제거
            if kw in label:
                label = label.split(kw)[-1].strip()
        lab = label.replace(" ", "")                  # '합 계'·'구 분'처럼 공백 낀 라벨 정규화
        periods = max(periods, len(vals))
        if not lab or "합계" in lab or lab.endswith("소계"):   # 합계/소계 행은 total로(항목 아님)
            total, subtotal = (vals, subtotal) if total is None else (total, vals)
            continue
        if _SGA_JUNK.search(lab):                      # 매출액·매입·조정영업이익 등 비-판관비 행 제거
            continue
        items[label] = vals
    return {"items": items, "total": total, "rnd_excl_subtotal": subtotal,
            "periods": periods, "unit": unit}


# ── 부문(세그먼트) P&L ────────────────────────────────────────────
_SEG_BAD = ("매출총이익", "매출원가")                 # 손익계산서류 배제


def _mk_seg(name, sales, op):
    return {"segment": name, "sales": sales, "op": op,
            "op_margin": [round(o / s * 100, 1) if s else None for o, s in zip(op, sales)]}


def _seg_blocks_by_name(tbl):
    """레이아웃A(부문명이 매출액 직전) — 삼성형."""
    starts = [(m.start(), m.group(1).strip())
              for m in re.finditer(r"([가-힣A-Za-z][가-힣A-Za-z ]{0,12}?)\s*매출액", tbl)]
    segs = []
    for k, (pos, name) in enumerate(starts):
        name = re.split(r"비중|구분", name)[-1].strip()       # 헤더 잔여 제거
        end = starts[k + 1][0] if k + 1 < len(starts) else len(tbl)
        block = tbl[pos:end]
        sm = re.search(r"매출액(.*?)영업이익", block, re.S)
        om = re.search(r"영업이익(.*?)(총자산|$)", block, re.S)
        sales = _amts(sm.group(1), 5) if sm else []
        op = _amts(om.group(1), 4) if om else []
        if len(sales) >= 3 and len(op) >= 3 and name:
            segs.append(_mk_seg(name, sales, op))
    return segs


def _seg_blocks_by_company(tbl):
    """레이아웃B(부문명 (주)회사명 총자산 … 매출액 … 영업이익) — GS·지주형."""
    pat = re.compile(r"([가-힣][가-힣A-Za-z·]{0,9})\s*\(주\).*?매출액(.*?)영업이익(.*?)"
                     r"(?=[가-힣][가-힣A-Za-z·]{0,9}\s*\(주\)|$)", re.S)
    segs = []
    for m in pat.finditer(tbl):
        sales, op = _amts(m.group(2), 5), _amts(m.group(3), 4)
        if sales and op and m.group(1).strip():
            segs.append(_mk_seg(m.group(1).strip(), sales, op))
    return segs


def parse_revenue(full):
    """부문별 매출/영업이익/영업이익률. 부문 P&L 후보 표를 점수순으로 시도해 **추출 성공한 첫 표** 채택
    (단일 표 선택은 매출증감표 등에 오인식되기 쉬움). 영업이익률(단위 무관)이 핵심 비교지표.
    표 없거나 실패 시 '매출실적' 섹션 텍스트 폴백."""
    cands = []
    for m in re.finditer("<TABLE", full):
        ts = m.start(); te = full.find("</TABLE>", ts)
        if te < 0:
            continue
        c = clean(full[ts:te])
        if not (c.count("매출") >= 2 and c.count("영업이익") >= 2 and len(c) < 8000):
            continue
        if any(b in c for b in _SEG_BAD):             # 손익계산서·매출총이익 표 배제
            continue
        score = c.count("부문") * 2 + (4000 - min(len(c), 4000)) / 1000.0   # 부문 많고 작을수록↑
        cands.append((score, ts, c))
    for _, ts, c in sorted(cands, key=lambda x: -x[0]):
        segs = _seg_blocks_by_name(c) or _seg_blocks_by_company(c)
        if len(segs) >= 2:                            # 진짜 부문표는 부문 ≥2 — 오인식 표 걸러냄
            return {"segments": segs, "unit": _unit_near(full, ts), "periods": "당기/전기/전전기"}
    i = full.find("가. 매출실적")
    if i < 0:
        i = full.find("매출실적")
    return {"section_text": clean(full[i:i + 1800])} if i >= 0 else {"segments": [], "unit": "억원"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="사업보고서 원문 → 판관비/부문 P&L JSON")
    ap.add_argument("--rcept", required=True, help="접수번호(collect 출력의 rcept_no)")
    ap.add_argument("--what", default="all", choices=["sga", "revenue", "all"])
    ap.add_argument("--key", default=None, help="DART 인증키 직접 전달(챗 대화 input용)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.key:
        os.environ["DART_API_KEY"] = a.key   # 챗: .env 대신 대화 input → 환경변수 주입
    key = load_api_key()
    full = get_document(a.rcept, key)
    out = {}
    if a.what in ("sga", "all"):
        out["sga"] = parse_sga(full)
    if a.what in ("revenue", "all"):
        out["revenue"] = parse_revenue(full)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        msg = []
        if out.get("sga"):
            msg.append(f"판관비 {len(out['sga']['items'])}항목")
        if out.get("revenue") and out["revenue"].get("segments"):
            msg.append(f"부문 P&L {len(out['revenue']['segments'])}개")
        print(f"✅ 추출: {', '.join(msg) or '데이터 확인 필요'} → {a.out}")
    else:
        print(text)
