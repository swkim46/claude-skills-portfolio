#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스킬 구성 참고 자료 생성기 — 저장소의 스킬 파일에서 읽어 docs/reference/스킬_구성.md 의 생성 블록을 갱신한다.

    python3 tools/skill_graph.py            # <!-- skill-graph:begin --> … end 블록을 다시 쓴다
    python3 tools/skill_graph.py --check    # 문서의 블록이 파일 상태와 같은지만 확인(다르면 종료코드 1)
    python3 tools/skill_graph.py --print    # 블록을 표준 출력으로

읽는 것: .claude/skills/<이름>/SKILL.md 의 이름, _stepgate/rubrics/<이름>.json 의 검사 지점 이름, audit_rubrics/<이름>.json 의
감사 항목 수, SKILL.md 본문이 다른 스킬 이름을 언급하는지. 검사 기준표는 상세 판정 조건을 출력하지 않고 항목 이름과 개수만
집계한다(_shared/check_skill_integrity.py 와 같은 방식). 역할 설명, 범위를 나눈 짝, 공유 자료는 아래 상수에 적어 두었다(각 스킬
문서의 범위 문장이 근거). '본문에서 언급'은 텍스트 참조이며 실행 의존 관계가 아니다. 표준 라이브러리만 쓴다.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
DOC = ROOT / "docs" / "reference" / "스킬_구성.md"
LINK = "../../.claude/skills"
BEGIN, END = "<!-- skill-graph:begin -->", "<!-- skill-graph:end -->"

LAYERS = {
    "일상·투자": ["world-study", "trade-run", "trade-feedback", "fact-check", "life-research", "life-map"],
    "컨설팅 리서치": ["research-verified", "research-source-check", "research-docx", "research-organizer", "knowledge-map",
                 "consulting-interview-note", "expert-vq-compiler", "dart-analysis-code", "dart-analysis"],
    "스킬 수정·감사": ["skill-maintainer", "gate-audit"],
}
LAYER_ID = {"일상·투자": "L1", "컨설팅 리서치": "L2", "스킬 수정·감사": "L3"}
ROLE = {
    "world-study": "구독 뉴스레터를 읽고 사실·원인·개념을 층으로 정리한 학습 노트를 쓴다",
    "trade-run": "뉴스·시세로 투자 시나리오를 세우고 모의매매 주문까지 여섯 단계로 실행한다",
    "trade-feedback": "매주 놓친 상승·매도 시점·손실 거래를 당시 기록과 대조해 규칙 변경을 제안하고 다음 주 재측정한다",
    "fact-check": "작성된 일상·실무 산출물의 출처 접근·일치·시효·자격을 네 값으로 판정한다",
    "life-research": "일상·실무 주제를 원문 직접 확인으로 조사해 출처 단 보고서를 만든다",
    "life-map": "개인 프로젝트 폴더의 지식지도를 만들고 변화만 반영한다",
    "research-verified": "컨설팅 리서치를 다중 검색·원문 확인·재검증으로 수행하고 검증 방법론 가이드로 결과를 확인한다",
    "research-source-check": "완성된 컨설팅 산출물의 링크·수치·인용·단위를 출처 원문과 대조해 감리한다",
    "research-docx": "검증된 내용을 컨설팅 하우스 스타일 Word 보고서로 조판한다",
    "research-organizer": "정리된 리서치를 컨설팅 양식의 Excel 산출물로 만든다",
    "knowledge-map": "컨설팅 워크스트림 폴더의 지식지도를 만들고 변화만 반영한다",
    "consulting-interview-note": "녹취 텍스트와 메타데이터로 표준 양식의 인터뷰 회의록 Word 문서를 만든다",
    "expert-vq-compiler": "전문가 네트워크의 사전 검증 답변 메일을 추적 스프레드시트로 정리한다",
    "dart-analysis-code": "공시 API로 재무제표를 수집해 비율·추세를 계산한다(dart-analysis 도구의 실행 절차)",
    "dart-analysis": "공시 재무제표 수집·비율·추세·Excel 산출의 원본 도구(팀 공유, 검사 없이 호출)",
    "skill-maintainer": "스킬을 고칠 때의 절차 — 백업·편집 전후 누락 대조·코드 편집 검사·테스트 기록·정합성 검사",
    "gate-audit": "끝난 작업의 검사 준수를 사후 점검하고, 산출물을 배경에서 항목별로 검수한다",
}
PAIRS = [("life-research", "research-verified"), ("fact-check", "research-source-check"), ("life-map", "knowledge-map")]
SHARED = {  # 공유 자료 → (정본을 가진 곳, 사용하는 스킬)
    "폴더 읽기 스크립트(_shared/scan_folders.py)": ("_shared", ["life-map", "knowledge-map"]),
    "검증 방법론 가이드": ("research-verified", ["research-docx", "life-research"]),
    "Excel 서식 코드(scripts/styles.py)": ("research-organizer", ["research-docx"]),
    "공시 분석 도구(dart-analysis)": ("dart-analysis", ["dart-analysis-code"]),
}
ENGINE, AUDITOR, MAINTAINER = "단계 검사 엔진 _stepgate", "gate-audit", "skill-maintainer"


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out, key = {}, None
    if m:
        for ln in m.group(1).splitlines():
            if ln.startswith((" ", "\t")) and key:
                out[key] = (out[key] + " " + ln.strip()).strip()
            elif ":" in ln:
                k, v = ln.split(":", 1)
                key = k.strip(); v = v.strip().strip('"').strip("'")
                out[key] = "" if v in (">-", ">", "|-", "|") else v
    return out


def checkpoints(name):
    f = SKILLS / "_stepgate" / "rubrics" / f"{name}.json"
    if not f.exists():
        return []
    d = json.load(open(f, encoding="utf-8"))
    return [k for k, v in d.items() if not k.startswith("_") and isinstance(v, dict)]


def audit_items(name):
    f = SKILLS / "_stepgate" / "audit_rubrics" / f"{name}.json"
    if not f.exists():
        return 0
    d = json.load(open(f, encoding="utf-8"))
    n = 0
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(v.get("lenses"), list):
            n += len(v["lenses"])
    return n


def collect():
    names = [n for layer in LAYERS.values() for n in layer]
    skills = {}
    for n in names:
        p = SKILLS / n / "SKILL.md"
        if not p.exists():
            raise SystemExit(f"[없음] {p}")
        text = p.read_text(encoding="utf-8")
        fm = frontmatter(text)
        uses_gate = bool(re.search(r"stepgate\.py|`SG`|SG form|SG judge", text))
        mentions = sorted(m for m in names if m != n and re.search(rf"(?<![\w-]){re.escape(m)}(?![\w-])", text))
        skills[n] = {"name": fm.get("name", n), "role": ROLE.get(n, ""), "cps": checkpoints(n), "audit": audit_items(n),
                     "gate": uses_gate, "mentions": mentions}
    return skills


def nid(s):
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def diagram_engine(skills):
    L = ["```mermaid", "graph LR"]
    for layer, names in LAYERS.items():
        L.append(f'  subgraph {LAYER_ID[layer]}["{layer}"]')
        for n in names:
            L.append(f'    {nid(n)}["{n}"]')
        L.append("  end")
    L.append(f'  ENGINE(["{ENGINE}"])')
    for n, s in skills.items():
        if s["gate"]:
            L.append(f"  {nid(n)} --> ENGINE")
    L.append("```")
    return "\n".join(L)


def diagram_shared(skills):
    L = ["```mermaid", "graph LR"]
    L.append('  SHARED(["_shared 공용 스크립트"])')
    seen = set()
    for res, (owner, users) in SHARED.items():
        src = "SHARED" if owner == "_shared" else nid(owner)
        for u in users:
            for x in ([owner] if owner != "_shared" else []) + [u]:
                if x not in seen:
                    L.append(f'  {nid(x)}["{x}"]'); seen.add(x)
            L.append(f'  {src} -->|"{res}"| {nid(u)}')
    L.append("```")
    return "\n".join(L)


def diagram_audit(skills):
    L = ["```mermaid", "graph LR", f'  {nid(AUDITOR)}["{AUDITOR}"]']
    for n, s in skills.items():
        if s["audit"] and n != AUDITOR:
            L.append(f'  {nid(n)}["{n}"]')
            L.append(f"  {nid(AUDITOR)} -.->|감사 항목 {s['audit']}| {nid(n)}")
    L.append("```")
    return "\n".join(L)


def table_roles(skills):
    rows = ["| 스킬 | 영역 | 역할 | 검사 지점 | 감사 항목 수 |", "|---|---|---|---|---|"]
    for layer, names in LAYERS.items():
        for n in names:
            s = skills[n]
            cps = "·".join(s["cps"]) if s["cps"] else ("엔진 사용" if s["gate"] else "없음(검사 없이 호출)")
            rows.append(f"| [{n}]({LINK}/{n}/SKILL.md) | {layer} | {s['role']} | {cps} | {s['audit'] or '—'} |")
    return "\n".join(rows)


def table_relations(skills):
    pair = {}
    for a, b in PAIRS:
        pair[a] = b; pair[b] = a
    owns, uses = {}, {}
    for res, (owner, users) in SHARED.items():
        if owner != "_shared":
            owns.setdefault(owner, []).append(res)
        for u in users:
            uses.setdefault(u, []).append(res)
    rows = ["| 스킬 | 범위를 나눈 짝 | 정본을 가진 공유 자료 | 사용하는 공유 자료 | 본문에서 언급하는 스킬(실행 의존 아님) |", "|---|---|---|---|---|"]
    for layer, names in LAYERS.items():
        for n in names:
            s = skills[n]
            rows.append(f"| {n} | {pair.get(n, '—')} | {' · '.join(owns.get(n, [])) or '—'} | {' · '.join(uses.get(n, [])) or '—'} | {', '.join(s['mentions']) or '—'} |")
    return "\n".join(rows)


def block(skills):
    total_cps = sum(len(s["cps"]) for s in skills.values())
    total_audit = sum(s["audit"] for s in skills.values())
    n_gate = sum(1 for s in skills.values() if s["gate"])
    head = (f"{date.today().isoformat()} 기준 집계: 스킬 {len(skills)}개, 단계 검사 엔진 사용 {n_gate}개, 검사 지점 {total_cps}개, 감사 항목 {total_audit}개. "
            f"검사 기준표의 상세 판정 조건은 출력하지 않고 항목 이름과 개수만 집계했다. 구현 규모를 나타내는 수치이며 효과 지표가 아니다.")
    return "\n".join([
        BEGIN, head, "",
        "### 1. 검사 엔진 연결", "", diagram_engine(skills), "",
        "### 2. 공통 자료의 재사용", "", diagram_shared(skills), "",
        "### 3. 감사 대상과 항목 수", "", diagram_audit(skills), "",
        "### 4. 스킬별 역할과 검사 현황", "", table_roles(skills), "",
        "### 5. 공유 자료와 참조 관계", "", table_relations(skills),
        END,
    ])


def main():
    skills = collect()
    new = block(skills)
    if "--print" in sys.argv:
        print(new); return 0
    text = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    if BEGIN not in text or END not in text:
        raise SystemExit(f"[없음] {DOC} 에 {BEGIN} … {END} 블록이 없다")
    cur = text[text.index(BEGIN): text.index(END) + len(END)]
    if "--check" in sys.argv:
        # 기준 날짜 줄은 생성일에 따라 달라지므로 비교에서 뺀다
        strip = lambda s: "\n".join(l for l in s.splitlines() if not l.endswith("효과 지표가 아니다."))
        if strip(cur).strip() == strip(new).strip():
            print("skill-graph: 문서와 파일 상태가 같다"); return 0
        print("skill-graph: 문서 블록이 파일 상태와 다르다 — tools/skill_graph.py 를 다시 돌려라"); return 1
    DOC.write_text(text.replace(cur, new), encoding="utf-8")
    print(f"skill-graph: {DOC.relative_to(ROOT)} 블록 갱신 — 스킬 {len(skills)}개"); return 0


if __name__ == "__main__":
    sys.exit(main())
