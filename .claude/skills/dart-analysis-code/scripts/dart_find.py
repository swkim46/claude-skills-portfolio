# -*- coding: utf-8 -*-
"""회사명으로 DART 사업보고서 접수번호+접수일자 목록 조회.
사용: python dart_find.py "증권사 B증권"
"""
import sys, re, subprocess

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def search(name):
    r = subprocess.run([
        "curl.exe", "-s", "-A", UA, "--max-time", "40",
        "https://dart.fss.or.kr/dsab007/detailSearch.ax",
        "--data-urlencode", "currentPage=1",
        "--data-urlencode", "maxResults=100",
        "--data-urlencode", ("textCrpNm=" + name),
        "--data-urlencode", "startDate=20220101",
        "--data-urlencode", "endDate=20260601",
        "--data-urlencode", "reportName=사업보고서",
    ], capture_output=True)
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return r.stdout.decode("cp949", errors="replace")

def main():
    name = sys.argv[1]
    html = search(name)
    # 각 행: rcpNo + 회사명 + 보고서명 + 접수일자(YYYY.MM.DD)
    # 행 단위로 분리해 rcpNo와 같은 행 근방의 날짜/회사명을 묶음
    rows = re.split(r"</tr>", html, flags=re.IGNORECASE)
    print("NAME:", name, "| rows:", len(rows))
    for row in rows:
        m = re.search(r"rcpNo=(\d+)", row)
        if not m:
            continue
        rcp = m.group(1)
        # 회사명 (corp_name) — title 또는 텍스트
        cm = re.search(r"openCorpInfoNew\('(\d+)','([^']*)'", row)
        corp = ""
        if cm:
            corp = cm.group(2)
        # 보고서명 + 정정여부
        rep = "사업보고서" if "사업보고서" in row else ""
        amend = "[정정]" if ("기재정정" in row or "첨부정정" in row) else ""
        # 접수일자
        dm = re.search(r"(20\d{2}\.\d{2}\.\d{2})", row)
        date = dm.group(1) if dm else ""
        txt = re.sub(r"<[^>]+>", " ", row)
        txt = re.sub(r"\s+", " ", txt).strip()
        # 보고서명 (괄호 안 연도)
        repm = re.search(r"사업보고서\s*\((20\d{2}\.\d{2})\)", txt)
        repyear = repm.group(1) if repm else ""
        print("  rcpNo=%s | %s | %s | %s %s | %s" % (rcp, date, corp, rep, amend, repyear))

if __name__ == "__main__":
    main()
