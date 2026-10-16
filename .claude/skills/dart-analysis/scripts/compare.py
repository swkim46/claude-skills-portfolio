#!/usr/bin/env python3
"""dart-analysis · 경쟁사 비교 모듈 (네트워크 불필요 — 2단계 챗 재사용)
────────────────────────────────────────────────────────────────────
analyze.py 산출 ratios.json N개(회사별) → 교차 비교 comparison.json.

핵심 가치(1단계 단일기업에선 금지했던 것):
  - peer 통계(중앙값·평균·최소·최대) — 다중 기업이므로 비로소 의미 있음.
  - 순위(leader/laggard) — 비율 방향(higher_is_better) 반영.
  - ★ 산업(macro) vs 개별(당사) 판별 — 최신 연도 YoY 방향이 전사 동조면 '산업요인',
    한 회사만 역행하면 '당사요인'. 컨설팅 시그널의 핵심.
  - 규모가 다르면 절대값보다 비율·점유(share)로 비교(매출 점유율 등).

사용:
  python3 compare.py --ratios a.json b.json c.json --out comparison.json
"""
import argparse, json, os, statistics, sys

# 채팅 헤드라인을 뽑을 대표 지표(있을 때만)
HEADLINE_IDS = ["revenue_growth", "operating_margin", "roe", "debt_ratio"]
# 금액 비교 대상(규모가 다르면 share로도 표시)
AMOUNT_ACCS = ["매출액", "영업이익", "당기순이익", "영업활동현금흐름"]
# 단위 라벨 → 원 환산 계수(경로B 드라이버 단위 통일용)
UNIT_FACTOR = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9, "조원": 1e12}


def _load_ref(name):
    """references/<name>.json 로드(없으면 빈 별칭). 분석층은 pyyaml 없을 수 있어 json 표준."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references")
    p = os.path.join(base, name + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"aliases": {}}


def _match_alias(raw, alias_map):
    """raw 라벨(공백 제거)에 별칭 토큰이 포함되면 그 표준명 반환(파일 순서=우선). 없으면 None."""
    nm = (raw or "").replace(" ", "")
    for std, variants in alias_map.items():
        for v in variants:
            if v.replace(" ", "") in nm:
                return std
    return None


def _name(doc):
    c = doc.get("company", {})
    return c.get("corp_name") or c.get("stock_code") or "?"


def _uniq_names(docs):
    """동명 회사는 종목코드를 덧붙여 구분."""
    raw = [_name(d) for d in docs]
    out, seen = [], {}
    for d, n in zip(docs, raw):
        if raw.count(n) > 1:
            n = f"{n}({d.get('company', {}).get('stock_code') or d.get('company', {}).get('corp_code', '')})"
        while n in seen:
            n += "_"
        seen[n] = 1
        out.append(n)
    return out


def _rank(pairs, higher_is_better):
    """pairs=[(name,value)] → 순위(동점 처리). higher_is_better=False면 작을수록 1위."""
    rev = True if higher_is_better is None else bool(higher_is_better)
    ordered = sorted(pairs, key=lambda kv: kv[1], reverse=rev)
    out, prev, rank = [], None, 0
    for i, (n, v) in enumerate(ordered, 1):
        if v != prev:
            rank, prev = i, v
        out.append({"company": n, "value": v, "rank": rank})
    return out


def _comove(by_company, y_latest, y_prev):
    """레벨 지표(마진·부채비율·ROE 등): 최신연도 YoY '방향'이 전사 동조인지로 판별.
    전사 같은 방향 → 산업(macro) 요인 / 한쪽만 역행 → 개별(당사) 요인."""
    deltas = {}
    for c, d in by_company.items():
        v0, v1 = d["values"].get(y_latest), d["values"].get(y_prev)
        if v0 is not None and v1 is not None:
            deltas[c] = round(v0 - v1, 4)
    if len(deltas) < 2:
        return None
    ups = [c for c, dv in deltas.items() if dv > 0]
    downs = [c for c, dv in deltas.items() if dv < 0]
    flats = [c for c, dv in deltas.items() if dv == 0]
    if downs == [] and flats == []:
        return {"type": "macro", "label": "산업 동반 개선", "direction": "▲",
                "basis": "delta", "ups": ups, "downs": downs, "deltas": deltas}
    if ups == [] and flats == []:
        return {"type": "macro", "label": "산업 동반 악화", "direction": "▼",
                "basis": "delta", "ups": ups, "downs": downs, "deltas": deltas}
    divergent = (downs if len(ups) >= len(downs) else ups) + flats
    return {"type": "idiosyncratic", "label": "개별 분화", "direction": "↕", "basis": "delta",
            "ups": ups, "downs": downs, "flats": flats, "divergent": divergent, "deltas": deltas}


def _comove_level(by_company, y):
    """성장률처럼 '값 자체가 변화율'인 지표: 최신연도 값의 부호(성장 vs 역성장)로 판별.
    (delta로 보면 2차미분=가감속이 잡혀 '한 곳만 역성장'을 '전사 감속'으로 오판함)"""
    vals = {c: d["values"][y] for c, d in by_company.items() if d["values"].get(y) is not None}
    if len(vals) < 2:
        return None
    pos = [c for c, v in vals.items() if v > 0]
    neg = [c for c, v in vals.items() if v < 0]
    zero = [c for c, v in vals.items() if v == 0]
    if neg == [] and zero == []:
        return {"type": "macro", "label": "산업 동반 성장", "direction": "▲", "basis": "level", "values": vals}
    if pos == [] and zero == []:
        return {"type": "macro", "label": "산업 동반 역성장", "direction": "▼", "basis": "level", "values": vals}
    divergent = (neg if len(pos) >= len(neg) else pos) + zero
    return {"type": "idiosyncratic", "label": "개별 분화", "direction": "↕", "basis": "level",
            "divergent": divergent, "values": vals}


def _cost_structure(docs, names, alias_map):
    """회사별 drivers.sga(판관비) → 표준 비용범주별 **매출 대비 비중(%)** 다년 교차비교.
    비중 = 항목금액(단위 통일 후 원) / 매출액(원, 같은 연도) × 100. 한 범주에 여러 항목이 걸리면 합산.
    낮은 비중 = 효율(1위). drivers 없으면 None(v1 회귀 안전)."""
    have = [(n, d) for n, d in zip(names, docs) if (d.get("drivers") or {}).get("sga", {}).get("items")]
    if not have:
        return None
    CAP_TOTAL, FLOOR_TOTAL = 0.6, 0.02     # SG&A 합계가 매출의 2~60% 밖이면 표 오인식/단위오류로 보고 제외
    cats, cat_amt, unmatched, suspect = {}, {}, {}, {}   # cat→{name→{year:pct}} 등, suspect=제외사유
    cat_order = list(alias_map.keys()) + ["기타"]
    for n, d in have:
        sga = d["drivers"]["sga"]
        rev = d.get("key_amounts", {}).get("매출액") or {}          # 원
        agg_raw = {}                                                # cat → {year: 원시합(단위 미적용)}
        for label, yv in sga["items"].items():
            cat = _match_alias(label, alias_map)
            if cat is None:
                cat = "기타"
                unmatched.setdefault(n, []).append(label)
            tgt = agg_raw.setdefault(cat, {})
            for y, v in yv.items():
                if v is not None:
                    tgt[y] = tgt.get(y, 0) + v
        # 단위: 감지값 우선, 매출 대비 합계가 비현실적이면 한 단계씩 보정(천원↔백만원 혼동 자동 흡수)
        det = UNIT_FACTOR.get(sga.get("unit", "백만원"), 1e6)
        yref = max((y for y in {yy for vv in agg_raw.values() for yy in vv} if rev.get(y)), default=None)
        factor = det
        if yref:
            raw_tot = sum(agg_raw[c].get(yref, 0) for c in agg_raw)
            factor = next((f for f in (det, det / 1e3, det / 1e6, det * 1e3)
                           if raw_tot and FLOOR_TOTAL <= raw_tot * f / rev[yref] <= CAP_TOTAL), None)
            if factor is None:                                      # 어떤 단위로도 비현실적 → 표 오인식, 제외(환각 차단)
                suspect[n] = (f"SG&A 합계가 어떤 단위로도 매출의 {int(FLOOR_TOTAL*100)}~{int(CAP_TOTAL*100)}% "
                              f"범위 밖({yref}) — 표 오인식 추정, 비용비교 제외")
                continue
        for cat, yv in agg_raw.items():
            amt = {y: v * factor for y, v in yv.items()}
            cat_amt.setdefault(cat, {})[n] = amt
            pct = {y: round(a / rev[y] * 100, 2) for y, a in amt.items() if rev.get(y) not in (None, 0)}
            if pct:
                cats.setdefault(cat, {})[n] = pct
    categories = []
    for cat in cat_order:
        bc = cats.get(cat)
        if not bc:
            continue
        cyears = sorted({y for v in bc.values() for y in v})
        stats = {y: {"median": round(statistics.median(vs), 2), "min": min(vs), "max": max(vs), "n": len(vs)}
                 for y in cyears for vs in [[bc[n][y] for n in bc if y in bc[n]]] if vs}
        y_latest = next((y for y in reversed(cyears) if stats.get(y, {}).get("n", 0) >= 1), None)
        ranking = leanest = heaviest = spread = comove = None
        if y_latest:
            pairs = [(n, bc[n][y_latest]) for n in bc if y_latest in bc[n]]
            ranking = _rank(pairs, higher_is_better=False)         # 낮은 비중 = 효율 = 1위
            leanest, heaviest = ranking[0]["company"], ranking[-1]["company"]
            vv = [p["value"] for p in ranking]
            spread = round(max(vv) - min(vv), 2)
            idx = cyears.index(y_latest)
            if idx > 0:
                comove = _comove({n: {"values": bc[n]} for n in bc}, y_latest, cyears[idx - 1])
        categories.append({
            "category": cat, "by_company": bc, "amounts": cat_amt.get(cat, {}),
            "years": cyears, "peer_stats": stats, "year_latest": y_latest, "ranking_latest": ranking,
            "leanest": leanest, "heaviest": heaviest, "spread": spread, "comove_latest": comove,
        })
    return {"categories": categories, "metric": "매출 대비 비중(%)", "unmatched": unmatched,
            "suspect": suspect,
            "note": "비중 = 판관비 항목 / 매출액 × 100. SG&A 단위 가정(기본 백만원)·매출=원으로 통일."}


def _segments(docs, names, alias_map, focus=None):
    """회사별 drivers.revenue.segments → canonical 부문별 매출/영익/영익률 교차비교.
    focus(canonical)면 해당 부문만(예: 편의점) — 연결 사업믹스 왜곡 제거. drivers 없으면 None."""
    have = [(n, d) for n, d in zip(names, docs) if (d.get("drivers") or {}).get("revenue", {}).get("segments")]
    if not have:
        return None
    groups, order, unmatched = {}, [], {}      # canonical → {name → {raw, by_year, unit}}
    for n, d in have:
        unit = d["drivers"]["revenue"].get("unit") or "억원"
        for seg in d["drivers"]["revenue"]["segments"]:
            raw = seg.get("segment", "")
            canon = _match_alias(raw, alias_map)
            if canon is None:
                canon = raw
                unmatched.setdefault(n, []).append(raw)
            if canon not in groups:
                groups[canon] = {}
                order.append(canon)
            groups[canon][n] = {"raw": raw, "by_year": seg.get("by_year", {}), "unit": unit}
    targets = [focus] if focus else order
    by_segment = []
    for canon in targets:
        grp = groups.get(canon)
        if not grp:
            continue
        metrics = {}
        for metric in ("op_margin", "sales", "op"):
            bc = {}
            for n, sd in grp.items():
                f = 1.0 if metric == "op_margin" else UNIT_FACTOR.get(sd.get("unit", "억원"), 1e8) / 1e8
                series = {y: (round(yv[metric], 1) if metric == "op_margin" else round(yv[metric] * f))
                          for y, yv in sd["by_year"].items() if yv.get(metric) is not None}
                if series:
                    bc[n] = series
            if not bc:
                continue
            myears = sorted({y for v in bc.values() for y in v})
            y_latest = myears[-1] if myears else None
            ranking = leader = comove = None
            if y_latest and sum(1 for n in bc if y_latest in bc[n]) >= 2:
                pairs = [(n, bc[n][y_latest]) for n in bc if y_latest in bc[n]]
                ranking = _rank(pairs, higher_is_better=True)      # 매출·영익·영익률 모두 높을수록 우위
                leader = ranking[0]["company"]
                idx = myears.index(y_latest)
                if metric == "op_margin" and idx > 0:              # 영익률 동조/분화(산업 vs 당사)
                    comove = _comove({n: {"values": bc[n]} for n in bc}, y_latest, myears[idx - 1])
            metrics[metric] = {"by_company": bc, "years": myears, "year_latest": y_latest,
                               "ranking_latest": ranking, "leader_latest": leader, "comove_latest": comove}
        by_segment.append({"segment": canon, "companies": grp, "metrics": metrics, "shared": len(grp) >= 2})
    return {"focus": focus, "unit": "억원", "by_segment": by_segment, "unmatched": unmatched}


def _synthesize(ratios_out, cost_structure, segments, names):
    """answer-first 컨설팅 작성용 핵심 사실 추출 — 채팅 요약의 거버닝/sub 메시지 앵커.
      leader_overall: 핵심지표 최다 선두 / outlier: 가장 자주 역행(당사이슈) /
      profit_gap: 영업이익률 선두-후위 격차 / cost_drivers: 비중 격차 큰 비용범주(why) /
      signals: 지표별 산업(동반) vs 당사(역행) 판별."""
    by_id = {r["id"]: r for r in ratios_out}
    KEY = ["operating_margin", "net_margin", "roe", "revenue_growth", "debt_ratio"]
    leads = {n: 0 for n in names}
    idio = {n: 0 for n in names}
    signals = []
    for rid in KEY:
        r = by_id.get(rid)
        if not r or not r.get("ranking_latest"):
            continue
        if r.get("leader_latest"):
            leads[r["leader_latest"]] = leads.get(r["leader_latest"], 0) + 1
        cm = r.get("comove_latest")
        if cm and cm["type"] == "idiosyncratic":
            for c in cm.get("divergent", []):
                idio[c] = idio.get(c, 0) + 1
            signals.append({"metric": r["label"], "type": "당사", "who": cm.get("divergent", [])})
        elif cm and cm["type"] == "macro":
            signals.append({"metric": r["label"], "type": "산업", "label": cm.get("label")})
    profit = by_id.get("operating_margin")
    profit_gap = None
    if profit and profit.get("ranking_latest"):
        profit_gap = {"metric": profit["label"], "year": profit["year_latest"],
                      "leader": profit["leader_latest"], "laggard": profit["laggard_latest"],
                      "gap_pp": profit.get("spread_latest")}
    cost_drivers = []
    if cost_structure:
        ranked = sorted([c for c in cost_structure["categories"] if c["category"] != "기타" and c.get("spread")],
                        key=lambda c: -(c["spread"] or 0))
        for c in ranked[:3]:
            cost_drivers.append({"category": c["category"], "year": c["year_latest"],
                                 "leanest": c["leanest"], "heaviest": c["heaviest"], "gap_pp": c["spread"]})
    seg_focus = None
    if segments and segments.get("focus"):
        fs = next((s for s in segments["by_segment"] if s["segment"] == segments["focus"]), None)
        om = fs and fs["metrics"].get("op_margin")
        if om and om.get("ranking_latest"):
            seg_focus = {"segment": segments["focus"], "year": om["year_latest"],
                         "leader": om["leader_latest"], "ranking": om["ranking_latest"]}
    return {"leader_overall": (max(leads, key=leads.get) if any(leads.values()) else None),
            "leads": leads, "outlier": (max(idio, key=idio.get) if any(idio.values()) else None),
            "profit_gap": profit_gap, "cost_drivers": cost_drivers,
            "segment_focus": seg_focus, "signals": signals}


def compare(docs, segment_focus=None, cost_aliases=None, segment_aliases=None):
    names = _uniq_names(docs)
    years = sorted({y for d in docs for y in d.get("years", [])})
    cost_amap = cost_aliases if cost_aliases is not None else _load_ref("cost-aliases").get("aliases", {})
    seg_amap = segment_aliases if segment_aliases is not None else _load_ref("segment-aliases").get("aliases", {})

    # 비율: id별 메타 + 회사별 시계열 취합
    rmap, order = {}, []
    for d, n in zip(docs, names):
        for r in d.get("ratios", []):
            rid = r["id"]
            if rid not in rmap:
                rmap[rid] = {"id": rid, "label": r["label"], "category": r["category"],
                             "unit": r["unit"], "higher_is_better": r.get("higher_is_better"),
                             "yoy": r.get("yoy", False),
                             "interpret": r.get("interpret", ""), "by_company": {}}
                order.append(rid)
            rmap[rid]["by_company"][n] = {"values": r.get("values", {}), "trend": r.get("trend")}

    ratios_out = []
    for rid in order:
        R = rmap[rid]
        bc = R["by_company"]
        ryears = sorted({y for d in bc.values() for y in d["values"]})
        stats = {}
        for y in ryears:
            vals = [d["values"][y] for d in bc.values() if d["values"].get(y) is not None]
            if vals:
                stats[y] = {"median": round(statistics.median(vals), 2),
                            "mean": round(statistics.mean(vals), 2),
                            "min": min(vals), "max": max(vals), "n": len(vals)}
        y_latest = next((y for y in reversed(ryears) if stats.get(y, {}).get("n", 0) >= 1), None)
        ranking = leader = laggard = spread = comove = None
        if y_latest:
            pairs = [(n, bc[n]["values"][y_latest]) for n in bc if bc[n]["values"].get(y_latest) is not None]
            ranking = _rank(pairs, R["higher_is_better"])
            if ranking:
                leader, laggard = ranking[0]["company"], ranking[-1]["company"]
                vv = [p["value"] for p in ranking]
                spread = round(max(vv) - min(vv), 2)
            if R.get("yoy"):                          # 성장률: 값 부호로(레벨)
                comove = _comove_level(bc, y_latest)
            else:                                      # 레벨 지표: YoY 방향으로(delta)
                idx = ryears.index(y_latest)
                if idx > 0:
                    comove = _comove(bc, y_latest, ryears[idx - 1])
        ratios_out.append({
            "id": R["id"], "label": R["label"], "category": R["category"], "unit": R["unit"],
            "higher_is_better": R["higher_is_better"], "interpret": R["interpret"],
            "by_company": bc, "years": ryears, "peer_stats": stats,
            "year_latest": y_latest, "ranking_latest": ranking,
            "leader_latest": leader, "laggard_latest": laggard,
            "spread_latest": spread, "comove_latest": comove,
        })

    # 금액: 규모 비교 + 점유율(share)
    amounts_out = []
    for acc in AMOUNT_ACCS:
        bc = {}
        for d, n in zip(docs, names):
            ka = d.get("key_amounts", {}).get(acc)
            if ka:
                bc[n] = ka
        if not bc:
            continue
        ayears = sorted({y for v in bc.values() for y in v})
        share = {}
        for y in ayears:
            present = {n: bc[n][y] for n in bc if bc[n].get(y) is not None}
            tot = sum(present.values())
            if tot and acc in ("매출액",):       # 점유율은 매출 기준이 의미(이익은 음수 가능)
                share[y] = {n: round(v / tot * 100, 1) for n, v in present.items()}
        amounts_out.append({"account": acc, "by_company": bc, "years": ayears, "share": share})

    # 경로 B 드라이버: 비용구조·세그먼트(drivers 있을 때만 — 없으면 None, v1 회귀 안전)
    cost_structure = _cost_structure(docs, names, cost_amap)
    segments = _segments(docs, names, seg_amap, segment_focus)
    synthesis = _synthesize(ratios_out, cost_structure, segments, names)   # answer-first 앵커

    # 헤드라인(채팅 요약 보조)
    by_id = {r["id"]: r for r in ratios_out}
    headlines = []
    for hid in HEADLINE_IDS:
        r = by_id.get(hid)
        if not r or not r.get("ranking_latest"):
            continue
        seg = f"{r['label']}({r['year_latest']}): 선두 {r['leader_latest']} · 후위 {r['laggard_latest']}"
        cm = r.get("comove_latest")
        if cm and cm["type"] == "macro":
            seg += f" · {cm['label']} → 산업요인"
        elif cm and cm["type"] == "idiosyncratic":
            seg += f" · {', '.join(cm.get('divergent', []))} 역행 → 당사요인"
        headlines.append(seg)
    cc_cands = [c for c in (cost_structure["categories"] if cost_structure else []) if c["category"] != "기타"]
    if cc_cands:                                               # 비용: 격차 최대 범주 1건('기타' 제외)
        cc = max(cc_cands, key=lambda c: c.get("spread") or 0)
        if cc["year_latest"]:
            headlines.append(f"비용구조·{cc['category']} 비중({cc['year_latest']}): "
                             f"무거움 {cc['heaviest']} · 가벼움 {cc['leanest']} (격차 {cc['spread']}%p)")
    if segments:                                               # 세그: focus/공유 부문 영익률
        for s in segments["by_segment"]:
            m = s["metrics"].get("op_margin")
            if s["shared"] and m and m.get("leader_latest"):
                line = f"세그·{s['segment']} 영업이익률({m['year_latest']}): 선두 {m['leader_latest']}"
                cm = m.get("comove_latest")
                if cm and cm["type"] == "idiosyncratic":
                    line += f" · {', '.join(cm.get('divergent', []))} 역행"
                headlines.append(line)

    # 경고: 비교가능성 점검
    warnings = []
    fsset = {d.get("fs_div") for d in docs}
    if len(fsset) > 1:
        warnings.append(f"연결/별도 혼재({fsset}) — 동일 기준 비교 주의.")
    yset = {tuple(d.get("years", [])) for d in docs}
    if len(yset) > 1:
        warnings.append("회사별 연도 범위 불일치 — 공통 연도 위주 해석.")
    for d, n in zip(docs, names):
        if d.get("warning"):
            warnings.append(f"[{n}] {d['warning']}")
        if d.get("core_missing"):
            warnings.append(f"[{n}] 핵심계정 부재 {d['core_missing']} — 표준비교 부적합.")
    if cost_structure:
        warnings.append("비용구조 비중은 SG&A 명세 단위 가정(기본 백만원)·매출=원 통일에 의존 — 단위 상이 시 왜곡.")
        for n, why in cost_structure.get("suspect", {}).items():
            warnings.append(f"[{n}] {why}")
        um = "; ".join(f"{n}: {', '.join(v[:4])}" for n, v in cost_structure["unmatched"].items() if v)
        if um:
            warnings.append(f"비용 미분류 항목(기타로 합산): {um}")
    if segments:
        warnings.append("세그먼트 비교는 회사별 부문 정의·범위 차이에 유의(같은 명칭도 포함 범위 다를 수 있음).")
        um = "; ".join(f"{n}: {', '.join(v[:4])}" for n, v in segments["unmatched"].items() if v)
        if um:
            warnings.append(f"부문 별칭 미매칭(원명 유지): {um}")

    return {
        "companies": [{
            "name": n, "stock_code": d.get("company", {}).get("stock_code"),
            "corp_code": d.get("company", {}).get("corp_code"), "fs_div": d.get("fs_div"),
            "sources": d.get("rcept_nos") or ([d["rcept_no"]] if d.get("rcept_no") else []),
        } for n, d in zip(names, docs)],
        "years": years, "currency": (docs[0].get("currency") or "KRW") if docs else "KRW",
        "ratios": ratios_out, "amounts": amounts_out,
        "cost_structure": cost_structure, "segments": segments,
        "synthesis": synthesis, "headlines": headlines, "warnings": warnings,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="회사별 ratios.json N개 → 경쟁사 비교 JSON")
    ap.add_argument("--ratios", nargs="+", required=True, help="analyze.py 산출 ratios.json 2개 이상")
    ap.add_argument("--segment-focus", default=None, help="특정 canonical 부문만 격리 비교(예: 편의점)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    docs = [json.load(open(p, encoding="utf-8")) for p in a.ratios]
    if len(docs) < 2:
        sys.exit("비교는 회사 2곳 이상 필요(--ratios a.json b.json ...).")
    res = compare(docs, segment_focus=a.segment_focus)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        nm = " vs ".join(c["name"] for c in res["companies"])
        print(f"✅ 비교: {nm} · {res['years'][0]}–{res['years'][-1]} · 비율 {len(res['ratios'])}개")
        for h in res["headlines"]:
            print("   • " + h)
        for w in res["warnings"]:
            print("   ⚠️ " + w)
    else:
        print(text)
