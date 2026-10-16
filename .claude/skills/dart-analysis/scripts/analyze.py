#!/usr/bin/env python3
"""dart-analysis · 분석 모듈 (네트워크 불필요 — 2단계 재사용)
────────────────────────────────────────────────────────────────────
raw JSON + ratios.yaml + account-aliases.yaml → 비율·추이 계산 → ratios JSON.

  - 계정명은 account-aliases.yaml로 표준명 정규화(표기차 흡수).
  - 비율은 ratios.yaml의 expr를 평가(__builtins__ 제거한 안전 네임스페이스).
  - 매칭 실패 계정은 임의 추정하지 않고 unmatched_accounts로 표면화.

Phase 0 안전장치:
  - YoY 증가율: 전기값이 0/적자(≤0)면 % 무의미 → 계산 생략(전기 적자 시 추세 단정 금지).
  - core_missing: 핵심계정(매출액·영업이익) 부재 시 표면화(금융업/특수구조 신호).
  - collect의 warning을 결과에 전파.

사용:
  python3 analyze.py --raw raw.json --out ratios.json
"""
import argparse, json, os
try:
    import yaml
except ImportError:
    yaml = None   # 챗 등 pyyaml 미설치 환경 → references/*.json 사용(load_yaml fallback)

KEY_AMT = ["매출액", "영업이익", "당기순이익", "영업활동현금흐름"]
CORE = ["매출액", "영업이익"]


def load_yaml(p):
    """yaml 우선. .json이거나 pyyaml 미설치(챗)면 같은 이름 .json 로드."""
    if p.endswith(".json"):
        return json.load(open(p, encoding="utf-8"))
    if yaml is None:
        return json.load(open(p.rsplit(".", 1)[0] + ".json", encoding="utf-8"))
    return yaml.safe_load(open(p, encoding="utf-8"))


def normalize_accounts(raw, aliases):
    """raw accounts → {표준계정명: {year: value}}. 공백 제거 후 별칭 매칭.
    같은 표준명에 여러 raw 계정이 걸리면(연도별 표기 변화: '매출액'↔'수익(매출액)' 등)
    연도값을 병합 — 이미 채워진 연도는 보존(첫 매칭 우선), 빈 연도만 보완.
    (다년도 수집 시 보고서 vintage가 달라 계정명이 갈리는 문제를 흡수)"""
    rev = {}
    for std, variants in (aliases.get("aliases") or {}).items():
        for v in list(variants) + [std]:
            rev[v.replace(" ", "")] = std
    norm = {}
    for acc in raw["accounts"]:
        nm = (acc["account_nm"] or "").replace(" ", "")
        std = rev.get(nm)
        if not std:
            continue
        tgt = norm.setdefault(std, {})
        for y, v in (acc["values"] or {}).items():
            if v is not None and tgt.get(y) is None:
                tgt[y] = v
    return norm


def calc(raw, ratios_doc, aliases):
    norm = normalize_accounts(raw, aliases)
    years = raw["years"]                       # [전전기, 전기, 당기]
    results, unmatched = [], set()
    for r in ratios_doc["ratios"]:
        vals = {}
        for i, y in enumerate(years):
            ns = {"__builtins__": {}}
            ok = True
            for acc in r["accounts"]:
                v = norm.get(acc, {}).get(y)
                if v is None:
                    ok = False
                    if acc not in norm:
                        unmatched.add(acc)
                    break
                ns[acc] = v
                if r.get("yoy"):               # 전기값 필요
                    pv = norm.get(acc, {}).get(years[i - 1]) if i > 0 else None
                    if pv is None or pv <= 0:  # 전기 없음/0/적자 → 증가율 무의미(N/A)
                        ok = False
                        break
                    ns[acc + "_prev"] = pv
            if not ok:
                continue
            try:
                vals[y] = round(eval(r["expr"], ns), 2)    # ns의 __builtins__ 제한
            except Exception:
                continue
        ys = [y for y in years if y in vals]
        trend = None
        if len(ys) >= 2:
            trend = "▲" if vals[ys[-1]] > vals[ys[0]] else ("▼" if vals[ys[-1]] < vals[ys[0]] else "→")
        results.append({
            "id": r["id"], "label": r["label"], "category": r["category"], "unit": r["unit"],
            "values": vals, "trend": trend, "higher_is_better": r.get("higher_is_better"),
            "yoy": bool(r.get("yoy")), "interpret": r.get("interpret", ""), "computed": bool(vals),
        })
    key_amounts = {k: norm[k] for k in KEY_AMT if k in norm}
    core_missing = [c for c in CORE if c not in norm]
    return {
        "company": raw["company"], "fs_div": raw["fs_div"], "currency": raw.get("currency"),
        "rcept_no": raw.get("rcept_no"), "rcept_nos": raw.get("rcept_nos"),
        "reprt_code": raw.get("reprt_code"),
        "warning": raw.get("warning"), "core_missing": core_missing,
        "years": years, "ratios": results,
        "key_amounts": key_amounts, "unmatched_accounts": sorted(unmatched),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="raw JSON → 재무비율 JSON")
    ap.add_argument("--raw", required=True)
    ap.add_argument("--ratios", default=None, help="ratios.yaml (기본: ../references/ratios.yaml)")
    ap.add_argument("--aliases", default=None, help="account-aliases.yaml (기본: ../references/)")
    ap.add_argument("--notes", default=None, help="parse_notes.py 출력(경로B 드라이버) 병합")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ref = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references")
    ratios_doc = load_yaml(a.ratios or os.path.join(ref, "ratios.yaml"))
    aliases = load_yaml(a.aliases or os.path.join(ref, "account-aliases.yaml"))
    raw = json.load(open(a.raw, encoding="utf-8"))
    res = calc(raw, ratios_doc, aliases)
    if a.notes and os.path.exists(a.notes):          # 경로 B 드라이버(판관비·부문 P&L) 병합
        res["drivers"] = json.load(open(a.notes, encoding="utf-8"))
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        n = sum(1 for r in res["ratios"] if r["computed"])
        print(f"✅ 분석: {res['company']['corp_name']} · 비율 {n}/{len(res['ratios'])}개 계산")
        if res["core_missing"]:
            print(f"   ⛔ 핵심계정 부재: {res['core_missing']} → 표준분석 부적합(금융업/특수구조 가능성)")
        if res["warning"]:
            print(f"   ⚠️ {res['warning']}")
        if res["unmatched_accounts"]:
            print(f"   ⚠️ 미매칭 {len(res['unmatched_accounts'])}건: " + ", ".join(res["unmatched_accounts"]))
    else:
        print(text)
