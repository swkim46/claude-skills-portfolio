"""Minimal working example of research-organizer skill output.

Demonstrates:
- 3-sheet structure (Intro / Summary / RAW Data)
- column-based hierarchy (one layer = one column, deeper = righter)
- 5-color palette used minimally (1 highlight row, 1 key_line)
- single-cell bullets (the modern convention)
- table with formula-based calculated values
- intermediate conclusion block

Run: python example_build.py  →  produces /tmp/research_example.xlsx
"""
from openpyxl import Workbook
import styles as S


def build(path="/tmp/research_example.xlsx"):
    wb = Workbook()

    # =========================================
    # Intro sheet
    # =========================================
    intro = wb.active
    intro.title = "Intro"
    S.set_tab_color(intro, S.TAB_INTRO)
    S.set_clean_view(intro)

    S.task_banner(intro, 2, "리서치 과제 - 자료 정리")
    S.key_line(intro, 4, "Date", col="C")
    S.support(intro, 4, "2026/06/01", col="D", wrap=False)
    S.key_line(intro, 5, "Editor", col="C")
    S.support(intro, 5, "작성자 B", col="D", wrap=False)

    S.key_line(intro, 7, "과제 설명", col="C")
    S.support(intro, 8, "예시 과제명 — 본문에 과제 정의를 한 문장으로", col="D", wrap=False)

    S.key_line(intro, 10, "접근 방식", col="C")
    for i, step in enumerate([
        "1) 정의 — 핵심 개념 정리",
        "2) 구조 분석 — 핵심 변수 분해",
        "3) 시사점 — 분석 결과 종합",
    ], start=11):
        S.support(intro, i, step, col="D", wrap=False)

    S.set_widths(intro, {"C": 14, "D": 50})

    # =========================================
    # Summary sheet
    # =========================================
    sm = wb.create_sheet("Summary")
    S.set_tab_color(sm, S.TAB_SUMMARY)
    S.set_clean_view(sm)

    S.task_banner(sm, 2, 'Task: "예시 과제의 핵심 질문"')
    S.section_label(sm, 4, "Summary", col="C")

    # --- Section 1 ---
    S.key_line(sm, 6, "1) 핵심 정의")
    S.source_tag(sm, 6, "<Source 1>")
    S.support(sm, 8, "대상 개념은 다음과 같이 정의된다.")
    S.evidence(sm, 10, "특징 A 설명 — 단위경제 측면", marker="[1]")
    S.evidence(sm, 11, "특징 B 설명 — 시장 구조 측면", marker="[2]")

    # --- Section 2: with a table ---
    S.key_line(sm, 14, "2) 구조 분석")
    S.source_tag(sm, 14, "<Source 2>")
    S.support(sm, 16, "주요 수치는 아래 표와 같다.")
    S.unit_note(sm, 17, "(단위: M USD)", col="H")
    end = S.make_table(
        sm, top_row=18,
        headers=["부문", "매출", "이익", "이익률"],
        rows=[
            ["알파", 1000, 120, "=H19/G19"],
            ["베타", 800, 60, "=H20/G20"],
        ],
        highlight_rows={1},        # decisive row (alpha — higher margin)
        number_formats={3: "0.0%"},
        col_widths=[12, 14, 14, 12],
    )

    # --- Intermediate conclusion ---
    S.support(sm, end + 1, "<중간 결론>")
    # ONE key_line in the whole document — the single most-critical insight.
    S.support(sm, end + 2,
              "알파 부문의 수익성이 베타 대비 4배 이상 높음 — 자본 재배분의 핵심 근거.",
              key=True)

    S.set_widths(sm, {"B": 12, "C": 14, "D": 28, "E": 44, "F": 60, "K": 16})

    # =========================================
    # RAW Data sheet
    # =========================================
    raw = wb.create_sheet("RAW Data")
    S.set_clean_view(raw)
    S.task_banner(raw, 2, "출처 모음", col="B")
    S.key_line(raw, 4, "Source 1 — 핵심 정의", col="B")
    S.support(raw, 5, "회사 IR 자료 / https://example.com/ir-2026q1", col="C", wrap=False)
    S.key_line(raw, 7, "Source 2 — 구조 분석", col="B")
    S.support(raw, 8, "업계 리포트 / https://example.com/industry-2026", col="C", wrap=False)
    S.set_widths(raw, {"B": 32, "C": 80})

    wb.save(path)

    # ---- Self-check (run on saved file, not in-memory wb) ----
    result = S.selfcheck_from_path(path)
    print(f"[self-check] key_font cells: {result['key_font_cells']} (policy ≤2)")
    print(f"[self-check] highlight cells per sheet: {result['highlight_cells_by_sheet']}")
    if result["warnings"]:
        for w in result["warnings"]:
            print(f"  WARNING: {w}")
    else:
        print("[self-check] all checks passed ✓")

    return path


if __name__ == "__main__":
    print(build())
