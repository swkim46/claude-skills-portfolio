#!/usr/bin/env python3
"""분석노트의 숫자가 재료에 실제로 있는지 대조한다 — 전사(轉寫) 검증.

왜 필요한가: fact-check는 `[AI]` 줄(재료 **밖**에서 끌어온 사실)만 검증한다.
그런데 매매 판단을 실제로 움직이는 숫자는 대부분 재료 **안**에서 온 것들이라
(2026-09-08 실사례: 9월 인상확률 60.4% · 낸드 +70% QoQ · 자사주 55조 · 비농업 16만2000명)
아무 검증도 받지 않은 채 주문 근거가 된다. 여기서 걸러야 하는 건 두 가지다.

  ① 뉴스레터가 틀렸을 경우 — 2차 자료라 원 기사에서 옮기며 어긋날 수 있다
  ② **내가 잘못 읽었을 경우** — 6만 토큰짜리 재료에서 뽑아 쓴 숫자를 되짚는 단계가 없었다

②가 더 흔하고 더 조용하다. 이 스크립트는 ②만 본다(①은 T1 승격이 맡는다).
네트워크도 LLM 판단도 쓰지 않는다 — 문자열 대조라 결정론이고, 그래서 신뢰할 수 있다.

한계(과대선전 금지): 숫자가 재료에 **있다**는 것만 보증한다. 그 숫자를 **맞는 맥락에
붙였는지**는 보지 못한다("전분기 대비"를 "전년 대비"로 잘못 단 경우는 통과한다).

사용:
    python3 verify_numbers.py analysis/분석노트_260908_kr_v4_0.md \
        --source data/material_260908_kr.md --source data/snapshot_260908_kr.json
    python3 verify_numbers.py <노트> --run 261019 kr      # 이 run의 표준 출처 파일 + 저장소 말뭉치

말뭉치(2026-10-17): `--run`이면 파일 목록에 더해 저장소 텍스트를 같이 본다 — 이번 run의 사실(종류별 한 줄 `facts.render_line` ·
옛 이전 행은 옛 문장 · 원문 인용) · 이번 run 재료(발행분 본문 mat_issue.body · 링크 한 홉 원문 mat_link.body) · 판단 원장
(논지·시나리오·지도·배분·고점 — 예전 journal/*.json 파일 자리). 노트의 값 자리 표식(`<!--F:F-000731-->`)과 사실 id는 숫자로 세지 않는다.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import db

HERE = Path(__file__).parent

# 한국어 자릿수. 큰 것부터 곱해 누적한다("6조8212억" = 6*10^12 + 8212*10^8).
UNIT = {"조": 10 ** 12, "억": 10 ** 8, "만": 10 ** 4}

# 숫자 덩어리 — 쉼표·소수점·한국어 자릿수까지 하나로 잡는다.
NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?(?:[조억만](?:\d[\d,]*(?:\.\d+)?)?)*)")

URL = re.compile(r"https?://\S+")
# 날짜·버전·식별자는 주장이 아니다.
# 헤더·표 구분선·인용블록은 주장이 아니다. 인용블록(`>`)은 이 노트에서 판본 메모로 쓰인다.
SKIP_LINE = re.compile(r"^\s*(?:#{1,6}\s|\||>)")
DATE_LIKE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}\b|\b2\d{5}\b|\bv\d+_\d+\b")
# 라벨을 '쓴' 줄만 — 백틱으로 감싼 `[AI]`는 표기 규칙을 설명한 것이다(옛 extract_ai_claims와 동일 규칙).
AI_TAG = re.compile(r"(?<!`)\[AI\](?!`)")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
HEADER = re.compile(r"^\s*#{1,6}\s")
# 노트의 사실 값 자리 표식·사실 id — 숫자가 아니다(F-000731의 000731을 주장으로 세지 않는다). 값 자체는 남긴다.
FACT_MARKUP = re.compile(r"<!--/?F(?::[^>]*)?-->|\{\{(?:F-\d{6}|SRC-\d+)(?:\.\w+)?\}\}|\b(?:F-\d{6}|SRC-\d+)\b")


def parse_number(tok: str):
    """'6조8212억' → 6821200000000.0 / '1,848,000' → 1848000.0. 실패하면 None."""
    s = tok.replace(",", "")
    total, cur, saw_unit = 0.0, "", False
    for ch in s:
        if ch.isdigit() or ch == ".":
            cur += ch
        elif ch in UNIT:
            if cur:
                total += float(cur) * UNIT[ch]
                cur, saw_unit = "", True
            else:
                return None
        else:
            return None
    if cur:
        total += float(cur)
    return total if (saw_unit or cur) else None


def numbers_in(text: str) -> set:
    """텍스트에 등장하는 모든 수치의 집합."""
    text = URL.sub(" ", text)
    out = set()
    for m in NUM.finditer(text):
        v = parse_number(m.group(1))
        if v is not None:
            out.add(round(v, 4))
    return out


def is_claim_bearing(tok: str, value: float, line: str, pos: int) -> bool:
    """주장을 나르는 숫자만 본다 — 목록 번호·항목 수까지 세면 잡음에 묻힌다.

    기준: 퍼센트가 붙었거나 · 한국어 자릿수가 붙었거나 · 소수점이 있거나 · 1,000 이상.
    """
    after = line[pos + len(tok):pos + len(tok) + 2]
    if after.startswith("%"):
        return True
    if any(u in tok for u in UNIT):
        return True
    if "." in tok:
        return True
    return value >= 1000


# 노트가 "(계산: 1,848,000 ÷ 9,999,644)"처럼 유도 근거를 적어둔 경우.
CALC = re.compile(r"계산:\s*([0-9,\.\s\+\-\*/×÷()]+)")


def _eval_arith(expr: str):
    """사칙연산만 계산한다. eval을 쓰지 않는다 — 노트의 문장은 결국 뉴스레터에서 흘러온
    글자이고, 신뢰할 수 없는 입력에 평가기를 붙이지 않는 것이 이 프로젝트의 원칙이다."""
    import ast
    ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED):
            return None
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return None
    try:
        return eval(compile(tree, "<calc>", "eval"))   # 위 화이트리스트를 통과한 산술식뿐
    except (ZeroDivisionError, OverflowError, ValueError):
        return None


def derived_ok(line: str, value: float) -> bool:
    """'계산:' 근거가 붙어 있으면 **그 식을 실제로 계산해** 값과 맞는지 본다.

    라벨만 보고 통과시키면 "계산이라고 적으면 뭐든 통과"가 되어 검사가 무의미해진다.
    """
    # ★ 한 줄에 `계산:`이 여럿이면 **전부** 본다 — 첫 식만 보면 둘째 값이 미확인으로 잡힌다(2026-09-16).
    for m in CALC.finditer(line):
        expr = m.group(1).replace(",", "").replace("×", "*").replace("÷", "/").strip()
        # 근거를 괄호 안에 적으면("(계산: A ÷ B)") 닫는 괄호까지 딸려온다 → 짝이 맞을 때까지 떼낸다.
        while expr.count(")") > expr.count("("):
            expr = expr[:expr.rfind(")")].strip()
        got = _eval_arith(expr)
        if got is None:
            continue
        # 퍼센트로 적은 경우(0.1848 → 18.5%)도 맞는 것으로 본다.
        for cand in (got, got * 100):
            if abs(cand - value) <= max(0.05, abs(cand) * 0.005):
                return True
    return False


def unit_ok(tok: str, value: float, haystack: set) -> bool:
    """조·억·만 단위로 적은 값은 재료 값과 **노트 자릿수의 절반 안**이면 같은 것으로 본다.

    재료는 `24,326억`, 노트는 `2.4조`처럼 단위를 바꿔 반올림해 적는 일이 흔하다(2026-09-16).
    정확히 같은 실수만 찾으면 이 둘이 다른 숫자로 잡힌다.
    """
    if not any(u in tok for u in UNIT):
        return False
    # 허용 오차 = 노트가 적은 **마지막 자릿수의 절반**. "2.4조"는 0.05조(=2.35~2.45조), "24,326억"은 0.5억.
    m = re.search(r"(\d[\d,]*(?:\.(\d+))?)\s*([조억만])\s*$", tok)
    if not m:
        return False
    decimals = len(m.group(2) or "")
    tol = 0.5 * (10 ** -decimals) * UNIT[m.group(3)]
    return any(abs(h - value) <= tol for h in haystack if isinstance(h, (int, float)) and h)


def ai_claims(note: Path) -> list:
    """`[AI]` 클레임 [(줄번호, 본문)] — 줄바꿈으로 이어진 항목은 합친다(옛 extract_ai_claims.extract와 같은 경계 규칙:
    빈 줄·헤더·새 불릿·새 클레임에서 끊고, 들여쓰기 규칙은 불릿 항목에만 — 문단형 클레임의 둘째 줄을 자르지 않는다)."""
    lines = note.read_text(encoding="utf-8").splitlines()
    claims, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not AI_TAG.search(line):
            i += 1
            continue
        lineno, is_bullet = i + 1, bool(BULLET.match(line))
        body = AI_TAG.sub("", BULLET.sub("", line), count=1).strip()
        indent = len(line) - len(line.lstrip())
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip() or HEADER.match(nxt) or BULLET.match(nxt) or AI_TAG.search(nxt):
                break
            if is_bullet and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body += " " + nxt.strip()
            j += 1
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            claims.append((lineno, body))
        i = j
    return claims


def declared_external(note: Path) -> set:
    """`[AI]`로 선언한 클레임에 등장하는 수치들.

    한 번 "이건 재료 밖 사실"이라고 선언하고 fact-check(5-C)로 보낸 숫자는, 본문 다른
    곳에서 다시 언급할 때마다 태그를 달 필요가 없다. 줄 단위로만 면제하면 여러 줄로
    이어진 클레임의 뒷줄과 그 사실을 되짚는 문장이 전부 걸려 잡음이 된다.
    클레임 경계는 `ai_claims`(옛 extract_ai_claims와 같은 규칙: 빈 줄·헤더·새 불릿·들여쓰기)로 잡는다.
    """
    return {n for _, body in ai_claims(note) for n in numbers_in(FACT_MARKUP.sub(" ", body))}


def db_corpus(stamp: str, mkt: str) -> str:
    """이 run의 저장소 말뭉치 — 사실(한 줄·옛 문장·원문 인용) · 재료(발행분 본문·링크 원문) · 판단 원장(논지·시나리오·지도·배분·고점).
    저장소를 못 읽으면 빈 문자열(파일 출처만으로 대조 — 호출자가 그 사실을 알린다)."""
    rid = db.run_id(stamp, mkt)
    parts = []
    try:
        import facts as _f
        for r in db.q("SELECT f.id, f.kind, f.legacy_text, f.quote FROM ev_fact_run r JOIN ev_fact f ON f.id=r.fact_id"
                      " WHERE r.run_id=? ORDER BY r.s_no", (rid,)):
            parts.append(r["legacy_text"] if r["kind"] == "legacy" else _f.render_line(r["id"]))
            if r.get("quote"):
                parts.append(r["quote"])
        iids = db.material_issues_for_run(rid) or [r["id"] for r in db.q(
            "SELECT id FROM mat_issue WHERE run_id=? OR first_run=?", (rid, rid))]
        for iid in iids:
            r = db.q1("SELECT subject, body FROM mat_issue WHERE id=?", (iid,))
            if r:
                parts += [r.get("subject") or "", r.get("body") or ""]
            for ln in db.q("SELECT id, title FROM mat_link WHERE issue_id=? AND (version_id IS NOT NULL OR body IS NOT NULL)", (iid,)):
                lb, _e = db.link_body(ln["id"])        # 마지막으로 성공한 판(v3) · 옛 행은 옛 body · 실패 사유는 본문이 아니다
                parts += [ln.get("title") or "", lb or ""]
        for name, val in (("theses", db.theses_all()), ("scenarios", db.scenarios_all()), ("market_map", db.market_map_load()),
                          ("allocation", db.allocation_rows()), ("position_peaks", db.peaks_all())):
            parts.append(json.dumps(val, ensure_ascii=False, default=str))
    except db.TestGuard:
        raise
    except Exception as e:                       # noqa: BLE001 — 말뭉치가 비면 파일 출처만으로 본다
        print(f"  (저장소 말뭉치를 못 읽었다: {type(e).__name__}: {str(e)[:100]})", file=sys.stderr)
    return "\n".join(p for p in parts if p)


def check(note: Path, sources: list, texts: tuple = ()) -> list:
    """반환: [(줄번호, 숫자문자열, 줄내용)] — 재료에서 못 찾은 것들. texts = 파일이 아닌 출처 텍스트(저장소 말뭉치)."""
    haystack = declared_external(note)
    for t in texts:
        haystack |= numbers_in(t)
    for s in sources:
        try:
            raw = s.read_text(encoding="utf-8")
        except UnicodeDecodeError:          # DART 공시 원문 등 EUC-KR 저장본
            raw = s.read_bytes().decode("cp949", errors="ignore")
        if s.suffix == ".json":
            # JSON은 숫자가 값으로 들어 있어 문자열 스캔만으로는 놓친다 → 평문화해서 같이 넣는다.
            try:
                raw += "\n" + json.dumps(json.loads(raw), ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        haystack |= numbers_in(raw)

    misses, in_fence = [], False
    for i, line in enumerate(note.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or SKIP_LINE.match(line):
            continue
        # `[AI]` 줄은 애초에 "재료 밖 사실"이라고 선언한 것이다 — 재료에 없는 게 정상이고,
        # 검증은 fact-check(5-C)가 맡는다. 그 줄에 등장한 수치는 haystack에 미리 넣어두었다.
        if AI_TAG.search(line):
            continue
        scrubbed = DATE_LIKE.sub(" ", URL.sub(" ", FACT_MARKUP.sub(" ", line)))
        for m in NUM.finditer(scrubbed):
            tok = m.group(1)
            v = parse_number(tok)
            if v is None or not is_claim_bearing(tok, v, scrubbed, m.start()):
                continue
            if round(v, 4) in haystack or derived_ok(line, v) or unit_ok(tok, v, haystack):
                continue
            misses.append((i, tok, line.strip()))
    return misses


def run_sources(stamp: str, mkt: str, window_days: int = 3) -> list:
    """한 run의 표준 출처 목록 — 매번 손으로 다시 짜지 않는다.

    ★ 2026-09-30·10-01: 출처 목록을 손으로 세 번 다시 짰고(18개·30개), 빠진 파일 때문에 '재료에서
    확인되지 않는 수치'가 났다가 사라지는 왕복이 단계마다 반복됐다. 무엇을 넣는지는 여기 한 곳에서 정한다.
    """
    from datetime import datetime, timedelta
    other = {"us": "kr", "kr": "us"}.get(mkt, "")
    out = []

    text_ext = {".md", ".txt", ".json", ".jsonl", ".html", ".htm", ".csv", ".xml"}

    def add(p: Path):
        # 바이너리(PDF·이미지)는 넣지 않는다 — 텍스트로 읽을 수 없고 숫자 대조에도 쓸모가 없다
        if p.is_file() and p.suffix.lower() in text_ext and p not in out:
            out.append(p)
    try:
        d0 = datetime.strptime(stamp, "%y%m%d")
    except ValueError:
        d0 = None
    for p in sorted((HERE / "data").glob(f"material_*_{mkt}.md")):
        m = re.match(r"material_(\d{6})_", p.name)
        if m and d0:
            d = datetime.strptime(m.group(1), "%y%m%d")
            if d0 - timedelta(days=window_days) <= d <= d0:
                add(p)
    add(HERE / "data" / f"snapshot_{stamp}_{mkt}.json")
    for p in sorted((HERE / "data" / "run_evidence").glob(f"*_{stamp}_{mkt}.*")):
        if not p.name.startswith("verify_"):
            add(p)
    if other:
        add(HERE / "data" / "run_evidence" / f"account_status_{stamp}_{other}.md")
    for p in sorted((HERE / "analysis").glob(f"섹터_*_{stamp}_*.md")):
        add(p)
    add(HERE / "analysis" / f"AI클레임_{stamp}_{mkt}_판정.md")
    for p in sorted((HERE / "analysis").glob(f"AI클레임_{stamp}_{mkt}_[AB]_판정.md")):   # 배경 묶음 판정표(2026-10-14)
        add(p)
    for p in sorted((HERE / "data" / "run_evidence").glob(f"research_branch*_{stamp}_{mkt}*.md")):   # 리서치 갈래 파일
        add(p)
    add(HERE / "journal" / f"carry_{stamp}_{mkt}.md")
    # 원장은 저장소 말뭉치(db_corpus)가 본다 — 남은 렌더본(sources·market_map·theses·자산 곡선)도 있으면 같이 넣는다
    for n in ("sources.json", "market_map.json", "theses.json", "equity_curve.jsonl"):
        add(HERE / "journal" / n)
    for k in ("signal", "approved"):
        add(HERE / "signals" / f"{k}_{stamp}_{mkt}.json")
    add(HERE / "journal" / f"trades_{stamp}_{mkt}.json")
    # 원자료는 수집 창 안의 날짜를 모두 본다 — 상대 시장 종가는 그 시장의 세션 날짜로 저장된다
    # (예: 261001 KR run이 받은 US 09-30 종가 = `KIS_us_closes_260930.json`).
    days = [d0 - timedelta(days=k) for k in range(window_days + 1)] if d0 else []
    for d in days or [None]:
        tag = d.strftime("%y%m%d") if d else stamp
        for p in sorted((HERE / "_raw_sources").glob(f"*{tag}*")):
            add(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="분석노트의 숫자를 재료와 대조한다")
    ap.add_argument("note", help="analysis/분석노트_YYMMDD_mkt_vN_M.md")
    ap.add_argument("--source", action="append", default=[],
                    help="재료 파일(여러 번 지정 가능): material_*.md, snapshot_*.json")
    ap.add_argument("--run", nargs=2, metavar=("STAMP", "MKT"),
                    help="이 run의 표준 출처 목록을 자동으로 쓴다(--source와 함께 쓰면 더한다)")
    args = ap.parse_args()
    if not args.source and not args.run:
        ap.error("--run <stamp> <mkt> 또는 --source가 필요하다")

    note = Path(args.note)
    if not note.is_absolute():
        note = HERE / note
    if not note.exists():
        print(f"분석노트 없음: {note}", file=sys.stderr)
        return 2
    sources = run_sources(args.run[0], args.run[1].lower()) if args.run else []
    texts = ()
    if args.run:
        print(f"표준 출처 {len(sources)}개(--run {args.run[0]} {args.run[1]})", file=sys.stderr)
        corpus = db_corpus(args.run[0], args.run[1].lower())
        texts = (corpus,) if corpus else ()
        print(f"저장소 말뭉치 {len(corpus):,}자(이번 run 사실·재료 본문·판단 원장)", file=sys.stderr)
    for s in args.source:
        p = Path(s)
        if not p.is_absolute():
            p = HERE / p
        if not p.exists():
            print(f"재료 파일 없음: {p}", file=sys.stderr)
            return 2
        if p not in sources:
            sources.append(p)

    misses = check(note, sources, texts)
    if not misses:
        print(f"전사 검증 OK — {note.name}의 수치가 모두 재료에서 확인됨")
        return 0

    print(f"★ 재료에서 확인되지 않는 수치 {len(misses)}건 — {note.name}\n", file=sys.stderr)
    for lineno, tok, line in misses:
        print(f"  L{lineno:<4} {tok:>14}   {line[:88]}", file=sys.stderr)
    print("\n각 건은 셋 중 하나여야 한다:", file=sys.stderr)
    print("  ① 잘못 옮긴 것 → 노트를 고친다", file=sys.stderr)
    print("  ② 재료에서 계산한 값 → 노트에 계산 근거를 적는다(예: '5%+2.04% 합산')", file=sys.stderr)
    print("  ③ 재료 밖 사실 → `[AI]`를 달아 fact-check로 보낸다", file=sys.stderr)
    print("  ※ 자주 걸리는 것: 재료는 억·노트는 조(노트 마지막 자릿수 절반 안이면 통과, 그 밖은 같은 단위로) · 한 줄에 계산식 여럿(전부 봄) ·"
          " 자본 줄 숫자는 `run_evidence/tools_*.md`의 〈자본 기준〉 블록이 출처(--source에 그 파일을 넣어라)",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
