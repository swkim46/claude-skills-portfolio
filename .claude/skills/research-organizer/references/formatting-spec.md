# Formatting spec — exact coordinates and values

Detailed layout specifications. Read when you need precise column letters, RGB values, or layout from reference files.

## Table of contents
1. Layer-to-column map (canonical)
2. RGB values
3. Reference file layouts
4. Number formats, alignment, font

---

## 1. Canonical layer-to-column map

Hierarchy is expressed by **starting column position only**. Do not use cell indent property. Each layer deeper = one column to the right.

| Layer | Column | Use | Style |
|-------|--------|-----|-------|
| L0 | B | Task / top-level section title | Bold, bottom border |
| L1 | C | Major subsection label (e.g., "Summary") | Bold |
| L2 | D | Numbered analysis step ("1) ...", "2) ...") | Bold |
| L3 | E | Body explanation sentence (one-liner conclusions) | Regular |
| Content | F~M | Table headers, table body, single-cell bullets | Regular |
| Source | K (or L) | Source tag on header rows | Regular |

**CRITICAL — Layer columns vs Content columns:**

Columns split into two distinct zones with different roles:

- **Layer zone (A~E)**: narrow columns used ONLY for hierarchy markers — banners, section labels, numbered headers ("1) ...", "2) ..."), and one-line body conclusions. **Keep these columns narrow** (A:2-3, B:3, C:9-13, D:9-13, E:4-7 typical). Text overflows rightward into empty cells; columns are NOT widened to fit content.
- **Content zone (F~M)**: wide columns used for actual content — table headers, table bodies, single-cell bullets. **These are the wide columns** (F:11-22, G-K:14-47, L-M:26-42 typical).

**Stop the hierarchy at D.** Numbered sections go in D ("1) ...", "2) ..."), then E carries a one-line body explanation, and from F onward EVERY layer is content. Sub-sections of a numbered section are NOT expressed as deeper columns (E, F, G...) — they are expressed as **table-title labels in F** (e.g., `<3-1> Sub-section A`, `<3-2> Sub-section B`) each followed by a fresh table at F. This matches the reference file pattern (see `<제조원가>`, `<판매비>`, `<관리비>` in the `작성자 B_컨설팅 펌_리서치과제.xlsx` reference).

**Wrong pattern** (gradual deepening):
```
D: 3) 수익 모델
  E: 3-1) 4축 분해              ← E now occupies a layer slot
    F: 3-1-a) sub-cut          ← F now layer, not content
      G: 표 시작                 ← table starts at G, content area is cramped
        H: KEY_FONT 인사이트     ← jammed into narrow column, gets cut off
```

**Right pattern** (flat structure with sub-tables):
```
D: 3) 수익 모델
  E: 수익 4축 framework + ②축 sub-cut 도입 한 줄
  F: <3-1> 4축 정의
    F: [표]
  F: <3-2> ②축 sub-cut: 운용 vs 수탁
    F: [표]
  F: <3-3> Securitize FINRA 승인
    E: KEY_FONT 인사이트         ← E is short and stays in layer zone
    F: [표]
```

Result: every table starts at F, gets full content width through M, and KEY_FONT lines sit in E (the natural body column) where they're readable.

Column widths (defaults — based on reference files `RWA_리서치과제_정리_운영자_추가.xlsx` and `작성자 B_컨설팅 펌_리서치과제.xlsx`):

| Column | Width | Role |
|--------|-------|------|
| A | 2-4 | Left margin |
| B | 3-4 | L0 banner anchor (narrow — text overflows right) |
| C | 3-4 | L1 section_label (narrow — text overflows right) |
| D | 3-4 | L2 numbered section (narrow — text overflows right) |
| E | 3-4 | L3 body sentence (narrow — text overflows right) |
| F | 11-22 | **Content start** — table headers, single-cell bullets |
| G | 14-33 | Table column |
| H | 22-40 | Table column |
| I | 12-47 | Table column |
| J | 22-42 | Table column |
| K | 22 | Source tag column or table column |
| L | 26-36 | Source link column or table column |
| M | 26 | Table column |

**Layer columns (A through E) must all be uniformly narrow (≈3.2)** — the same width as A/B. This is the "Layer zone uniform width" rule (added v5). Earlier versions used C:13, D:13.7, E:7 (varying), but this still created visual interference. Standard is now: A~E all share the same narrow width, and `set_layer_widths(ws)` enforces this.

**Important — overflow vs. width**: Section headers and body text in left columns (A~E) overflow rightward into empty cells. The reference files leave these columns narrow and let text spill across — do NOT explicitly widen any layer column just because text is present there. Explicit widening of left columns creates dead whitespace AND pushes table-start columns rightward (cramping content). Only widen columns that contain *table cells* (typically F onward in Summary).

Use `S.set_layer_widths(ws, extra={"F": 22.0, "G": 33.3, ...})` to apply both layer zone (A~E narrow) and content zone (F~M wider) widths in one call.

---

## 2. RGB values (color palette)

| Name | RGB hex | Where |
|------|---------|-------|
| Task banner fill | `8ED973` | L0 banner cell |
| Section banner fill | `B4E5A2` | L1 section label cell |
| Table header fill | `A6CAEC` | Every table header row |
| Highlight row fill | `D9F2D0` | Decisive table rows (max 1-2 per table) |
| Key font color | `0000FF` | Most-critical insight font (max 1-2 per Summary) |
| Header underline | thin black bottom border | L0 titles |
| Table border | thin black, all sides | Every table cell |

**Sheet tab colors (optional):**
- Intro: `95FFA1`
- Summary: `00FF79`

See `color-policy.md` for when to use each (and when not to).

### Banner span rule (mandatory)

Task banner and Section label fills extend across multiple cells **without merging**:
- Task banner (`task_banner()`): fills from L0 column (B) through the source column (K) — i.e. B2 through K2 — covering 10 cells. Text lives in B; cells C through K stay empty but carry the same fill.
- Section label (`section_label()`): fills from L1 column (C) through K — covering 9 cells. Same pattern.

This produces a unified color block reaching across the content area, while text overflows naturally from the leftmost cell. The reference files use this pattern consistently.

The styles helpers (`task_banner`, `section_label`) handle this automatically via the `span_to` parameter (defaults to LAYER["source"] = K). Override only when banner should reach further (e.g., a wider Summary that uses M column).

### Intro vs Summary — different visual register

**Critical**: Intro and Summary are NOT formatted the same way. They have different roles and different visual weight.

| Sheet | Visual register | Banner usage |
|-------|-----------------|--------------|
| **Summary** | Heavy — task banner (B2) + section label (C4 "Summary") + numbered headers (D6, D18 ...) | Both `task_banner()` AND `section_label()` |
| **Intro** | Light — only task banner (B2). Meta labels are bold-only, no fills, no L1 banner | Only `task_banner()`. **No `section_label()` on Intro.** |

For Intro meta labels (Date, Editor, 과제 설명, 접근 방식 etc.):
- Use `intro_meta_label(ws, row, "Date", col="D")` and `intro_meta_value(ws, row, "2026/06/01", col="E")`
- These produce bold-only labels in D and plain values in E — matches the reference Intro pattern
- Do NOT use `section_label()` on Intro — that creates a heavy green band suited only for Summary's L1 anchor

If you find yourself reaching for `section_label()` on Intro to make it "look more structured", stop — the lighter register is intentional. Intro is a metadata sheet, not a primary deliverable.

### Gridlines must be off

Every worksheet MUST call `set_clean_view(ws)` after creation. This sets `sheet_view.showGridLines = False` so the worksheet displays as a clean canvas where only table borders are visible. The reference files apply this on all sheets without exception.

```python
S.set_clean_view(ws)  # call right after set_tab_color
```

Forgetting this is the most common visual regression — gridlines on a consulting-house-style deliverable immediately reads as "draft" rather than "finalized".

### RAW Data — URLs must be clickable

RAW Data source lines typically contain URLs embedded in descriptive text:
```
"RWA.xyz 사모신용: https://app.rwa.xyz/private-credit (활성 222억) [1차·검증]"
```

These URLs MUST be clickable hyperlinks — not just plain text. The reader of the deliverable needs to one-click to the source.

**Use `raw_data_entry()` or `raw_data_section()`** — both auto-detect URLs in the text and apply `cell.hyperlink = <url>` plus blue/underline styling.

```python
# Per-line entry:
S.raw_data_entry(raw, row, "RWA.xyz: https://app.rwa.xyz/... [1차·검증]")

# Or full section block at once (returns header_row for linkify_internal):
hdr, nxt_row = S.raw_data_section(raw, row, "Source 1 — 시장 개괄", [
    "RWA.xyz: https://app.rwa.xyz/private-credit [1차·검증]",
    "한국 STO: https://www.asiae.co.kr/... [2차]",
])
S.linkify_internal(src1_cell, f"'RAW Data'!B{hdr}")
```

**Do NOT use `support()` for RAW Data lines** — that writes plain text without making URLs clickable. This is a common bug regression — RAW Data lines look fine but URLs aren't actually links.

---

## 3. Reference file layouts

### `자료_정리_예시.xlsx` (single-sheet reference)

```
B2   [L0, bold, bottom border]    Task title
B4   [L0, bold, bottom border]    Section: "인수배경"
C6   [L1, bold]                    Sub-label: "ABC 회사의 abc 사업부 특징"      | J6  "출처"
C7   "-"   D7   bullet text
C8   "-"   D8   bullet text
C10  [bold]                        "인수 기대효과"
C11  "-"   D11 / C12  "-"  D12
B14  [L0, bold, bottom border]    Section: "인수성과"
...
B24  [L0, bold, bottom border]    "가나다 회사의 매출 및 영업이익 추이"        | J24 "출처"
H26  [right-aligned]              "(단위: 백만원)"
F27 G27 H27  [bold, header fill]  "2019년" "2018년" "2017년"
D28:D30 merged "알파부문"  | E28 "매출" F28..H28 numbers
                          | E29 "법인세차감전영업이익" numbers
                          | E30 "비중(%)"  F30 =F29/F28 (format 0.00%)
D31:D33  "베타부문" ... D34:D36  "감마부문"  (same pattern)
```

Note: this reference uses C-D split bullets ("-" in C, content in D). The newer convention in this skill is **single-cell bullets** (put `"- text..."` or `"[1] text..."` whole in F). Either is acceptable; pick one per file and be consistent.

### `작성자 B_컨설팅 펌_리서치과제.xlsx` (4-sheet reference)

Sheets: `Intro`, `Summary`, `RAW >>>>` (divider), `RAW Data`.

**Intro sheet:**
- B2: Title "컨설팅 펌 RA Interview - 리서치 테스트"
- C4, C5: Metadata labels (Date / Editor)
- C7: "리서치 과제 설명" → C8 task name
- C10: "접근 방식" → C11..C15: numbered ToC ("1) 정의", "2) 구조 분석", ...)

**Summary sheet (canonical column-hierarchy example):**
```
B2  [bold]   Task: "..."
C4  [bold]   "Summary"
D6  [bold]   "1) '일반 제조업 기업' 정의"          | K6: <Source>
E8           Body sentence
E9           "구체적으로 하기와 같은 특징을 갖고 있음"
F10..F13     "[1] ...", "[2] ...", "[3] ...", "[4] ..."  detail bullets
E14          "핵심은 제조업은 특정 원재료를 활용한다는 것"  ← summary
F15          "따라서, 비용 구조 내 원재료와 관련된 비용이 포함될 것"

D18 [bold]   "2) 제조업 기업의 비용 구조 세분화"
E20          Body + table follows
D30 [bold]   "3) 비용 구조 속 노무비 구성 요소 파악"  → multiple sub-tables
             (대분류 / 중분류 / 세부 항목 예시 / 노무비 여부)
<중간 결론> block → 1-2 sentence so-what
D...[bold]   "4) 노무비 감축 방안 분석"
             4.1) Top-Down table: [1]전략·레버 / [2]작동원리 / [3]기대효과·주의점
             4.2) Bottom-Up table: 사례 / 적용 레버 / 결과  (each row: Source Link)
Implication block → final 4 시사점
```

**RAW Data sheet:**
- Table form, sections numbered to match Summary
- Each numbered section header followed by sources (link / document name / publisher)
- Summary's `<Source N>` tags hyperlink-jump to the matching row here

---

## 4. Number formats, alignment, font

| Element | Format / Style |
|---------|---------------|
| Font (Korean) | 맑은 고딕 11pt |
| Font (English-only) | Arial 11pt |
| Year header | Text string ("2024"), not number |
| Currency | `#,##0` with unit annotation top-right (e.g., "(단위: 백만원)") |
| Percentage | `0.0%` or `0.00%` (cell stores decimal, e.g., 0.0123) |
| Unit annotation | Right-aligned, row directly above table header |
| Calculated cell | Excel formula (e.g., `=F29/F28`), NEVER Python-hardcoded |
| Table header | Bold, centered, fill `A6CAEC` |
| Table body | Regular, left-aligned, thin border all sides |

## 5. Bullet style choice (single-cell vs split)

Two conventions exist in the reference files:

**Single-cell (preferred for newer files):**
```
F10  "[1] 원재료 변환: 투입된 원재료를 가공하여 성질이 완전히 다른 새로운 ..."
F11  "[2] 물리적/화학적 작용: 기계적 가공(절단, 연마 등), 화학적 반응 ..."
```

**Split (older convention from `자료_정리_예시.xlsx`):**
```
C7  "-"     D7  "투입된 원재료를 가공하여..."
C8  "-"     D8  "기계적 가공, 화학적 반응..."
```

Pick **one convention per file** and use it consistently. For new files, single-cell is recommended (cleaner column structure).
