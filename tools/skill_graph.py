#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스킬 관계도 생성기 — 저장소의 스킬 파일에서 관계를 읽어 docs의 생성 블록(Mermaid 그림 + 표)을 갱신한다.

    python3 tools/skill_graph.py            # docs/06_스킬_운영_원칙.md 의 <!-- skill-graph:begin --> … end 블록을 다시 쓴다
    python3 tools/skill_graph.py --check    # 문서의 블록이 파일 상태와 같은지만 확인(다르면 종료코드 1)
    python3 tools/skill_graph.py --print    # 블록을 표준 출력으로

읽는 것: .claude/skills/<이름>/SKILL.md 의 이름·설명, _stepgate/rubrics/<이름>.json 의 검사 지점 **이름만**(기준·관찰 항목은 열지 않는다 —
봉인된 합격 기준을 읽지 않기 위해 _shared/check_skill_integrity.py 와 같은 방식), audit_rubrics/<이름>.json 의 감사 관점 수,
SKILL.md 본문이 다른 스킬을 언급하는지. 자매 스킬 쌍과 공유 자원은 아래 상수에 적어 두었다(스킬 문서의 스코프 문장이 근거).
표준 라이브러리만 쓴다.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
DOC = ROOT / "docs" / "06_스킬_운영_원칙.md"
BEGIN, END = "<!-- skill-graph:begin -->", "<!-- skill-graph:end -->"

LAYERS = {
    "일상·투자": ["world-study", "trade-run", "trade-feedback", "fact-check", "life-research", "life-map"],
    "컨설팅 리서치": ["research-verified", "research-source-check", "research-docx", "research-organizer", "knowledge-map",
                 "consulting-interview-note", "expert-vq-compiler", "dart-analysis-code", "dart-analysis"],
    "메타(스킬을 다루는 스킬)": ["skill-maintainer", "gate-audit"],
}
SISTERS = [("life-research", "research-verified"), ("fact-check", "research-source-check"), ("life-map", "knowledge-map")]
SHARED = {  # 공유 자원 → (가진 스킬, 쓰는 스킬들)
    "_shared/scan_folders.py": ("_shared", ["life-map", "knowledge-map"]),
    "검증 방법론 가이드": ("research-verified", ["research-docx", "life-research"]),
    "scripts/styles.py": ("research-organizer", ["research-docx"]),
    "dart-analysis 엔진": ("dart-analysis", ["dart-analysis-code"]),
}
ENGINE, AUDITOR, MAINTAINER = "게이트 엔진 _stepgate", "gate-audit", "skill-maintainer"
LAYER_ID = {"일상·투자": "L1", "컨설팅 리서치": "L2", "메타(스킬을 다루는 스킬)": "L3"}
ROLE = {  # 표의 '역할' — 스킬 설명문을 평이한 말로 줄인 것
    "world-study": "구독 뉴스레터를 읽고 사실·원인·개념을 층으로 정리한 학습 노트를 쓴다",
    "trade-run": "뉴스·시세로 투자 시나리오를 세우고 모의매매 주문까지 여섯 단계로 실행한다",
    "trade-feedback": "매주 놓친 상승·매도 시점·손실 거래를 당시 기록과 대조해 규칙 변경을 제안하고 다음 주 재측정한다",
    "fact-check": "작성된 일상·실무 산출물의 출처 접근·일치·시효·자격을 네 값으로 판정한다",
    "life-research": "일상·실무 주제를 원문 직접 확인으로 조사해 출처 단 보고서를 만든다",
    "life-map": "개인 프로젝트 폴더의 지식지도를 만들고 변화만 반영한다",
    "research-verified": "컨설팅 리서치를 다중 검색·원문 확인·적대적 재검증으로 수행하고 검증 방법론을 후처리로 강제한다",
    "research-source-check": "완성된 컨설팅 산출물의 링크·수치·인용·단위를 출처 원문과 대조해 감리한다",
    "research-docx": "검증된 내용을 컨설팅 하우스 스타일 Word 보고서로 조판한다",
    "research-organizer": "정리된 리서치를 컨설팅 양식의 Excel 산출물로 만든다",
    "knowledge-map": "컨설팅 워크스트림 폴더의 지식지도를 만들고 변화만 반영한다",
    "consulting-interview-note": "녹취 텍스트와 메타데이터로 표준 양식의 인터뷰 회의록 Word 문서를 만든다",
    "expert-vq-compiler": "전문가 네트워크의 사전 검증 답변 메일을 추적 스프레드시트로 정리한다",
    "dart-analysis-code": "공시 API로 재무제표를 수집해 비율·추세를 계산한다(dart-analysis 엔진의 얇은 실행 래퍼)",
    "dart-analysis": "공시 재무제표 수집·비율·추세·Excel 산출의 원본 엔진(원본 불변·팀 공유)",
    "skill-maintainer": "스킬 자체를 고칠 때의 절차 — 백업·무손실 대조·코드 편집 검사·테스트 기록·정합성 검사",
    "gate-audit": "끝난 작업의 검사 준수를 사후 감사하고, 산출물을 배경에서 관점별로 적대적 검수한다",
}


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out, key = {}, None
    if m:
        for ln in m.group(1).splitlines():
            if ln.startswith((" ", "\t")) and key:          # 블록 스칼라(>- / |-)의 이어지는 줄
                out[key] = (out[key] + " " + ln.strip()).strip()
            elif ":" in ln:
                k, v = ln.split(":", 1)
                key = k.strip(); v = v.strip().strip('"').strip("'")
                out[key] = "" if v in (">-", ">", "|-", "|") else v
    return out


def first_sentence(desc):
    desc = re.sub(r"\*\*|`", "", desc)
    m = re.search(r"^(.{10,120}?(다|니다|함|됨)\.)\s", desc + " ")
    return (m.group(1) if m else desc[:90]).strip()


def checkpoints(name):
    f = SKILLS / "_stepgate" / "rubrics" / f"{name}.json"
    if not f.exists():
        return []
    d = json.load(open(f, encoding="utf-8"))
    return [k for k, v in d.items() if not k.startswith("_") and isinstance(v, dict)]


def audit_lenses(name):
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
        elif isinstance(v, list):
            n += len(v)
        elif isinstance(v, dict):
            n += len([x for x in v.values() if isinstance(x, (dict, list))])
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
        skills[n] = {"name": fm.get("name", n), "desc": ROLE.get(n) or first_sentence(fm.get("description", "")), "cps": checkpoints(n),
                     "lenses": audit_lenses(n), "gate": uses_gate, "mentions": mentions,
                     "audited": "gate-audit" in mentions or audit_lenses(n) > 0}
    return skills


def nid(s):
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def mermaid(skills):
    L = ["```mermaid", "graph LR"]
    for layer, names in LAYERS.items():
        L.append(f'  subgraph {LAYER_ID[layer]}["{layer}"]')
        for n in names:
            L.append(f'    {nid(n)}["{n}"]')
        L.append("  end")
    L.append(f'  ENGINE(["{ENGINE}"])')
    L.append('  SHARED(["_shared 공유 스크립트"])')
    for n, s in skills.items():
        if s["gate"] and n != MAINTAINER:
            L.append(f"  {nid(n)} -->|검사| ENGINE")
    L.append(f"  {nid(MAINTAINER)} -->|자기 검사| ENGINE")
    for n, s in skills.items():
        if s["lenses"] and n != AUDITOR:
            L.append(f"  {nid(AUDITOR)} -.->|감사 관점 {s['lenses']}| {nid(n)}")
    L.append(f"  {nid(MAINTAINER)} ==>|편집 절차·정합성 검사| SHARED")
    for a, b in SISTERS:
        L.append(f"  {nid(a)} <-.->|자매·경계| {nid(b)}")
    for res, (owner, users) in SHARED.items():
        src = "SHARED" if owner == "_shared" else nid(owner)
        for u in users:
            L.append(f'  {src} -->|"{res}"| {nid(u)}')
    L.append("```")
    return "\n".join(L)


def table(skills):
    rows = ["| 스킬 | 영역 | 역할 | 검사 지점 | 감사 관점 | 자매 스킬 | 공유 자원 | 본문이 언급하는 스킬 |", "|---|---|---|---|---|---|---|---|"]
    sister = {}
    for a, b in SISTERS:
        sister[a] = b; sister[b] = a
    shared_of = {}
    for res, (owner, users) in SHARED.items():
        for u in [owner] + users:
            shared_of.setdefault(u, []).append(res)
    for layer, names in LAYERS.items():
        for n in names:
            s = skills[n]
            cps = "·".join(s["cps"]) if s["cps"] else ("엔진 사용" if s["gate"] else "—")
            rows.append(f"| [{n}](../.claude/skills/{n}/SKILL.md) | {layer} | {s['desc']} | {cps} | {s['lenses'] or '—'} | "
                        f"{sister.get(n, '—')} | {' · '.join(shared_of.get(n, [])) or '—'} | {', '.join(s['mentions']) or '—'} |")
    return "\n".join(rows)


def block(skills):
    total_cps = sum(len(s["cps"]) for s in skills.values())
    total_lens = sum(s["lenses"] for s in skills.values())
    head = (f"스킬 {len(skills)}개 · 검사 지점 {total_cps}개 · 감사 관점 {total_lens}개 (`tools/skill_graph.py`가 파일에서 읽어 생성 — "
            f"검사 기준의 내용은 열지 않고 이름만 센다)")
    return "\n".join([BEGIN, head, "", mermaid(skills), "", table(skills), END])


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
        if cur.strip() == new.strip():
            print("skill-graph: 문서와 파일 상태가 같다"); return 0
        print("skill-graph: 문서 블록이 파일 상태와 다르다 — tools/skill_graph.py 를 다시 돌려라"); return 1
    DOC.write_text(text.replace(cur, new), encoding="utf-8")
    print(f"skill-graph: {DOC.name} 블록 갱신 — 스킬 {len(skills)}개"); return 0


if __name__ == "__main__":
    sys.exit(main())
