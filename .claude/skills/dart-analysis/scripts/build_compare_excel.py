#!/usr/bin/env python3
"""dart-analysis · 경쟁사 비교 Excel 산출 (네트워크 불필요 — 2단계 챗 재사용)
────────────────────────────────────────────────────────────────────
compare.py 산출 comparison.json → Excel 2시트 + 비교 차트.
  · 요약   : 최신연도 스냅샷(회사×지표) + 선두/신호(산업 vs 당사) + 비교 차트 묶음
  · 데이터 : 지표·금액별 회사×연도 표(차트가 이 셀들을 참조 — hard key-in 아님)
차트는 xlsxwriter 네이티브(연도=가로축, 회사=계열)라 사용자가 바로 재가공 가능.

사용:
  python3 build_compare_excel.py --comparison comparison.json --out "편의점3사_비교_2021-2025.xlsx"
"""
import argparse, json
try:
    import xlsxwriter
except ImportError:
    raise SystemExit("xlsxwriter 필요: pip install xlsxwriter")


def _xy_block(ws, row, title, companies, yrs, vals_by_company, f_sub, f_h, fmt):
    """회사×연도 표 한 블록 작성 → (다음행, 차트참조 좌표) 반환. 세로 흐름(겹침 방지)."""
    ws.write(row, 0, title, f_sub)
    hdr = row + 1
    ws.write(hdr, 0, "회사", f_h)
    for j, y in enumerate(yrs):
        ws.write(hdr, 1 + j, y, f_h)
    rows, rr = {}, hdr
    for n in companies:
        if n not in vals_by_company:
            continue
        rr += 1
        ws.write(rr, 0, n)
        for j, y in enumerate(yrs):
            v = vals_by_company[n].get(y)
            if v is not None:
                ws.write_number(rr, 1 + j, v, fmt)
        rows[n] = rr
    return rr + 2, {"hdr": hdr, "rows": rows, "yrs": yrs, "sheet": ws.name}


def _trend_chart(wb, ws, kind, title, blk, anchor_row):
    """blk(연도=가로축, 회사=계열) → 차트를 anchor_row(A열)에 삽입. 세로로 쌓아 겹침 방지."""
    if not blk or not blk["rows"]:
        return anchor_row
    ch = wb.add_chart({"type": kind})
    ny, hdr, sh = len(blk["yrs"]), blk["hdr"], blk["sheet"]
    for n, rw in blk["rows"].items():
        ch.add_series({"name": [sh, rw, 0], "categories": [sh, hdr, 1, hdr, ny],
                       "values": [sh, rw, 1, rw, ny]})
    ch.set_title({"name": title})
    ch.set_size({"width": 520, "height": 300})
    ch.set_legend({"position": "bottom"})
    ws.insert_chart(anchor_row, 0, ch)
    return anchor_row + 17


def _cost_sheet(wb, cs, companies, f_title, f_h, f_sub, f_pct):
    """비용구조 시트: 최신연도 범주×회사 스냅샷(+막대) + 범주별 다년 추세(표+라인)."""
    ws = wb.add_worksheet("비용구조")
    ws.set_column(0, 0, 22)
    ws.set_column(1, 12, 12)
    ws.write(0, 0, "비용구조 — 매출 대비 비중(%)", f_title)
    ws.write(1, 0, cs.get("note", ""))
    cats_l = [c for c in cs["categories"] if c.get("year_latest")]
    yl = cats_l[0]["year_latest"] if cats_l else None

    # 1) 최신연도 스냅샷(범주×회사) + 무거움 + 클러스터 막대
    row = 3
    ws.write(row, 0, f"최신연도({yl}) 비용 비중(%)", f_sub)
    hdr = row + 1
    ws.write(hdr, 0, "범주", f_h)
    for j, n in enumerate(companies):
        ws.write(hdr, 1 + j, n, f_h)
    ws.write(hdr, 1 + len(companies), "무거움", f_h)
    snap_rows, rr = [], hdr
    for c in cats_l:
        rr += 1
        ws.write(rr, 0, c["category"])
        for j, n in enumerate(companies):
            v = c["by_company"].get(n, {}).get(c["year_latest"])
            if v is not None:
                ws.write_number(rr, 1 + j, v, f_pct)
        ws.write(rr, 1 + len(companies), c.get("heaviest") or "")
        snap_rows.append(rr)
    row = rr + 2
    if snap_rows:
        ch = wb.add_chart({"type": "column"})
        first, last = snap_rows[0], snap_rows[-1]
        for j, n in enumerate(companies):
            ch.add_series({"name": ["비용구조", hdr, 1 + j],
                           "categories": ["비용구조", first, 0, last, 0],
                           "values": ["비용구조", first, 1 + j, last, 1 + j]})
        ch.set_title({"name": f"비용구조 비중 비교 ({yl})"})
        ch.set_size({"width": 660, "height": 340})
        ch.set_legend({"position": "bottom"})
        ws.insert_chart(row, 0, ch)
        row += 19

    # 2) 범주별 다년 추세(표 + 라인) — 세로로 쌓기
    for c in cs["categories"]:
        row, blk = _xy_block(ws, row, f'{c["category"]} 비중(%) 추세', companies, c["years"],
                             c["by_company"], f_sub, f_h, f_pct)
        row = _trend_chart(wb, ws, "line", f'{c["category"]} 비중 추세(%)', blk, row)


def _segment_sheet(wb, sg, companies, f_title, f_h, f_sub, f_acct, f_pct):
    """세그먼트 시트: canonical 부문별 영업이익률(%)·매출 표 + 차트. focus면 해당 부문만."""
    ws = wb.add_worksheet("세그먼트")
    ws.set_column(0, 0, 22)
    ws.set_column(1, 12, 14)
    ws.write(0, 0, "세그먼트(사업부문) 비교", f_title)
    sub = f"부문 단위 {sg.get('unit', '억원')}"
    if sg.get("focus"):
        sub += f"  ·  초점: {sg['focus']} (사업믹스 왜곡 제거)"
    ws.write(1, 0, sub)
    row = 3
    for s in sg["by_segment"]:
        seg, m = s["segment"], s["metrics"]
        om = m.get("op_margin")
        if om and om["by_company"]:
            row, blk = _xy_block(ws, row, f'[{seg}] 영업이익률(%)', companies, om["years"],
                                 om["by_company"], f_sub, f_h, f_pct)
            row = _trend_chart(wb, ws, "line", f'[{seg}] 영업이익률 추세(%)', blk, row)
        sl = m.get("sales")
        if sl and sl["by_company"]:
            row, blk = _xy_block(ws, row, f'[{seg}] 매출({sg.get("unit", "억원")})', companies,
                                 sl["years"], sl["by_company"], f_sub, f_h, f_acct)
            row = _trend_chart(wb, ws, "column", f'[{seg}] 매출 추이', blk, row)
        row += 1


def build(cmp, out_path):
    wb = xlsxwriter.Workbook(out_path)
    f_title = wb.add_format({"bold": True, "font_size": 14})
    f_h = wb.add_format({"bold": True, "bg_color": "#DDEBD8", "border": 1})
    f_sub = wb.add_format({"bold": True, "font_size": 11})
    f_lead = wb.add_format({"bold": True})
    # 셀 서식: 금액=회계(accounting, 천단위·음수 괄호·0은 대시), 비율·점유=숫자
    f_acct = wb.add_format({"num_format": '_-* #,##0_-;-* #,##0_-;_-* "-"_-;_-@_-'})
    f_ratio = wb.add_format({"num_format": "#,##0.0"})
    f_pct = wb.add_format({"num_format": "0.0"})

    companies = [c["name"] for c in cmp["companies"]]

    # ── 데이터 시트: 지표·금액 블록(회사×연도). 차트가 참조 ──────────
    wd = wb.add_worksheet("데이터")
    wd.set_column(0, 0, 20)
    wd.set_column(1, 12, 18)            # 금액(원)이 회계서식으로 길어 열 넓힘
    state = {"row": 0}
    blk_ratio, blk_amt = {}, {}

    def block(title, yrs, vals_by_company, fmt=None):
        """회사×연도 표 한 블록 작성 → 차트 참조용 좌표 반환. fmt=셀 서식."""
        row = state["row"]
        wd.write(row, 0, title, f_sub)
        hdr = row + 1
        wd.write(hdr, 0, "회사", f_h)
        for j, y in enumerate(yrs):
            wd.write(hdr, 1 + j, y, f_h)
        rows, rr = {}, hdr
        for n in companies:
            if n not in vals_by_company:
                continue
            rr += 1
            wd.write(rr, 0, n)
            for j, y in enumerate(yrs):
                v = vals_by_company[n].get(y)
                if v is not None:
                    wd.write_number(rr, 1 + j, v, fmt)
            rows[n] = rr
        state["row"] = rr + 2
        return {"hdr": hdr, "rows": rows, "yrs": yrs}

    for R in cmp["ratios"]:
        vbc = {n: R["by_company"][n]["values"] for n in R["by_company"]}
        blk_ratio[R["id"]] = block(f'{R["label"]} ({R["unit"]})', R["years"], vbc, f_ratio)
    for A in cmp["amounts"]:                       # ★ 금액은 회계 서식
        blk_amt[A["account"]] = block(f'{A["account"]} ({cmp["currency"]})', A["years"], A["by_company"], f_acct)
    # 매출 점유율(share) 블록
    blk_share = None
    sales = next((A for A in cmp["amounts"] if A["account"] == "매출액" and A.get("share")), None)
    if sales:
        syrs = sorted(sales["share"])
        sbc = {n: {y: sales["share"][y].get(n) for y in syrs} for n in companies}
        sbc = {n: v for n, v in sbc.items() if any(x is not None for x in v.values())}
        blk_share = block("매출 점유율 (%)", syrs, sbc, f_pct)

    # ── 요약 시트 ────────────────────────────────────────────────
    ws = wb.add_worksheet("요약")
    ws.set_column(0, 0, 24)
    ws.set_column(1, 8, 13)
    ws.write(0, 0, "경쟁사 재무비교", f_title)
    ws.write(1, 0, " vs ".join(companies))
    fs_note = " / ".join(f"{c['name']} {c['fs_div'] or ''}" for c in cmp["companies"])
    ws.write(2, 0, f"{cmp['years'][0]}–{cmp['years'][-1]} · {cmp['currency']} · {fs_note}")

    r = 4
    if cmp.get("headlines"):
        ws.write(r, 0, "헤드라인", f_h)
        r += 1
        for h in cmp["headlines"]:
            ws.write(r, 0, "• " + h)
            r += 1
        r += 1

    # 최신연도 스냅샷(회사×지표) + 선두 + 신호
    ws.write(r, 0, "최신연도 스냅샷", f_h)
    nc = len(companies)
    for j, n in enumerate(companies):
        ws.write(r, 1 + j, n, f_h)
    ws.write(r, 1 + nc, "선두", f_h)
    ws.write(r, 2 + nc, "신호(산업/당사)", f_h)
    r += 1
    for R in cmp["ratios"]:
        if not R.get("year_latest"):
            continue
        ws.write(r, 0, f'{R["label"]} ({R["unit"]})')
        for j, n in enumerate(companies):
            v = R["by_company"].get(n, {}).get("values", {}).get(R["year_latest"])
            if v is not None:
                ws.write_number(r, 1 + j, v, f_ratio)
        ws.write(r, 1 + nc, R.get("leader_latest") or "", f_lead)
        cm = R.get("comove_latest")
        sig = ""
        if cm and cm["type"] == "macro":
            sig = f'{cm["label"]} → 산업요인'
        elif cm and cm["type"] == "idiosyncratic":
            sig = f'{cm["label"]} → {", ".join(cm.get("divergent", []))} 역행(당사)'
        ws.write(r, 2 + nc, sig)
        r += 1

    if cmp.get("warnings"):
        r += 1
        ws.write(r, 0, "⚠️ 비교가능성 주의", f_h)
        r += 1
        for w in cmp["warnings"]:
            ws.write(r, 0, "• " + w)
            r += 1

    # ── 비교 차트 묶음(연도=가로축, 회사=계열) ────────────────────
    def chart(kind, title, blk, anchor, subtype=None):
        if not blk or not blk["rows"]:
            return
        spec = {"type": kind}
        if subtype:
            spec["subtype"] = subtype
        ch = wb.add_chart(spec)
        ny, hdr = len(blk["yrs"]), blk["hdr"]
        for n, rw in blk["rows"].items():
            ch.add_series({
                "name": ["데이터", rw, 0],
                "categories": ["데이터", hdr, 1, hdr, ny],
                "values": ["데이터", rw, 1, rw, ny],
            })
        ch.set_title({"name": title})
        ch.set_size({"width": 480, "height": 300})
        ch.set_legend({"position": "bottom"})
        ws.insert_chart(anchor, ch)

    specs = [
        ("column", "매출액 추이(절대규모)", blk_amt.get("매출액"), None),
        ("column", "영업이익 추이", blk_amt.get("영업이익"), None),
        ("line", "영업이익률 추이(%)", blk_ratio.get("operating_margin"), None),
        ("line", "매출액증가율 추이(%)", blk_ratio.get("revenue_growth"), None),
        ("line", "부채비율 추이(%)", blk_ratio.get("debt_ratio"), None),
        ("line", "ROE 추이(%)", blk_ratio.get("roe"), None),
        ("column", "매출 점유율 추이(%)", blk_share, "percent_stacked"),
    ]
    cs = r + 2
    for i, (k, t, b, sub) in enumerate(specs):
        col = "A" if i % 2 == 0 else "I"
        chart(k, t, b, f"{col}{cs + (i // 2) * 16}", sub)

    # ── 경로B 드라이버 시트(있을 때만 — 없으면 v1 그대로) ─────────────
    cost = cmp.get("cost_structure")
    if cost and cost.get("categories"):
        _cost_sheet(wb, cost, companies, f_title, f_h, f_sub, f_pct)
    seg = cmp.get("segments")
    if seg and seg.get("by_segment"):
        _segment_sheet(wb, seg, companies, f_title, f_h, f_sub, f_acct, f_pct)

    wb.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="comparison.json → 경쟁사 비교 Excel")
    ap.add_argument("--comparison", required=True, help="compare.py 산출 JSON")
    ap.add_argument("--out", required=True, help="출력 .xlsx 경로")
    a = ap.parse_args()
    cmp = json.load(open(a.comparison, encoding="utf-8"))
    build(cmp, a.out)
    sheets = ["요약", "데이터"]
    if (cmp.get("cost_structure") or {}).get("categories"):
        sheets.append("비용구조")
    if (cmp.get("segments") or {}).get("by_segment"):
        sheets.append("세그먼트")
    print(f"✅ 비교 Excel 저장: {a.out}  (시트: {'/'.join(sheets)} · 비교 차트)")
