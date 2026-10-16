#!/usr/bin/env python3
"""dart-analysis · 챗 데이터 반입 모듈 (네트워크 불필요 — collect.py의 챗 대체)
────────────────────────────────────────────────────────────────────
claude.ai 챗은 외부 egress가 차단되어 DART 자동 수집이 불가.
DART에서 확보한 재무 수치(CSV)를 raw.json(analyze 호환)으로 변환한다.

CSV 형식 (references/chat-input-template.csv 참고):
  - 1행 헤더: account,<연도>,<연도>,...   (연도는 오래된→최신 순)
  - 각 행: 표준 계정명(account-aliases의 표준명), 연도별 금액(원 단위, 콤마 허용)
  - 빈 칸/'-'/'N/A'는 결측 처리

사용:
  python3 from_input.py --csv input.csv --name "코리아세븐" --stock 000000 --fs CFS --out raw.json
"""
import argparse, csv, json, sys


def to_raw(csv_path, name, stock="", corp_code="", fs="CFS"):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    if len(rows) < 2:
        sys.exit("CSV에 데이터 행이 없음(헤더 + 최소 1계정 필요).")
    years = [h.strip() for h in rows[0][1:] if h.strip()]
    if not years:
        sys.exit("헤더에서 연도 열을 찾지 못함(예: account,2021,2022,...).")
    accounts = []
    for r in rows[1:]:
        nm = r[0].strip()
        if not nm:
            continue
        vals = {}
        for i, y in enumerate(years):
            cell = r[i + 1].replace(",", "").strip() if i + 1 < len(r) else ""
            try:
                vals[y] = float(cell) if cell and cell not in ("-", "N/A", "n/a") else None
            except ValueError:
                vals[y] = None
        accounts.append({"sj_div": "", "sj_nm": "", "account_nm": nm, "values": vals})
    return {
        "company": {"corp_name": name, "stock_code": stock, "corp_code": corp_code, "ceo_nm": "", "corp_cls": "", "induty_code": ""},
        "fs_div": fs, "reprt_code": "manual", "currency": "KRW", "rcept_no": "(수동 입력)",
        "warning": None, "years": years, "accounts": accounts,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="재무 수치 CSV → raw.json (챗 데이터 반입, collect 대체)")
    ap.add_argument("--csv", required=True, help="입력 CSV 경로")
    ap.add_argument("--name", required=True, help="회사명")
    ap.add_argument("--stock", default="", help="종목코드(선택)")
    ap.add_argument("--corp_code", default="", help="고유번호(선택)")
    ap.add_argument("--fs", default="CFS", choices=["CFS", "OFS"], help="연결/별도")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    raw = to_raw(a.csv, a.name, a.stock, a.corp_code, a.fs)
    text = json.dumps(raw, ensure_ascii=False, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(f"✅ raw 생성: {a.name} · {len(raw['accounts'])}계정 · {'–'.join(raw['years'])} → {a.out}")
    else:
        print(text)
