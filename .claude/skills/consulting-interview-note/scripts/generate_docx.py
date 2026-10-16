#!/usr/bin/env python3
"""
컨설팅 펌 인터뷰 미팅록 docx 생성 헬퍼.

참조 미팅록(references/reference_example.docx)의 시각적 구조를 복제합니다.
- 헤더(일시/참석자): 일반 텍스트 2~3줄
- 이모지 헤더(📌, 🎯, 🔑): bold 텍스트
- 번호 섹션(1., 2., ...): bold 텍스트
- 본문 bullet: List Bullet 스타일
- 표: 자동 생성 (구분/내용 헤더 행 bold)
- 본문 내 `**…**` 마크다운: bold run으로 변환

사용법:
    python3 generate_docx.py --output OUT.docx --json INPUT.json

INPUT.json 스키마 (예시):
{
  "meta": {
    "meeting_title": "PE신기술투자부 미팅 w/ 인터뷰이 A 이사",   // (선택) 멀티미팅 마스터 문서용 구분선
    "date": "2026.06.01(월) 오후 2시",
    "attendees": [                       // 순서: 고객사 인원 먼저 → 컨설팅 펌("(컨설팅 펌)" 태그)는 뒤로.
      "인터뷰이 A PE신기술투자부 부서장",        //   헬퍼가 한 단락에 콤마로 나열(Word 자동 줄바꿈, 별도 줄 분할 없음)
      "인터뷰이 B 전략기획부 팀장",
      "인터뷰이 C 이사(컨설팅 펌)",
      "인터뷰이 D 컨설턴트(컨설팅 펌)"
    ]
  },
  "executive_summary": [
    "인터뷰이 A 이사는 IB(특히 본사 차원의 글로벌 IB)와 Web3 두 축으로 의견을 제시. ...",
    "고객사 A(증권사) 본사의 글로벌 IB는 사실상 ...",
    "..."
  ],
  "sections": [
    {
      "number": 1,
      "title": "미팅 배경",                          // 핵심 섹션엔 제목 끝에 " ★", 귀속은 " (김 팀장 답변)" 식 부연 허용
      "body": [
        {"type": "bullet", "text": "컨설팅 펌는 글로벌 성장전략 관점 8주 프로젝트 진행 중..."},
        {"type": "bullet", "text": "김 팀장 설명: 미국 철수 이후 글로벌을 어떻게..."},
        {"type": "bullet", "text": "(현재 ② 핵심) 크립토 S&T + PBS JV...", "level": 1}  // 하위 항목은 level:1 (들여쓰기 1단계)
      ]
    },
    {
      "number": 5,
      "title": "한투(벤치마크)·증권사 B 비교",
      "body": [
        {
          "type": "table",
          "headers": ["구분", "내용"],
          "rows": [
            ["한투", "올 초까지 글로벌 PDF를..."],
            ["증권사 B", "A 회장 영향으로..."]
          ]
        }
      ]
    }
  ],
  "implications_title": "🎯 시사점 / 후속",   // (선택) "🎯 액션 아이템 / 일정" 등으로 교체 가능
  "implications": [
    "IB 글로벌의 본질은 거점 보유가 아니라 밸런스시트 의지 + 명확한 미션...",
    "진짜 모멘텀은 Web3/캔턴 네트워크·스테이블코인 쪽에 있다는..."
  ],
  "glossary": [
    {"term": "2차 도매", "definition": "골드만 등이 총액 인수한 메가딜(1차 도매) 물량을 국내 기관에 재공급하는 중개 역할"},
    {"term": "PA (Placement Agent)", "definition": "글로벌 펀드 지분을 국내 기관에 중개·배치하는 업무/부서"}
  ]
}

본문 텍스트(executive_summary 문단, bullet text, 표 셀, implications, glossary definition) 안에서는
`**굵게**` 마크다운 패턴이 docx의 bold run으로 변환됩니다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor


BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def add_runs_with_bold(paragraph, text: str):
    """텍스트의 **…** 패턴을 bold run으로 변환해 paragraph에 추가."""
    pos = 0
    for m in BOLD_PATTERN.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        bold_run = paragraph.add_run(m.group(1))
        bold_run.bold = True
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def add_bullet(doc, text: str, level: int = 0):
    """List Bullet 스타일로 bullet 단락 추가. level은 들여쓰기 단계."""
    style_name = "List Bullet" if level == 0 else f"List Bullet {level + 1}"
    try:
        p = doc.add_paragraph(style=style_name)
    except KeyError:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Pt(18 * (level + 1))
    add_runs_with_bold(p, text)
    return p


def add_bold_heading(doc, text: str, size_pt: int = 12, space_before: int = 8):
    """이모지/번호 헤더처럼 bold 헤딩 추가 (Normal 스타일 + bold)."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(size_pt)
    return p


def add_divider(doc):
    """가로 구분선(__________) 단락."""
    p = doc.add_paragraph()
    p.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "999999")
    pBdr.append(bottom)
    pPr.append(pBdr)
    return p


def add_table_block(doc, headers, rows):
    """헤더 행 bold, 본문 셀에는 마크다운 bold 변환 적용한 표 추가."""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"

    # 헤더 행
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(h)
        run.bold = True

    # 본문 행
    for ri, row in enumerate(rows, start=1):
        for ci, cell_text in enumerate(row):
            cell = table.rows[ri].cells[ci]
            cell.text = ""
            p = cell.paragraphs[0]
            add_runs_with_bold(p, cell_text)

    # 표 다음 한 줄 공백
    doc.add_paragraph()
    return table


def build_document(data: dict, output_path: Path):
    doc = Document()

    # 본문 기본 폰트(맑은 고딕 권장 — 한글 미팅록 표준)
    style = doc.styles["Normal"]
    style.font.name = "맑은 고딕"
    style.font.size = Pt(10)
    # 동아시아 폰트 설정
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    rFonts.set(qn("w:ascii"), "맑은 고딕")
    rFonts.set(qn("w:hAnsi"), "맑은 고딕")

    # 0. (선택) 미팅 제목 구분선 — 멀티미팅 마스터 문서에서 인터뷰별 구분에 사용
    #    예) meta.meeting_title = "해외주식파생부 미팅 w/ 인터뷰이 F 부서장"
    #        → "< 해외주식파생부 미팅 w/ 인터뷰이 F 부서장 >" (bold)
    meta = data.get("meta", {})
    meeting_title = meta.get("meeting_title")
    if meeting_title:
        add_bold_heading(doc, f"< {meeting_title} >", size_pt=12, space_before=4)

    # 1. 헤더 (일시 / 참석자) — 라벨(일시:/참석:)만 bold, 값은 plain (참조 미팅록 동일)
    p = doc.add_paragraph()
    label = p.add_run("일시: ")
    label.bold = True
    p.add_run(meta.get("date", ""))
    attendees = meta.get("attendees", [])
    if attendees:
        # 참석자 순서: 고객사 인원 먼저, 컨설팅 펌 인원("(컨설팅 펌)" 태그)은 뒤로.
        #   stable partition이라 그룹 내 입력 순서(고객사 인터뷰이→배석 등)는 그대로 유지된다.
        def _is_bcg(name):
            return "(컨설팅 펌)" in name.replace(" ", "")

        ordered = [a for a in attendees if not _is_bcg(a)] + [a for a in attendees if _is_bcg(a)]
        # 한 단락에 콤마로 모두 나열 — Word가 자동 줄바꿈하므로 인원을 별도 단락으로 쪼개지 않는다.
        #   (과거 ~80자 단위 chunking은 누적 길이가 80자를 넘으면 마지막 참석자를 단독 줄로
        #    떨어뜨리는 버그가 있어 제거함.)
        p = doc.add_paragraph()
        label = p.add_run("참석: ")
        label.bold = True
        p.add_run(", ".join(ordered))

    # 2. Executive Summary
    add_bold_heading(doc, "📌 Executive Summary", size_pt=12, space_before=12)
    for para in data.get("executive_summary", []):
        p = doc.add_paragraph()
        add_runs_with_bold(p, para)

    # 3. 번호 섹션들
    for section in data.get("sections", []):
        number = section.get("number")
        title = section.get("title", "")
        heading_text = f"{number}. {title}" if number else title
        add_bold_heading(doc, heading_text, size_pt=11, space_before=12)

        for item in section.get("body", []):
            itype = item.get("type", "bullet")
            if itype == "bullet":
                add_bullet(doc, item.get("text", ""), level=item.get("level", 0))
            elif itype == "para":
                p = doc.add_paragraph()
                add_runs_with_bold(p, item.get("text", ""))
            elif itype == "table":
                add_table_block(
                    doc,
                    item.get("headers", []),
                    item.get("rows", []),
                )

    # 4. 시사점 / 후속 — 헤더 라벨은 가변 (implications_title)
    #    실제 미팅록은 "🎯 시사점 / 후속", "🎯 액션 아이템 / 일정", "🎯 컨설팅 펌 액션 아이템" 등으로 달라짐
    implications = data.get("implications", [])
    if implications:
        add_divider(doc)
        add_bold_heading(
            doc,
            data.get("implications_title", "🎯 시사점 / 후속"),
            size_pt=12,
            space_before=8,
        )
        for imp in implications:
            add_bullet(doc, imp)

    # 5. 용어 풀이
    glossary = data.get("glossary", [])
    if glossary:
        add_divider(doc)
        add_bold_heading(doc, "🔑 용어 풀이", size_pt=12, space_before=8)
        for entry in glossary:
            term = entry.get("term", "")
            definition = entry.get("definition", "")
            add_bullet(doc, f"**{term}**: {definition}")

    # 저장
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))


def main():
    # Windows 콘솔(cp949)에서 악센트·비라틴 파일명 print 시 UnicodeEncodeError 방지
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="컨설팅 펌 인터뷰 미팅록 docx 생성")
    parser.add_argument("--output", required=True, help="출력 docx 경로")
    parser.add_argument("--json", required=True, help="입력 JSON 경로")
    args = parser.parse_args()

    with open(args.json, "r", encoding="utf-8") as f:
        data = json.load(f)

    output_path = Path(args.output)
    build_document(data, output_path)
    print(f"[OK] {output_path}")


if __name__ == "__main__":
    main()
