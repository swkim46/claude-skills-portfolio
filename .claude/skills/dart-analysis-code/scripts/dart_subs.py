# -*- coding: utf-8 -*-
"""DART 사업보고서 -> 연결주석 '종속기업 요약재무' 표에서 대상 해외법인 행 추출.
사용: python dart_subs.py <rcpNo> <savePrefix> "<name_regex1>|<name_regex2>|..."
fetch는 curl.exe(브라우저 UA)로 수행(검증된 방식), 파싱은 python.
"""
import sys, re, subprocess, json, os

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
RAW = os.path.dirname(os.path.abspath(__file__))

def curl(url, timeout=120):
    r = subprocess.run(["curl.exe", "-s", "-A", UA, "--max-time", str(timeout), url],
                       capture_output=True)
    # DART는 EUC-KR/UTF-8 혼재 가능 -> utf-8 우선, 실패시 cp949
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return r.stdout.decode("cp949", errors="replace")

def get_fin_node(rcpno):
    html = curl("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=%s" % rcpno)
    # treeData 노드 블록 파싱
    nodes = []
    for m in re.finditer(r"node1\['text'\]\s*=\s*\"(.*?)\";.*?node1\['dcmNo'\]\s*=\s*\"(\d+)\";"
                         r".*?node1\['eleId'\]\s*=\s*\"(\d+)\";.*?node1\['offset'\]\s*=\s*\"(\d+)\";"
                         r".*?node1\['length'\]\s*=\s*\"(\d+)\";.*?node1\['dtd'\]\s*=\s*\"(.*?)\";",
                         html, re.DOTALL):
        text, dcm, ele, off, length, dtd = m.groups()
        nodes.append(dict(text=text, dcmNo=dcm, eleId=ele, offset=off, length=length, dtd=dtd))
    # '재무에 관한 사항' 노드 선택 (가장 큰 length = 재무제표 본문)
    fin = [n for n in nodes if "재무에 관한 사항" in n["text"]]
    if not fin:
        return None, nodes
    fin.sort(key=lambda n: int(n["length"]), reverse=True)
    return fin[0], nodes

def fetch_section(rcpno, node):
    url = ("https://dart.fss.or.kr/report/viewer.do?rcpNo=%s&dcmNo=%s&eleId=%s&offset=%s&length=%s&dtd=%s"
           % (rcpno, node["dcmNo"], node["eleId"], node["offset"], node["length"], node["dtd"]))
    return curl(url, timeout=180)

def cell_text(td):
    t = re.sub(r"<[^>]+>", " ", td)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()

def parse_rows(html):
    """모든 <TR> 행 -> 셀 리스트. period 마커도 함께 추적."""
    # 표/마커를 순서대로 스캔
    out = []  # list of (kind, payload) ; kind in {marker, row}
    pos = 0
    # 토큰: 제NN(당/전)기 마커 또는 한 행(<TR>...</TR>)
    token_re = re.compile(r"(제\s*\d+\s*\([당전]\)기)|(<TR[ >].*?</TR>)", re.DOTALL | re.IGNORECASE)
    for m in token_re.finditer(html):
        if m.group(1):
            out.append(("marker", re.sub(r"\s+", "", m.group(1))))
        else:
            cells = [cell_text(c) for c in re.findall(r"<T[DH][ >].*?</T[DH]>", m.group(2), re.DOTALL | re.IGNORECASE)]
            out.append(("row", cells))
    return out

def is_num(s):
    return bool(re.fullmatch(r"\(?-?[\d,]+\)?", s.strip())) and any(ch.isdigit() for ch in s)

def main():
    rcpno = sys.argv[1]; prefix = sys.argv[2]; names = sys.argv[3]
    name_re = re.compile(names, re.IGNORECASE)
    node, nodes = get_fin_node(rcpno)
    if node is None:
        print("NO_FIN_NODE; nodes=", [n["text"] for n in nodes]); return
    html = fetch_section(rcpno, node)
    path = os.path.join(RAW, prefix + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("SAVED", path, "bytes=", len(html))
    tokens = parse_rows(html)
    cur_period = None
    last_header = None
    results = []
    seen = set()  # (period, name) 중복 제거 — 첫 출현(연결주석 요약표)만
    for kind, payload in tokens:
        if kind == "marker":
            cur_period = payload
            continue
        cells = payload
        joined = " ".join(cells)
        if ("총자산" in joined and "총부채" in joined) or ("자산총계" in joined and "부채총계" in joined):
            last_header = cells
            continue
        if cells and name_re.search(cells[0]):
            tail = cells[1:]
            # 요약재무 행 판정: 이름 뒤 첫 셀이 숫자/대시 (현황·장부금액·소재지 표 배제)
            if not tail or not (is_num(tail[0]) or tail[0].strip() == "-"):
                continue
            nums = [c for c in tail if is_num(c) or c.strip() == "-"]
            if len(nums) < 5:
                continue
            key = (cur_period, cells[0])
            if key in seen:
                continue
            seen.add(key)
            results.append(dict(period=cur_period, name=cells[0], header=last_header, cells=cells))
    jpath = os.path.join(RAW, prefix + "_subs.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("MATCHES:", len(results))
    for r in results:
        # ASCII-safe period (제NN(당/전)기 -> period digits + dang/jeon)
        per = r["period"] or "?"
        tag = "DANG" if "당" in per else ("JEON" if "전" in per else "?")
        gi = re.search(r"\d+", per)
        gi = gi.group(0) if gi else "?"
        vals = [c for c in r["cells"][1:] if is_num(c) or c.strip() == "-"]
        print("  [%s gi=%s] %s | %s" % (tag, gi, r["name"], " / ".join(vals[:6])))

if __name__ == "__main__":
    main()
