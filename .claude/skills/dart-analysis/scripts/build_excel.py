#!/usr/bin/env python3
"""dart-analysis · Excel 산출 모듈 (네트워크 불필요 — 2단계 재사용)
────────────────────────────────────────────────────────────────────
ratios JSON (+ raw JSON) → Excel 3시트(Summary/Ratios/Raw) + 네이티브 차트 3종.
차트는 xlsxwriter 네이티브(엑셀 차트)라 한글 폰트 이슈 없고 사용자가 바로 재가공 가능.

사용:
  python3 build_excel.py --ratios ratios.json --raw raw.json --out "회사명_재무분석_2022-2024.xlsx"
"""
import argparse, json
try:
    import xlsxwriter
except ImportError:
    raise SystemExit("xlsxwriter 필요: pip install xlsxwriter")


def build(res, raw, out_path):
    wb = xlsxwriter.Workbook(out_path)
    f_title = wb.add_format({"bold": True, "font_size": 14})
    f_hdr = wb.add_format({"bold": True, "bg_color": "#DDEBD8", "border": 1})
    f_num = wb.add_format({"num_format": '_-* #,##0_-;-* #,##0_-;_-* "-"_-;_-@_-'})  # 회계(accounting)
    years = res["years"]
    ny = len(years)

    # ── Summary ──────────────────────────────────────────
    ws = wb.add_worksheet("Summary")
    ws.set_column(0, 0, 24)
    c = res["company"]
    ws.write(0, 0, f"{c['corp_name']} 재무분석", f_title)
    ws.write(1, 0, f"{res['fs_div']} · {years[0]}–{years[-1]} · 출처 접수번호 {res.get('rcept_no', '')}")

    # 비율 표
    r0 = 3
    ws.write(r0, 0, "비율 (범주)", f_hdr)
    for j, y in enumerate(years):
        ws.write(r0, 1 + j, y, f_hdr)
    ws.write(r0, 1 + ny, "추세", f_hdr)
    ratio_row, rr = {}, r0 + 1
    for r in res["ratios"]:
        if not r["computed"]:
            continue
        ws.write(rr, 0, f'{r["label"]} ({r["category"]})')
        for j, y in enumerate(years):
            if y in r["values"]:
                ws.write_number(rr, 1 + j, r["values"][y])
        ws.write(rr, 1 + ny, r.get("trend") or "")
        ratio_row[r["id"]] = rr
        rr += 1

    # 주요 금액 표
    amt_hdr = rr + 1
    ws.write(amt_hdr, 0, "주요 금액 (원)", f_hdr)
    for j, y in enumerate(years):
        ws.write(amt_hdr, 1 + j, y, f_hdr)
    amt_row, ar = {}, amt_hdr + 1
    for k, vals in res.get("key_amounts", {}).items():
        ws.write(ar, 0, k)
        for j, y in enumerate(years):
            if vals.get(y) is not None:
                ws.write_number(ar, 1 + j, vals[y], f_num)
        amt_row[k] = ar
        ar += 1

    # ── 차트 3종 (D열에 세로 배치) ───────────────────────
    def chart(kind, title, keys, hdr_row, rowmap, anchor):
        ch = wb.add_chart({"type": kind})
        added = 0
        for key in keys:
            if key in rowmap:
                row = rowmap[key]
                ch.add_series({
                    "name": ["Summary", row, 0],
                    "categories": ["Summary", hdr_row, 1, hdr_row, ny],
                    "values": ["Summary", row, 1, row, ny],
                })
                added += 1
        if added:
            ch.set_title({"name": title})
            ch.set_size({"width": 460, "height": 280})
            ws.insert_chart(anchor, ch)

    chart("column", "매출·이익 추이", ["매출액", "영업이익", "당기순이익"], amt_hdr, amt_row, f"D{r0 + 1}")
    chart("line", "수익성 비율 추이", ["operating_margin", "net_margin", "roe", "roa"], r0, ratio_row, f"D{r0 + 17}")
    chart("line", "안정성 비율 추이", ["debt_ratio", "current_ratio"], r0, ratio_row, f"D{r0 + 33}")

    # ── Ratios (재가공용 평면 표) ────────────────────────
    wr = wb.add_worksheet("Ratios")
    wr.write_row(0, 0, ["id", "label", "category", "unit"] + years + ["trend"], f_hdr)
    for i, r in enumerate(res["ratios"], 1):
        wr.write_row(i, 0, [r["id"], r["label"], r["category"], r["unit"]]
                     + [r["values"].get(y, "") for y in years] + [r.get("trend") or ""])

    # ── Raw (원계정) ─────────────────────────────────────
    wraw = wb.add_worksheet("Raw")
    wraw.set_column(1, 1, 30)
    wraw.write_row(0, 0, ["재무제표", "계정명"] + years, f_hdr)
    for i, acc in enumerate(raw["accounts"], 1):
        wraw.write_row(i, 0, [acc.get("sj_nm", ""), acc.get("account_nm", "")]
                       + [acc["values"].get(y) for y in years])

    wb.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="재무비율 JSON → Excel(.xlsx)")
    ap.add_argument("--ratios", required=True, help="analyze.py 출력 JSON")
    ap.add_argument("--raw", required=True, help="collect.py 출력 JSON(원계정용)")
    ap.add_argument("--out", required=True, help="출력 .xlsx 경로")
    a = ap.parse_args()
    res = json.load(open(a.ratios, encoding="utf-8"))
    raw = json.load(open(a.raw, encoding="utf-8"))
    build(res, raw, a.out)
    print(f"✅ Excel 저장: {a.out}  (시트: Summary/Ratios/Raw · 차트 3종)")
