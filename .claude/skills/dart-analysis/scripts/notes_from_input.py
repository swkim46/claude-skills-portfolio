#!/usr/bin/env python3
"""dart-analysis · 챗 경로 B 반입 (네트워크 불필요 — parse_notes의 챗 대체)
────────────────────────────────────────────────────────────────────
claude.ai 챗은 egress 차단으로 사업보고서 원문(document.xml)을 못 받는다.
DART 사업보고서에서 확보한 판관비 명세/부문 P&L을 CSV로 받아 notes.json(=drivers)으로 변환.
analyze --notes 로 ratios.json에 병합하면 compare(비용구조·세그먼트)가 그대로 사용한다.

연도키 drivers 계약(= peer가 parse_notes 위치배열을 매핑한 결과와 동일 형태):
  sga     : {items:{raw항목명:{year:금액}}, unit}
  revenue : {segments:[{segment:raw부문명, by_year:{year:{sales,op,op_margin}}}], unit}

CSV 형식:
  - 비용(--cost)     : 1행 'cost_item,<연도>,…' / 각 행 = 판관비 항목명 + 연도별 금액(기본 백만원)
  - 부문(--segments) : 1행 'segment,metric,<연도>,…' / metric=sales|op, 각 부문 2행(매출/영익)
                       금액 기본 억원. op_margin은 op/sales로 자동 계산.

사용:
  python3 notes_from_input.py --cost cost.csv --segments seg.csv --out notes.json
  → analyze.py --raw raw.json --notes notes.json --out ratios.json  (회사별 반복)
"""
import argparse, csv, json, sys


def _read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.reader(f) if any(c.strip() for c in r)]


def _num(s):
    s = (s or "").replace(",", "").replace("△", "-").strip()
    if s in ("", "-", "N/A", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cost_from_csv(path, unit="백만원"):
    rows = _read(path)
    if len(rows) < 2:
        sys.exit("비용 CSV에 데이터 행이 없음(헤더 + 최소 1항목).")
    years = [h.strip() for h in rows[0][1:] if h.strip()]
    items = {}
    for r in rows[1:]:
        nm = r[0].strip()
        if not nm:
            continue
        vals = {y: _num(r[i + 1]) for i, y in enumerate(years) if i + 1 < len(r) and _num(r[i + 1]) is not None}
        if vals:
            items[nm] = vals
    return {"items": items, "unit": unit}


def segments_from_csv(path, unit="억원"):
    rows = _read(path)
    if len(rows) < 2:
        sys.exit("부문 CSV에 데이터 행이 없음(헤더 + 최소 1행).")
    years = [h.strip() for h in rows[0][2:] if h.strip()]
    acc, order = {}, []                                  # segment → metric → {year: val}
    for r in rows[1:]:
        seg = r[0].strip()
        metric = (r[1].strip().lower() if len(r) > 1 else "")
        if not seg or metric not in ("sales", "op"):
            continue
        if seg not in acc:
            acc[seg] = {}
            order.append(seg)
        acc[seg][metric] = {y: _num(r[i + 2]) for i, y in enumerate(years)
                            if i + 2 < len(r) and _num(r[i + 2]) is not None}
    segs = []
    for seg in order:
        sales, op = acc[seg].get("sales", {}), acc[seg].get("op", {})
        by_year = {}
        for y in sorted(set(sales) | set(op)):
            s, o = sales.get(y), op.get(y)
            m = round(o / s * 100, 1) if (s not in (None, 0) and o is not None) else None
            by_year[y] = {"sales": s, "op": o, "op_margin": m}
        segs.append({"segment": seg, "by_year": by_year})
    return {"segments": segs, "unit": unit}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="비용/부문 CSV → notes.json (챗 경로B 반입)")
    ap.add_argument("--cost", default=None, help="판관비 명세 CSV")
    ap.add_argument("--segments", default=None, help="부문 P&L CSV")
    ap.add_argument("--cost-unit", default="백만원")
    ap.add_argument("--segment-unit", default="억원")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if not (a.cost or a.segments):
        sys.exit("최소 하나 필요: --cost 또는 --segments")
    out = {}
    if a.cost:
        out["sga"] = cost_from_csv(a.cost, a.cost_unit)
    if a.segments:
        out["revenue"] = segments_from_csv(a.segments, a.segment_unit)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        msg = []
        if out.get("sga"):
            msg.append(f"비용 {len(out['sga']['items'])}항목")
        if out.get("revenue"):
            msg.append(f"부문 {len(out['revenue']['segments'])}개")
        print(f"✅ notes 생성: {', '.join(msg)} → {a.out}")
    else:
        print(text)
