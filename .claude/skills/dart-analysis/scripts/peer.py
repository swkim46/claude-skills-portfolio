#!/usr/bin/env python3
"""dart-analysis · 경쟁사 비교 오케스트레이터 (Claude Code 전용 — 네트워크)
────────────────────────────────────────────────────────────────────
여러 기업을 한 번에: collect(다년도) + analyze 를 회사별로 돌리고 → compare → 비교 Excel.
네트워크는 collect.py에만 의존하므로, 챗(egress 차단)에선 collect 대신 from_input을 쓰고
analyze→compare→build_compare_excel 를 수동 실행하면 동일 산출(§SKILL 비교 워크플로 참고).

기본으로 경로B(판관비 명세·부문 P&L)도 보고서 원문에서 수집해 비용구조·세그먼트 비교까지 산출.
  --no-notes        경로B 생략(표준비율만, 빠름)
  --segment-focus   특정 canonical 부문만 격리(예: 편의점 — 연결 사업믹스 왜곡 제거)

사용:
  python3 peer.py --idents 코리아세븐 비지에프리테일 지에스리테일 --years 5 \
                  --segment-focus 편의점 --out-dir /tmp/cvs --xlsx "편의점3사_비교.xlsx"
  python3 peer.py --idents "현대:2" 신세계 --years 5 ...   # 동명은 'ident:순번'으로 선택
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import collect, load_api_key         # noqa: E402  (네트워크 — Code 전용)
from analyze import calc, load_yaml               # noqa: E402
from compare import compare                       # noqa: E402
from build_compare_excel import build             # noqa: E402
from parse_notes import get_document, parse_sga, parse_revenue  # noqa: E402  (경로B 원문 — 네트워크)


def _split_pick(ident):
    if ":" in ident:
        a, b = ident.rsplit(":", 1)
        if b.isdigit():
            return a, int(b)
    return ident, None


def _collect_drivers(rcept_map, key):
    """보고서별 parse_notes(원문) → 위치배열(당기/전기/전전기)을 연도로 매핑·병합 → 연도키 drivers.
    최신 base 보고서부터 처리(gap-fill, 최신 우선). 네트워크/표 실패는 건너뜀. 없으면 None."""
    if not rcept_map:
        return None
    sga_items, sga_unit, sga_periods = {}, None, 0
    seg_acc, seg_order, seg_unit = {}, [], None
    for b in sorted((int(k) for k in rcept_map), reverse=True):    # 최신 보고서부터 — 단위는 최신 우선
        try:
            full = get_document(rcept_map[str(b)], key)
        except Exception:
            continue
        try:
            sga = parse_sga(full)
        except Exception:
            sga = None
        if sga and sga.get("items"):
            if sga_unit is None:
                sga_unit = sga.get("unit", "백만원")
            sga_periods = max(sga_periods, sga.get("periods", 0))
            for label, vals in sga["items"].items():
                tgt = sga_items.setdefault(label, {})
                for i, v in enumerate(vals):
                    y = str(b - i)
                    if v is not None and y not in tgt:          # gap-fill, 최신 보고서 우선
                        tgt[y] = v
        try:
            rev = parse_revenue(full)
        except Exception:
            rev = None
        if rev and rev.get("segments"):
            if seg_unit is None:
                seg_unit = rev.get("unit", "억원")
            for seg in rev["segments"]:
                name = seg.get("segment", "")
                if name not in seg_acc:
                    seg_acc[name] = {}
                    seg_order.append(name)
                by_year, sales, op = seg_acc[name], seg.get("sales", []), seg.get("op", [])
                for i in range(max(len(sales), len(op))):
                    y = str(b - i)
                    cell = by_year.setdefault(y, {})
                    if i < len(sales) and sales[i] is not None and cell.get("sales") is None:
                        cell["sales"] = sales[i]
                    if i < len(op) and op[i] is not None and cell.get("op") is None:
                        cell["op"] = op[i]
    for name in seg_order:                                       # 병합 후 영익률 재계산(연도 일관)
        for cell in seg_acc[name].values():
            s, o = cell.get("sales"), cell.get("op")
            cell["op_margin"] = round(o / s * 100, 1) if (s not in (None, 0) and o is not None) else None
    drivers = {}
    if sga_items:
        drivers["sga"] = {"items": sga_items, "unit": sga_unit or "백만원", "periods": sga_periods}
    if seg_acc:
        drivers["revenue"] = {"unit": seg_unit or "억원",
                              "segments": [{"segment": n, "by_year": seg_acc[n]} for n in seg_order]}
    return drivers or None


def main():
    ap = argparse.ArgumentParser(description="경쟁사 비교 한 번에(수집→분석→비교→Excel)")
    ap.add_argument("--idents", nargs="+", required=True,
                    help="회사명/종목코드/고유번호 2개 이상. 동명은 '이름:순번'")
    ap.add_argument("--years", type=int, default=5, help="수집 연수(기본 5)")
    ap.add_argument("--year", type=int, default=None, help="종료연도(기본: 작년)")
    ap.add_argument("--fs", default="CFS", choices=["CFS", "OFS"], help="연결/별도")
    ap.add_argument("--reprt", default="11011", help="보고서 코드(기본 연간 11011)")
    ap.add_argument("--key", default=None, help="DART 인증키 직접 전달")
    ap.add_argument("--no-notes", action="store_true", help="경로B(판관비·부문) 수집 생략 — 표준비율만")
    ap.add_argument("--segment-focus", default=None, help="특정 canonical 부문만 격리 비교(예: 편의점)")
    ap.add_argument("--out-dir", default="/tmp/dart-compare", help="중간 산출 폴더")
    ap.add_argument("--xlsx", default=None, help="비교 Excel 경로(미지정 시 out-dir 생성)")
    a = ap.parse_args()
    if a.key:
        os.environ["DART_API_KEY"] = a.key
    ref = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references")
    ratios_doc = load_yaml(os.path.join(ref, "ratios.yaml"))
    aliases = load_yaml(os.path.join(ref, "account-aliases.yaml"))
    key = load_api_key()
    os.makedirs(a.out_dir, exist_ok=True)

    docs = []
    for ident in a.idents:
        name, pick = _split_pick(ident)
        raw = collect(name, a.year, a.fs, pick, a.reprt, a.years)
        res = calc(raw, ratios_doc, aliases)
        if not a.no_notes:                                # 경로B 드라이버(판관비·부문) 부착
            drivers = _collect_drivers(raw.get("rcept_map", {}), key)
            if drivers:
                res["drivers"] = drivers
        cn = res["company"]["corp_name"] or name
        json.dump(raw, open(os.path.join(a.out_dir, f"raw_{cn}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        json.dump(res, open(os.path.join(a.out_dir, f"ratios_{cn}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        ncalc = sum(1 for r in res["ratios"] if r["computed"])
        flag = f" ⛔ 핵심계정부재 {res['core_missing']}" if res.get("core_missing") else ""
        dv = res.get("drivers") or {}
        dnote = ""
        if dv:
            bits = []
            if dv.get("sga"):
                bits.append(f"비용 {len(dv['sga']['items'])}항목")
            if dv.get("revenue"):
                bits.append(f"부문 {len(dv['revenue']['segments'])}개")
            dnote = " · 경로B " + "/".join(bits)
        print(f"· {cn}(종목 {raw['company']['stock_code'] or '-'}) {raw['fs_div']} "
              f"{raw['years'][0]}–{raw['years'][-1]} · 비율 {ncalc}/{len(res['ratios'])}{flag}{dnote}")
        docs.append(res)

    if len(docs) < 2:
        sys.exit("비교는 회사 2곳 이상 필요.")
    cmp = compare(docs, segment_focus=a.segment_focus)
    json.dump(cmp, open(os.path.join(a.out_dir, "comparison.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    xlsx = a.xlsx or os.path.join(a.out_dir, f"경쟁사비교_{cmp['years'][0]}-{cmp['years'][-1]}.xlsx")
    build(cmp, xlsx)

    print(f"\n✅ 비교 완료: {' vs '.join(c['name'] for c in cmp['companies'])} "
          f"· {cmp['years'][0]}–{cmp['years'][-1]}")
    sy = cmp.get("synthesis") or {}
    if sy.get("leader_overall"):
        line = f"   ★ 종합선두 {sy['leader_overall']}(핵심지표 {sy['leads'].get(sy['leader_overall'], 0)}개)"
        if sy.get("outlier"):
            line += f" · 당사이슈 이상치 {sy['outlier']}"
        print(line)
    if sy.get("cost_drivers"):
        cd = sy["cost_drivers"][0]
        print(f"   ★ 비용드라이버 {cd['category']}: 무거움 {cd['heaviest']} vs 가벼움 {cd['leanest']} ({cd['gap_pp']}%p)")
    for h in cmp["headlines"]:
        print("   • " + h)
    for w in cmp["warnings"]:
        print("   ⚠️ " + w)
    print(f"\n📊 {xlsx}\n🗂  {os.path.join(a.out_dir, 'comparison.json')}")


if __name__ == "__main__":
    main()
