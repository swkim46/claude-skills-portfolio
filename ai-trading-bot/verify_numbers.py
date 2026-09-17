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
"""
import argparse
import json
import re
import sys
from pathlib import Path

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
# 라벨을 '쓴' 줄만 — 백틱으로 감싼 `[AI]`는 표기 규칙을 설명한 것이다(extract_ai_claims와 동일 규칙).
AI_TAG = re.compile(r"(?<!`)\[AI\](?!`)")


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


def declared_external(note: Path) -> set:
    """`[AI]`로 선언한 클레임에 등장하는 수치들.

    한 번 "이건 재료 밖 사실"이라고 선언하고 fact-check(5-C)로 보낸 숫자는, 본문 다른
    곳에서 다시 언급할 때마다 태그를 달 필요가 없다. 줄 단위로만 면제하면 여러 줄로
    이어진 클레임의 뒷줄과 그 사실을 되짚는 문장이 전부 걸려 잡음이 된다.
    클레임 경계는 `extract_ai_claims`와 같은 규칙(빈 줄·헤더·새 불릿·들여쓰기)으로 잡는다.
    """
    try:
        from extract_ai_claims import extract
    except ImportError:
        return set()
    return {n for _, body in extract(note) for n in numbers_in(body)}


def check(note: Path, sources: list) -> list:
    """반환: [(줄번호, 숫자문자열, 줄내용)] — 재료에서 못 찾은 것들."""
    haystack = declared_external(note)
    for s in sources:
        raw = s.read_text(encoding="utf-8")
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
        scrubbed = DATE_LIKE.sub(" ", URL.sub(" ", line))
        for m in NUM.finditer(scrubbed):
            tok = m.group(1)
            v = parse_number(tok)
            if v is None or not is_claim_bearing(tok, v, scrubbed, m.start()):
                continue
            if round(v, 4) in haystack or derived_ok(line, v) or unit_ok(tok, v, haystack):
                continue
            misses.append((i, tok, line.strip()))
    return misses


def main() -> int:
    ap = argparse.ArgumentParser(description="분석노트의 숫자를 재료와 대조한다")
    ap.add_argument("note", help="analysis/분석노트_YYMMDD_mkt_vN_M.md")
    ap.add_argument("--source", action="append", required=True,
                    help="재료 파일(여러 번 지정 가능): material_*.md, snapshot_*.json")
    args = ap.parse_args()

    note = Path(args.note)
    if not note.is_absolute():
        note = HERE / note
    if not note.exists():
        print(f"분석노트 없음: {note}", file=sys.stderr)
        return 2
    sources = []
    for s in args.source:
        p = Path(s)
        if not p.is_absolute():
            p = HERE / p
        if not p.exists():
            print(f"재료 파일 없음: {p}", file=sys.stderr)
            return 2
        sources.append(p)

    misses = check(note, sources)
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
