"""House-style helpers for the research-organizer skill.

Express hierarchy through column position. Apply restrained color use,
consistent table styling, source tags on the right.

Rules embodied here (see SKILL.md for full reasoning):
- one layer = one column, deeper = righter (cell indent property NOT used)
- 5-color palette only: TASK / SECTION / TBL_HEADER / HIGHLIGHT / KEY_FONT
- HIGHLIGHT rows per table: max 2
- KEY_FONT cells per Summary: max 2
- Single-cell bullets ("[1] text..." in one cell, overflows right)
- Tables: thin border all sides, header fill, calculated cells = formulas

Korean font default: 맑은 고딕 11pt.
"""
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import column_index_from_string, get_column_letter

# ---- Defaults ----
DEFAULT_FONT = "맑은 고딕"
DEFAULT_SIZE = 11

# ---- 5-color palette (see references/color-policy.md for usage rules) ----
PALETTE = {
    "TASK":       "8ED973",   # L0 banner — top-level section
    "SECTION":    "B4E5A2",   # L1 banner — major subsection
    "TBL_HEADER": "A6CAEC",   # table header row
    "HIGHLIGHT":  "D9F2D0",   # decisive table row (max 2 per table)
    "KEY_FONT":   "0000FF",   # blue font for single most-critical insight (max 2 per Summary)
}

# ---- Layer-to-column map (canonical) ----
LAYER = {
    "task":          "B",  # L0
    "section":       "C",  # L1
    "key":           "D",  # L2 — numbered "1) ..."
    "support":       "E",  # L3 — body sentence
    "evidence":      "F",  # L4 — detail bullets (single-cell)
    "evidence_text": "G",  # only if using split-bullet style
    "source":        "K",  # source tag column
}

# ---- Borders ----
THIN = Side(style="thin")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BOTTOM = Border(bottom=THIN)

# ---- Tab colors ----
TAB_INTRO = "95FFA1"
TAB_SUMMARY = "00FF79"


# =============================================================
# Low-level put + font
# =============================================================

def house_font(bold=False, size=DEFAULT_SIZE, name=DEFAULT_FONT, color="FF000000"):
    return Font(name=name, size=size, bold=bold, color=color)


def _put(ws, row, col_letter, value, *, bold=False, font_name=DEFAULT_FONT,
         size=DEFAULT_SIZE, color="FF000000", align=None, wrap=False, fill=None):
    """Internal: write a cell with house-style font and optional alignment/fill."""
    c = ws[f"{col_letter}{row}"]
    c.value = value
    c.font = house_font(bold=bold, size=size, name=font_name, color=color)
    if align or wrap:
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    return c


# =============================================================
# Banners and labels (L0, L1, L2)
# =============================================================

def task_banner(ws, row, text, col=None, span_to=None, **kw):
    """L0: Task/top-level section banner. Bold + TASK fill (dark green), NO border.

    span_to: optional end column letter (default 'K'). Fill is extended from `col` to `span_to`
             without merging — text overflows naturally, but the color block reaches across the
             content area. This matches the consulting house-style reference files.

    NO bottom border: _extend_fill spans the FILL across B→span_to but a single-cell bottom
    border would only sit under `col` (B), rendering as a floating underline on the narrow
    layer column ('B열 부유 border' — recurring complaint, esp. on the Intro sheet which is
    not always passed through clear_layer_borders). The banner gets its visual weight from
    the fill block, not a border. (If a separator under a banner is ever wanted, span it with
    span_to and clear via clear_layer_borders.)
    """
    col = col or LAYER["task"]
    span_to = span_to or "Z"  # 배너/제목 컬러를 여유있게 Z열까지(사용자 요청)
    c = _put(ws, row, col, text, bold=True, fill=PALETTE["TASK"], **kw)
    _extend_fill(ws, row, col, span_to, PALETTE["TASK"])
    return c


def section_label(ws, row, text, col=None, span_to=None, **kw):
    """L1: Major subsection banner. Bold + SECTION fill (light green). Used for 'Summary', 'Implication' etc.

    span_to: optional end column letter (default 'K'). Same fill-span pattern as task_banner.
    """
    col = col or LAYER["section"]
    span_to = span_to or "Z"  # 배너/제목 컬러를 여유있게 Z열까지(사용자 요청)
    c = _put(ws, row, col, text, bold=True, fill=PALETTE["SECTION"], **kw)
    _extend_fill(ws, row, col, span_to, PALETTE["SECTION"])
    return c


def _extend_fill(ws, row, start_col, end_col, fill_rgb):
    """Internal: paint empty cells from start_col+1 through end_col with the same fill.
    Used to create banner span effect without merging. Text in start_col overflows naturally
    across these (empty) cells; the fill creates a unified color block.
    """
    from openpyxl.utils import column_index_from_string, get_column_letter
    start_idx = column_index_from_string(start_col)
    end_idx = column_index_from_string(end_col)
    fill_obj = PatternFill("solid", fgColor=fill_rgb)
    for i in range(start_idx + 1, end_idx + 1):
        c = ws.cell(row=row, column=i)
        c.fill = fill_obj


def key_line(ws, row, text, col=None, key=False, **kw):
    """L2: Numbered analysis step ('1) ...', '2) ...') or Exec Summary headline. Bold, no fill.

    key=True → KEY_FONT (blue) for the single most-critical conclusion (≤2 per workbook).
    """
    col = col or LAYER["key"]
    color = "FF" + PALETTE["KEY_FONT"] if key else "FF000000"
    return _put(ws, row, col, text, bold=True, color=color, **kw)


# Aliases for backward compatibility / readability
title = task_banner
section = task_banner   # legacy alias — same style
subhead = key_line      # legacy alias


# =============================================================
# Body text (L3, L4)
# =============================================================

def support(ws, row, text, col=None, key=False, wrap=False, **kw):
    """L3: Body explanation sentence. wrap=False so text overflows right (consistent indent staircase).

    key=True: apply KEY_FONT (blue) — use ONLY for the single most-critical insight.
              Total key=True cells in a workbook should not exceed 2.
    """
    col = col or LAYER["support"]
    color = "FF" + PALETTE["KEY_FONT"] if key else "FF000000"
    return _put(ws, row, col, text, color=color, wrap=wrap, **kw)


# Backward-compatible alias
def body(ws, row, text, col=None, **kw):
    return support(ws, row, text, col=col, **kw)


def evidence(ws, row, text, col=None, marker=None, key=False, wrap=False, **kw):
    """L4: Single-cell bullet. The whole string ('[1] text...' or '- text...') lives in one cell,
    overflows right. This is the modern convention (vs split C-D).

    marker: optional. If given, prepended to text: '[1]' + ' ' + text.
    key=True: blue font (see support()).
    """
    col = col or LAYER["evidence"]
    color = "FF" + PALETTE["KEY_FONT"] if key else "FF000000"
    full = f"{marker} {text}" if marker else text
    return _put(ws, row, col, full, color=color, wrap=wrap, **kw)


def bullet_split(ws, row, text, col=None, marker="-", wrap=True, **kw):
    """LEGACY split-bullet style: marker in col, text in col+1.
    Use only when matching an existing file that uses this convention.
    For new files, prefer evidence() (single-cell)."""
    col = col or LAYER["evidence"]
    _put(ws, row, col, marker)
    nxt = get_column_letter(column_index_from_string(col) + 1)
    return _put(ws, row, nxt, text, wrap=wrap, **kw)


# Backward-compatible alias
def bullet(ws, row, text, col=None, marker="-", split=False, **kw):
    if split:
        return bullet_split(ws, row, text, col=col, marker=marker, **kw)
    return evidence(ws, row, text, col=col, marker=marker, **kw)


# =============================================================
# Source tags and unit notes
# =============================================================

LINK_BLUE = "FF0563C1"


def linkify_internal(cell, target_ref, *, blue=True):
    """Make a cell an INTERNAL hyperlink to another sheet location.

    target_ref form: "'RAW Data'!B12" (sheet!cell). Used to point a Summary
    source tag at its RAW Data section header so the reader can click-jump.
    """
    from openpyxl.worksheet.hyperlink import Hyperlink
    cell.hyperlink = Hyperlink(ref=cell.coordinate, location=target_ref)
    if blue:
        f = cell.font
        cell.font = Font(name=f.name, size=f.size, bold=f.bold,
                         color=LINK_BLUE, underline="single")
    return cell


def source_tag(ws, row, text="<출처>", col=None, *, url=None, internal_ref=None):
    """Source tag in the rightmost column of a header row.

    Make it CLICKABLE (required — see SKILL.md '엑셀 형식 피드백'):
      - url=...          → external hyperlink (the primary 1차 자료 URL)
      - internal_ref=... → internal jump, e.g. "'RAW Data'!B12" (RAW Data section header)
    If neither is given, the tag is plain text (legacy; avoid for new files —
    Summary source tags must be clickable just like RAW Data lines).
    """
    col = col or LAYER["source"]
    c = _put(ws, row, col, text)
    if url:
        c.hyperlink = url
        f = c.font
        c.font = Font(name=f.name, size=f.size, bold=f.bold,
                      color=LINK_BLUE, underline="single")
    elif internal_ref:
        linkify_internal(c, internal_ref)
    return c


def unit_note(ws, row, text="(단위: 백만원)", col="H"):
    """Unit annotation, right-aligned, above a table."""
    return _put(ws, row, col, text, align="right")


# =============================================================
# Executive Summary opener + glossary / term notes
# =============================================================

def sheet_title(ws, row, text, *, size=16, span_to=None, col=None):
    """Prominent sheet-top title (larger font) — the report/sheet name.

    Use at the TOP of a content sheet, above the Executive Summary. Bigger than the
    task_banner section headers so the sheet reads as a titled deliverable, not a
    wall of equal-weight banners.
    """
    col = col or LAYER["task"]
    span_to = span_to or "Z"  # 제목 컬러를 여유있게 Z열까지(사용자 요청)
    c = _put(ws, row, col, text, bold=True, size=size, fill=PALETTE["TASK"])
    _extend_fill(ws, row, col, span_to, PALETTE["TASK"])
    # BUGFIX: a size-16 title in a default-height (15pt) row gets clipped — set the
    # row height so the title is actually visible.
    ws.row_dimensions[row].height = max(20, size + 10)
    return c


def exec_summary(ws, row, *, label="Executive Summary", span_to=None):
    """Open the Executive Summary block as an L1 banner (section_label, column C),
    placed UNDER the sheet's big title (sheet_title at B). Returns the next row.

    Hierarchy (the layout that reads well — big title brackets everything below it):
        B  큰 제목 (sheet_title, L0, size 16)
        C  Executive Summary + 섹션 타이틀 ①②③ (section_label, L1)   ← 모두 같은 C열
        D  1) 2) 3) 단계 (key_line)
        E  본문 (support)

    Put a sheet_title at B ABOVE this. The single headline verdict that follows goes at
    D via key_line() (key=True), then a divider() before the numbered sections — which
    ALSO use section_label at C, so Exec Summary and section titles stay column-aligned.

        S.sheet_title(sm, r, "보고서 제목"); r += 2            # 큰 제목 (B, L0)
        r = S.exec_summary(sm, r)                              # Executive Summary (C, L1)
        S.key_line(sm, r, "핵심 결론 한 문장 …", key=True); r += 2
        S.divider(sm, r); r += 1
        S.section_label(sm, r, "① 거시·경제 — …"); ...         # 섹션도 C (L1)
    """
    section_label(ws, row, label, span_to=span_to)
    return row + 1


def term_note(ws, row, text, col="F", *, size=9, wrap=False):
    """Inline glossary gloss for a proper noun / jargon term (회색 이탤릭 소형).

    Place directly BELOW the line that first introduces the term. Default wrap=False:
    the gloss overflows rightward as a single line across empty cells (like support()),
    which avoids the row-height explosion that wrap=True causes on narrow columns.
    Keep the gloss short enough to fit before any source tag on the same row.

        S.support(sm, r, "샤리아 펀드 비중 13% …"); r += 1
        S.term_note(sm, r, "샤리아: 이슬람 율법(이자·투기·하람 산업 금지)", col="F"); r += 1
    """
    c = _put(ws, row, col, text, wrap=wrap)
    c.font = Font(name=DEFAULT_FONT, size=size, italic=True, color="FF808080")
    if wrap:
        c.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    return c


def glossary_block(ws, row, terms, *, col_term="F", col_def="G", label="용어"):
    """Collected '용어' definitions mini-block at the end of a section (compact GREY style).

    terms: list of (term, definition). Each row = [term(bold grey 9pt)] | [definition(grey)].
    Use for a few minor inline glosses you want visually de-emphasized.

    For a glossary that reads as a normal content table (term | definition at 11pt,
    several entries — like '주요 용어'), prefer `definition_list()` instead: it lays the
    terms out as a clean two-column list at body register. Do NOT pack glossary entries
    into single-cell '■ 용어 — 정의' bullets via evidence() — they misalign and bloat.
    Returns the row after the block.
    """
    _put(ws, row, col_term, label, bold=True, size=9, color="FF808080")
    row += 1
    for term, definition in terms:
        _put(ws, row, col_term, term, bold=True, size=9)
        d = _put(ws, row, col_def, definition, wrap=True, size=9, color="FF595959")
        d.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        row += 1
    return row + 1


def definition_list(ws, top_row, items, *, term_col="F", def_col="G",
                    bold_term=False, size=DEFAULT_SIZE, wrap_def=False):
    """Two-column term | definition list at NORMAL body register (the clean glossary
    layout). term in `term_col`, definition in `def_col`.

    This is the right shape for a '주요 용어' block: every term aligns in one column and
    every definition in the next. By default the definition is wrap=False so it overflows
    rightward as a single line across the empty cells to its right (matches the 컨설팅 펌
    reference). If the cells to the right are NOT empty, pass wrap_def=True so the
    definition wraps inside def_col instead of being clipped.

    Do NOT express a definition list as single-cell '■ 용어 — 정의' bullets (evidence()):
    the marker+term+def crammed in one cell breaks alignment and produces over-long cells.

    For a bordered/header version, use make_table(headers=["용어","정의"], rows=items,
    wrap=True) — but a borderless definition_list usually reads cleaner for a glossary.

    Returns the row after the block.
    """
    r = top_row
    for term, dfn in items:
        _put(ws, r, term_col, term, bold=bold_term, size=size)
        _put(ws, r, def_col, dfn, size=size, wrap=wrap_def)
        r += 1
    return r + 1


# =============================================================
# Tables
# =============================================================

def make_table(ws, top_row, headers, rows, *,
               left_col=None,
               header_fill=None,
               highlight_rows=None,
               number_formats=None,
               col_widths=None,
               wrap=True,
               col_wrap=None,
               link_col=None,
               links=None,
               link_cols=None):
    """Build a thin-bordered table with header fill.

    headers:        list of header strings
    rows:           list of row value lists (formula strings '=A1/B1' OK)
    left_col:       starting column letter (default: LAYER['evidence'] = 'F')
    header_fill:    override header fill color (default PALETTE['TBL_HEADER'])
    highlight_rows: set of 1-based row indexes to apply HIGHLIGHT fill.
                    HARD LIMIT: 2 per table (see references/color-policy.md).
    number_formats: dict {col_offset: '0.00%'} for column-specific number formats
    col_widths:     DISCOURAGED for multi-table sheets — Excel column widths are
                    GLOBAL, so per-table widths clobber every other table sharing
                    those columns. Prefer leaving this None and calling
                    autofit_columns(ws) ONCE at the end of the build (content-aware,
                    adapts when text changes). Only pass col_widths for a single
                    isolated table whose columns no other table touches.
    wrap:           body-cell wrap_text (default True). Set wrap=False for an
                    overflow-style row where text should flow right as one line.
    col_wrap:       optional {col_offset: bool} to override wrap per column
                    (e.g. {0: False} keeps the first column from wrapping).
    link_col:       0-based column offset whose body cells become clickable EXTERNAL
                    hyperlinks (e.g. a 출처/접수번호 column). Pairs with `links`.
    links:          list of URLs aligned to `rows` (one per data row). links[i] is the
                    target for row i's cell at link_col. Falsy/None entries (or a shorter
                    list) leave that cell as plain text. Display text stays the cell value;
                    only the link + blue underline font are added. Use this instead of
                    hand-building a 출처 표 at C~E (which trips clear_layer_borders) —
                    keep the table at F+ and linkify here. (국내사 해외법인 세션 피드백 #3)
    link_cols:      OPTIONAL dict {col_offset: [urls...]} to linkify MORE THAN ONE column
                    (link_col handles one). Use when a row carries 2+ sources in separate
                    columns (e.g. 출처1 at offset 5, 출처2 at offset 6) — each source its own
                    clickable cell, no extra rows. urls aligned to `rows`; falsy/short →
                    plain text. (S&T 세션 피드백: 출처가 여럿이면 행 분리 대신 출처1/출처2 *열* 분리.)

    Returns the row immediately after the last table row.
    """
    if left_col is None:
        left_col = LAYER["evidence"]
    if header_fill is None:
        header_fill = PALETTE["TBL_HEADER"]
    highlight_rows = set(highlight_rows or [])
    col_wrap = col_wrap or {}

    # ---- soft guard: warn if highlight_rows exceeds policy ----
    if len(highlight_rows) > 2:
        # Don't raise — just print. The skill instruction layer should catch this.
        print(f"[styles.make_table] WARNING: {len(highlight_rows)} highlight rows requested. "
              f"Policy is max 2 per table. See references/color-policy.md.")

    c0 = column_index_from_string(left_col)

    # ---- header row ----
    header_fill_obj = PatternFill("solid", fgColor=header_fill)
    for j, h in enumerate(headers):
        col = get_column_letter(c0 + j)
        c = _put(ws, top_row, col, h, bold=True, align="center")
        c.fill = header_fill_obj
        c.border = BOX

    # ---- body rows ----
    highlight_fill = PatternFill("solid", fgColor=PALETTE["HIGHLIGHT"])
    for i, rowvals in enumerate(rows, start=1):
        r = top_row + i
        is_highlight = i in highlight_rows
        for j, v in enumerate(rowvals):
            col = get_column_letter(c0 + j)
            cell_wrap = col_wrap.get(j, wrap)
            c = _put(ws, r, col, v, wrap=cell_wrap)
            c.border = BOX
            if is_highlight:
                c.fill = highlight_fill
            if number_formats and j in number_formats:
                c.number_format = number_formats[j]
            if link_col is not None and j == link_col and links and (i - 1) < len(links) and links[i - 1]:
                c.hyperlink = links[i - 1]
                f = c.font
                c.font = Font(name=f.name, size=f.size, bold=f.bold,
                              color=LINK_BLUE, underline="single")
            if link_cols and j in link_cols:
                _urls = link_cols[j]
                if _urls and (i - 1) < len(_urls) and _urls[i - 1]:
                    c.hyperlink = _urls[i - 1]
                    f = c.font
                    c.font = Font(name=f.name, size=f.size, bold=f.bold,
                                  color=LINK_BLUE, underline="single")
        # 행 높이 자동 — wrap된 텍스트에 맞춰 늘어나도록 명시 해제
        ws.row_dimensions[r].height = None

    # ---- column widths ----
    if col_widths:
        for j, w in enumerate(col_widths):
            col = get_column_letter(c0 + j)
            ws.column_dimensions[col].width = w

    return top_row + len(rows) + 1


# =============================================================
# Layout helpers
# =============================================================

def divider(ws, row, col=None):
    """Bottom border as a visual separator. NOTE: on a narrow layer column (B/C/D/E) a
    single-cell bottom border renders as a floating underline ('B열 부유 border' — recurring
    complaint). For section separation prefer a BLANK ROW, not divider(). If you do want a
    rule, span it across the content with span_to, or clear it later via clear_layer_borders().
    """
    col = col or LAYER["task"]
    c = ws[f"{col}{row}"]
    c.border = BOTTOM


def clear_layer_borders(ws, cols=("A", "B", "C", "D", "E"), max_row=None):
    """Remove stray cell borders from the layer-zone columns (A–E). Banners (sheet_title/
    task_banner) and divider() leave single-cell bottom borders on these narrow columns,
    which show as a floating underline — the recurring 'B열에 이상한 테두리' complaint.
    Tables live at F+, so layer columns must carry NO borders. Call at the END of the build
    (after banners/dividers, before save). Safe: only touches A–E, never the table zone.
    """
    if max_row is None:
        max_row = ws.max_row or 1
    blank = Border()
    box_cells = 0
    for r in range(1, max_row + 1):
        for col in cols:
            c = ws[f"{col}{r}"]
            b = c.border
            if not b:
                continue
            sides = [getattr(b, s) and getattr(b, s).style for s in ("left", "right", "top", "bottom")]
            if not any(sides):
                continue
            if all(sides):
                # Full BOX = a real table cell (e.g. a 출처/접수번호 표 hand-built at C~H).
                # Stripping it breaks the table — the 'F7~H12 이상한 테두리' bug where only
                # half the table kept borders. Keep it. (국내사 해외법인 세션 피드백 #2)
                box_cells += 1
                continue
            c.border = blank      # partial/stray border (banner·divider underline) → remove
    if box_cells:
        print(f"[styles.clear_layer_borders] NOTE: {box_cells} bordered table cell(s) in the "
              f"layer zone {cols} — kept (not stripped). Tables belong at F+; if hand-built, "
              f"prefer make_table(..., link_col=, links=) at F or wider.")


def set_widths(ws, widths):
    """widths: {'B': 30, 'I': 80} form."""
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def set_tab_color(ws, color):
    """Tab color hex (without '#')."""
    ws.sheet_properties.tabColor = color


def set_clean_view(ws, zoom=None):
    """Turn off cell gridlines for this worksheet — the consulting house-style reference files
    use a clean canvas where only table borders are visible. Always call this on every sheet
    after creation.

    zoom: optional zoom level (e.g. 100). If None, leaves at default.
    """
    ws.sheet_view.showGridLines = False
    if zoom is not None:
        ws.sheet_view.zoomScale = zoom


# =============================================================
# Content-aware layout — size to the actual content, not magic numbers
# =============================================================
#
# WHY: hardcoded column widths (a fixed GRID, or make_table col_widths) are tuned
# to ONE dataset. When the text changes — longer quotes, more rows, a new column —
# those magic numbers are wrong and the layout breaks (columns too tight/too wide),
# forcing the user to hand-drag every column. The helpers below COMPUTE widths and
# heights from the cells actually present, so the layout adapts on its own.
#
# Pipeline at end of build:   autofit_columns(ws)  →  finalize_row_heights(ws)

def _disp_width(s, cjk=1.9):
    """Approximate Excel display width of a string (in '0'-char width units).
    CJK / Hangul / fullwidth glyphs count `cjk` (they render ~2x wide); others 1.0.

    Use cjk≈1.9 for column-WIDTH sizing (autofit); use cjk≈2.0 for ROW-HEIGHT line
    counting (finalize) — Korean wraps slightly wider than 1.9 in practice, so a higher
    weight there avoids under-counting lines (= clipping).
    """
    if s is None:
        return 0.0
    if not isinstance(s, str):
        s = str(s)
    w = 0.0
    for ch in s:
        o = ord(ch)
        if (0xAC00 <= o <= 0xD7A3) or (0x1100 <= o <= 0x11FF) or \
           (0x3000 <= o <= 0x30FF) or (0x3130 <= o <= 0x318F) or \
           (0x4E00 <= o <= 0x9FFF) or (0xFF00 <= o <= 0xFFEF):
            w += cjk
        else:
            w += 1.0
    return w


def _percentile(sorted_vals, p):
    """Linear-interpolation percentile of an already-sorted list. p in [0,1]."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = p * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def autofit_columns(ws, *, first_col="A", last_col=None, min_w=8.5, max_w=44.0,
                    pct=0.78, padding=2.6, indent_w=None, overrides=None, max_row=None,
                    short_threshold=16.0, max_wrap_lines=5.0, pad_to="Z", pad_w=11.0):
    """Decide EVERY column's width from the content situation — no hardcoded widths,
    no per-table col_widths. Call ONCE near the END of the build, after all cells are
    written and BEFORE finalize_row_heights().

    The key idea: each cell either MUST fit inside its own column or may OVERFLOW right
    into empty neighbors. A column is sized only to its must-fit cells; pure-overflow
    columns (the layer/indent zone) collapse to a thin indent. So the function adapts
    per column instead of assuming which letters are "layer" vs "content".

    A cell MUST fit (counts toward width) when:
      - it wraps (wrap_text=True — table body), OR
      - it is a table header (TBL_HEADER fill), OR
      - the cell immediately to its right (same row) is non-empty, so its text would be
        clipped rather than overflow.
    A cell may OVERFLOW (ignored for width) when it is wrap=False AND its right neighbor
    is empty — e.g. section labels, '1) …' headers, '<중간 결론> …', support() sentences,
    definition_list definitions. These flow rightward across blank cells, so widening
    their column only wastes space.

    Width rule per column:
      - has must-fit cells → clamp( max(header_max, body_target) + padding, min_w, max_w )
        where body_target = max(body) if the longest body ≤ short_threshold (short tokens
        like codes/names fit fully), else percentile(body, pct) (long prose wraps).
      - no must-fit cells (pure-overflow / indent column) → `indent_w` (a thin, uniform
        indentation step). Defaults to the layer baseline (column B width), NOT Excel's
        ~8.43 default — leaving it unset makes the layer zone look too wide.

    Pooling across tables that share a column letter is automatic, so the width satisfies
    whichever table needs the most room (resolves the shared-column conflict).

    overrides: {col_letter: width} escape hatch to pin a column (e.g. RAW Data's wide
               source column {"C": 120}). Use sparingly.
    indent_w:  width for pure-overflow columns (default = LAYER_WIDTHS['B'] ≈ 3.2).
    """
    from openpyxl.utils import column_index_from_string, get_column_letter
    overrides = overrides or {}
    if indent_w is None:
        indent_w = LAYER_WIDTHS["B"]
    hdr_rgb6 = PALETTE["TBL_HEADER"]
    first_idx = column_index_from_string(first_col)
    if max_row is None:
        max_row = ws.max_row or 1

    # discover rightmost column that holds any value
    if last_col is None:
        last_idx = first_idx
        for row in ws.iter_rows(min_row=1, max_row=max_row):
            for c in row:
                if c.value is not None and c.column >= first_idx:
                    last_idx = max(last_idx, c.column)
    else:
        last_idx = column_index_from_string(last_col)

    for cidx in range(first_idx, last_idx + 1):
        col = get_column_letter(cidx)
        if col in overrides:
            ws.column_dimensions[col].width = overrides[col]
            continue
        header_widths, body_widths = [], []
        any_content = False
        for r in range(1, max_row + 1):
            c = ws.cell(row=r, column=cidx)
            if c.value is None:
                continue
            any_content = True
            is_wrap = bool(c.alignment and c.alignment.wrap_text)
            is_header = False
            if c.fill and c.fill.patternType == "solid":
                rgb = getattr(c.fill.start_color, "rgb", None)
                if isinstance(rgb, str) and rgb[-6:].upper() == hdr_rgb6.upper():
                    is_header = True
            # A full BOX (all 4 sides) or a hyperlink marks a TABLE / 출처 cell: it carries
            # content even when wrap=False and its right neighbor is empty (e.g. a table's
            # LAST data column = 출처 rcpNo 링크). Such cells MUST fit — never collapse to
            # indent_w. A lone bottom border (title/divider underline) is NOT a box, so
            # titles still overflow rightward as before. (국내사 해외법인 세션 피드백 #1)
            bd = c.border
            is_box = bool(bd and all(getattr(bd, s) and getattr(bd, s).style
                                     for s in ("left", "right", "top", "bottom")))
            has_link = c.hyperlink is not None
            right = ws.cell(row=r, column=cidx + 1)
            right_blocked = right.value is not None
            dw = _disp_width(c.value)
            if is_header:
                header_widths.append(dw)
            elif is_wrap or right_blocked or is_box or has_link:
                body_widths.append(dw)
            # else: pure-overflow cell → ignore for sizing
        if not header_widths and not body_widths:
            # pure-overflow / indent column (incl. left margin & banner columns)
            ws.column_dimensions[col].width = indent_w
            continue
        body_widths.sort()
        outlier_min = 0.0
        if body_widths:
            bmax = body_widths[-1]
            if bmax <= short_threshold:
                body_target = bmax           # short tokens fit fully
            else:
                body_target = _percentile(body_widths, pct)  # long prose wraps
            # Outlier floor: when a column mixes short cells (e.g. source codes) with a
            # long-prose outlier (e.g. one table's long column sharing the letter), the
            # percentile sizes to the short majority and the outlier wraps to 8-10 lines
            # (clipping risk + ugly tall row). Raise the floor so the LONGEST cell wraps
            # to at most ~max_wrap_lines (within max_w). 0.72 ≈ usable fraction per line.
            outlier_min = bmax / (max_wrap_lines * 0.72)
        else:
            body_target = 0.0
        target = max(max(header_widths) if header_widths else 0.0, body_target, outlier_min)
        ws.column_dimensions[col].width = max(min_w, min(max_w, target + padding))

    # Pad the empty trailing columns (after the content edge) out to `pad_to` (default Z),
    # so long overflow lines — support()/<중간 결론>/감점 sentences that flow rightward from the
    # narrow layer zone — have room to COMPLETE within the visible A→Z span instead of running
    # off-screen past Z. The banner fill also spans to Z, so this keeps fill + text aligned.
    # (User: 내용 없는 열(L~Z)도 너비 늘려 웬만하면 Z에서 모든 내용이 끝나게.)
    if pad_to:
        pad_idx = column_index_from_string(pad_to)
        for cidx in range(last_idx + 1, pad_idx + 1):
            col = get_column_letter(cidx)
            if col not in overrides:
                ws.column_dimensions[col].width = pad_w


def rightmost_used_col(ws, max_row=None, min_col=2):
    """Return the rightmost column letter holding a value or a solid fill.
    Use to span banners to the content's right edge instead of hardcoding 'K':
        span = S.rightmost_used_col(sm)
        S.task_banner(sm, r, title, span_to=span)
    """
    from openpyxl.utils import get_column_letter
    if max_row is None:
        max_row = ws.max_row or 1
    right = min_col
    for row in ws.iter_rows(min_row=1, max_row=max_row):
        for c in row:
            has_fill = bool(c.fill and c.fill.patternType == "solid")
            if (c.value is not None or has_fill) and c.column >= min_col:
                right = max(right, c.column)
    return get_column_letter(right)


# Standard narrow widths for layer-zone columns (A through E).
# All layer columns are equally narrow — text overflows rightward into content zone.
# Reference: user requirement after v4 — "CDE 열을 A,B 열과 동일한 너비로 통일".
LAYER_WIDTHS = {"A": 2.3, "B": 3.2, "C": 3.2, "D": 3.2, "E": 3.2}

# Intro sheet uses wider D — meta labels like '과제 설명', 'v15 핵심 변경 사항',
# '접근 방식' don't fit at D=3.2. E stays narrow because intro_meta_value()
# overflows right into empty F-K cells.
INTRO_LAYER_WIDTHS = {"A": 2.3, "B": 3.2, "C": 3.2, "D": 15.0, "E": 3.2}


def set_layer_widths(ws, extra=None, cde_width=3.2):
    """Apply NARROW widths to layer-zone columns (A–E) so they read as an indentation
    staircase — section labels (C), numbered headers / '<중간 결론>' (D), and body
    sentences (E) overflow rightward across the empty content cells. This is the
    correct house-style: layer columns hold markers, not content.

    A/B stay tight (≈2.3 / 3.2) and C/D/E default to `cde_width=3.2` (= column B).
    KEEP THEM NARROW — at Excel's default (~8.43) the layer zone looks too wide and the
    staircase breaks (real feedback: "CDE 열이 너무 넓다"). The overflow pattern means
    narrow C/D/E does NOT clip section labels / headers, because the cells to their right
    on those rows are empty (banners span-fill; header rows are followed by tables one row
    below). So narrow is both correct and safe.

    cde_width: width for C/D/E (default 3.2). Pass `None` ONLY in the rare case a layer
    column genuinely holds standalone content that must not overflow — almost never.
    Do NOT use None just to "make it readable"; readability comes from overflow, not width.

    For the Intro sheet use `set_intro_widths()` (D widened for meta labels).

    IMPORTANT: this sizes LAYER columns only. For content columns (F onward) do NOT
    hardcode via `extra=` — call `autofit_columns(ws)` at the end of the build so the
    content zone adapts to the text. `extra=` remains for rare manual pins (e.g. RAW
    Data's wide source column).

    extra: optional {col: width} to also apply (overrides the above for those columns).
    """
    widths = {"A": LAYER_WIDTHS["A"], "B": LAYER_WIDTHS["B"]}
    if cde_width is not None:
        widths.update({"C": cde_width, "D": cde_width, "E": cde_width})
    if extra:
        widths.update(extra)
    set_widths(ws, widths)


def set_intro_widths(ws, extra=None):
    """Intro-specific layer widths. D is widened to ~15 to accommodate meta labels
    placed there by `intro_meta_label()` ('Date', 'Editor', '버전', '과제 설명',
    '분석 구조', '분석 기간', '출처 유형' etc.) — at D=3.2 the longer Korean labels
    are visually clipped or rely on overflow into a column that already holds the
    meta-value (E), producing a cramped layout.

    Use this helper on the Intro sheet instead of `set_layer_widths()`.

    extra: optional dict of additional column widths.
    """
    widths = dict(INTRO_LAYER_WIDTHS)
    if extra:
        widths.update(extra)
    set_widths(ws, widths)


# =============================================================
# RAW Data sheet — entry helper (auto-detect URL and linkify)
# =============================================================

import re
_URL_RE = re.compile(r'(https?://[^\s)\]]+)')


def raw_data_entry(ws, row, text, col="C"):
    """Write a RAW Data source line. If the line contains a URL, auto-detect it
    and make the cell a clickable external hyperlink.

    Common pattern: a source line looks like
        "RWA.xyz 사모신용: https://app.rwa.xyz/private-credit (활성 222억) [1차·검증]"
    This helper extracts the URL and applies it as the cell's hyperlink so the
    reader can click straight to the source. Visible text stays the full line,
    but the cell becomes blue/underlined to signal it's clickable.

    Without this helper, RAW Data lines written via `support()` carry the URL
    only as text (not clickable). Common bug introduced when source lines are
    bulk-loaded into RAW Data via plain text writes.
    """
    c = _put(ws, row, col, text, wrap=False)
    m = _URL_RE.search(text)
    if m:
        url = m.group(1).rstrip('.,;:)')
        c.hyperlink = url
        f = c.font
        c.font = Font(name=f.name, size=f.size, bold=f.bold,
                      color="FF0563C1", underline="single")
    return c


def raw_data_section(ws, row, header, lines, col_header="B", col_body="C"):
    """Write one full source section block in RAW Data.

    Returns (header_row, next_row) — header_row is what Summary's <Source N>
    tag should linkify_internal() to.

    Example:
        hdr, nxt = raw_data_section(raw, 4, "Source 1 — 시장 개괄", [
            "RWA.xyz: https://app.rwa.xyz/... [1차·검증]",
            "한국 STO: https://www.asiae.co.kr/... [2차]",
        ])
        # nxt is row after this block (with one blank line gap)
        # ... build next section starting at nxt ...
        linkify_internal(src1_cell, f"'RAW Data'!B{hdr}")
    """
    key_line(ws, row, header, col=col_header)
    header_row = row
    row += 1
    for line in lines:
        raw_data_entry(ws, row, line, col=col_body)
        row += 1
    return header_row, row + 1


# =============================================================
# Intro sheet — lightweight meta helpers
# =============================================================

def intro_meta_label(ws, row, label, col="D"):
    """Intro meta-label (e.g., 'Date', 'Editor', '과제 설명', '분석 구조', '분석 기간', '출처 유형').
    Bold only, NO fill, NO border — matches the reference Intro pattern.

    Label vocabulary (엑셀 형식 피드백): use '분석 구조'(NOT '접근 방식'), '분석 기간/전망 시계'
    (NOT bare '시계'), '출처 유형'(NOT '출처 등급'). Do NOT add a '내용 기준' row.

    Intro is a lightweight metadata sheet — it should NOT use section_label()
    (the green-banded L1 label suited for Summary). Use this helper for all
    Intro labels to keep the visual register lighter than Summary.

    Pair the Intro sheet with `set_intro_widths()` (NOT `set_layer_widths()`)
    so the D column is wide enough (~15) to display the longer Korean labels.
    """
    return _put(ws, row, col, label, bold=True)


def intro_meta_value(ws, row, value, col="E"):
    """Intro meta-value (paired with intro_meta_label). Plain text in col E,
    overflows right into adjacent empty cells (F-K are blank on Intro sheet).

    Requires the Intro sheet to have been sized via `set_intro_widths()` so the
    label in D is fully visible and the value in E starts at a stable position.
    """
    return _put(ws, row, col, value)


# =============================================================
# Self-check helpers (call before delivery)
# =============================================================

def count_key_font_cells(wb):
    """Count cells using KEY_FONT (blue text). Policy: ≤2 per workbook (in Summary).

    Returns int. Call this before delivery to verify color discipline.
    NOTE: alpha-channel safe — compares only last 6 RGB hex chars.
    """
    target_rgb6 = PALETTE["KEY_FONT"]
    n = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.font and c.font.color:
                    rgb = getattr(c.font.color, "rgb", None)
                    if isinstance(rgb, str) and rgb[-6:].upper() == target_rgb6.upper():
                        n += 1
    return n


def count_highlight_cells(wb):
    """Count cells using HIGHLIGHT fill (pale green). Returns dict {sheet_name: count}.

    Each table row counts as N cells (one per column). Useful to verify
    no table has > 2 highlight rows.
    NOTE: openpyxl may save/load fill rgb with different alpha prefixes
    ('FF' vs '00'), so we compare only the last 6 hex chars (RGB body).
    """
    target_rgb6 = PALETTE["HIGHLIGHT"]  # 6-char hex
    out = {}
    for ws in wb.worksheets:
        n = 0
        for row in ws.iter_rows():
            for c in row:
                if c.fill and c.fill.patternType == "solid":
                    rgb = getattr(c.fill.start_color, "rgb", None)
                    if isinstance(rgb, str) and rgb[-6:].upper() == target_rgb6.upper():
                        n += 1
        if n:
            out[ws.title] = n
    return out


# =============================================================
# Version management — filename collision check
# =============================================================

def next_available_path(base_path):
    """Given a desired output path, return a path that does not collide with
    any existing file in the same directory.

    Rules:
    - If `base_path` does not exist, return it unchanged.
    - If `base_path` exists, scan the directory for files matching the same
      stem and pick the next available version suffix.
    - Version suffix patterns recognized: `_v2`, `_v3`, `_v3_2`, etc.
      If the base path has no version suffix, append `_v2` and bump from there.

    Example:
        next_available_path("/mnt/user-data/outputs/consulting_RWA.xlsx")
            → if directory has consulting_RWA.xlsx already, returns
              "/mnt/user-data/outputs/consulting_RWA_v2.xlsx"
            → if v2 also exists, returns _v3, etc.

        next_available_path("/mnt/user-data/outputs/consulting_RWA_v5.xlsx")
            → if v5 exists, returns "/mnt/user-data/outputs/consulting_RWA_v6.xlsx"
            → if v5 and v6 exist, returns _v7
            → if v5_2 patterns also exist, picks the next integer above current max

    This prevents silent overwrites — every build produces a new filename
    that the user can keep alongside earlier versions for reference.
    """
    from pathlib import Path
    import re

    p = Path(base_path)
    if not p.exists():
        return str(p)

    stem = p.stem  # e.g. "consulting_RWA_v5" or "consulting_RWA"
    suffix = p.suffix  # ".xlsx"
    parent = p.parent

    # Detect existing version suffix on the base stem
    m = re.search(r'_v(\d+)(?:_(\d+))?$', stem)
    if m:
        base_stem = stem[:m.start()]
        current_major = int(m.group(1))
    else:
        base_stem = stem
        current_major = 1  # treat unsuffixed as "v1"

    # Scan directory for siblings matching base_stem
    pattern = re.compile(re.escape(base_stem) + r'(?:_v(\d+)(?:_(\d+))?)?$')
    max_major = current_major
    for f in parent.glob(f"{base_stem}*{suffix}"):
        fm = pattern.match(f.stem)
        if fm:
            major = int(fm.group(1)) if fm.group(1) else 1
            if major > max_major:
                max_major = major

    # Pick next major version
    next_major = max_major + 1
    new_stem = f"{base_stem}_v{next_major}"
    return str(parent / f"{new_stem}{suffix}")


def resolve_output_path(desired_path, *, auto_bump=True, verbose=True):
    """Wrap next_available_path with a small UX layer.

    - If `auto_bump=False`: raise FileExistsError when the desired path exists
      (caller must decide what to do).
    - If `auto_bump=True` (default): silently pick the next available path
      and (if verbose) print a notice.

    Use this at the TOP of every build function — before `wb.save(path)`:

        def build():
            desired = "/mnt/user-data/outputs/consulting_RWA_사업모델.xlsx"
            path = S.resolve_output_path(desired)
            wb = Workbook()
            ...
            wb.save(path)
    """
    from pathlib import Path
    if not Path(desired_path).exists():
        return desired_path
    if not auto_bump:
        raise FileExistsError(
            f"Output path already exists: {desired_path}\n"
            f"Pass a new filename, or use auto_bump=True."
        )
    new_path = next_available_path(desired_path)
    if verbose:
        print(f"[version] {Path(desired_path).name} exists. Using {Path(new_path).name} instead.")
    return new_path


# =============================================================
# vN_M 버전 규율 가드 (덮어쓰기 차단 + 형식 강제) — 모델이 자의로 못 끄게 굳힘
# =============================================================
#
# WHY: resolve_output_path는 flat _vN 충돌회피만 하고(→ v1_0 다음이 v2), path=OUT로
# 우회해 덮어쓸 수 있었다. 아래 가드는 (1) 산출물 파일명을 vN_M로 *강제*하고
# (2) 기존 버전 파일의 *조용한 덮어쓰기를 예외로 차단*한다(overwrite_ok 없으면 raise).
# 반복 빌드는 wip_path()/save_wip()으로 스크래치에. (게이트 deliverable_name floor와 짝 —
# 빌드 코드로 이 가드를 우회해도 stepgate publish 게이트가 vN_M 아니면 FAIL.)

import re as _re
_VNM_RE = _re.compile(r"_v\d+_\d+\.[A-Za-z0-9]+$")   # ..._v1_0.xlsx (vN_M)


def is_versioned(path):
    """파일명이 vN_M 형식(..._v{N}_{M}.<ext>)인가."""
    from pathlib import Path
    return bool(_VNM_RE.search(Path(path).name))


def next_version_path(folder, base_stem, *, level="minor", ext="xlsx"):
    """folder 안의 `{base_stem}_vN_M.{ext}` 중 최신을 찾아 *다음 vN_M* 경로를 반환.
    첫 발행이면 v1_0. level='minor'면 v1_1→v1_2, level='major'면 v1_2→v2_0.
    resolve_output_path와 달리 flat _vN(v2)가 아니라 항상 proper vN_M을 낸다.
    반환 경로는 항상 존재하지 않는 파일(덮어쓰기 없음).
    """
    from pathlib import Path
    import re
    folder = Path(folder)
    pat = re.compile(re.escape(base_stem) + r"_v(\d+)_(\d+)\." + re.escape(ext) + r"$")
    best = None
    if folder.is_dir():
        for f in folder.glob(f"{base_stem}_v*.{ext}"):
            m = pat.match(f.name)
            if m:
                nm = (int(m.group(1)), int(m.group(2)))
                if best is None or nm > best:
                    best = nm
    if best is None:
        n, m = 1, 0
    elif level == "major":
        n, m = best[0] + 1, 0
    else:
        n, m = best[0], best[1] + 1
    p = folder / f"{base_stem}_v{n}_{m}.{ext}"
    while p.exists():           # 안전: 혹시 존재하면 minor 계속 bump
        m += 1
        p = folder / f"{base_stem}_v{n}_{m}.{ext}"
    return str(p)


def _coerce_nonformula_text(wb):
    """Excel '수식 오인 → 파일 손상' 방지 가드(저장 직전 호출).
    openpyxl은 '='로 시작하는 문자열을 formula로 저장 → Excel이 파싱 실패 시 파일을 손상시키고
    'Removed Records: Formula' 복구 팝업을 띄운다. 데이터/라벨이 '='로 시작하지만 *유효한 수식이
    될 수 없는* 경우만 강제 텍스트(data_type='s')로 만든다:
      · 한글(가–힣) 포함  · '==' 로 시작(예: 이메일 '==>' 마커)  · '=' 바로 뒤 공백.
    이 세 패턴은 결코 유효 수식이 아니므로, 실제 수식('=A1/B1', '=SUM(...)')은 건드리지 않는다(회귀 0).
    실사례: expert-vq 답변 '==> ...' 4칸이 formula로 저장돼 손상."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v.startswith("=") and (
                    v.startswith("==") or v[:2] == "= "
                    or any('가' <= ch <= '힣' for ch in v)
                ):
                    c.data_type = "s"


def save_deliverable(wb, path, *, overwrite_ok=False):
    """산출물 저장 가드 — vN_M 형식 강제 + 기존 파일 조용한 덮어쓰기 차단.
    - 파일명이 vN_M(..._v1_0.xlsx)이 아니면 ValueError(base·flat _vN·_wip 거부).
    - 파일이 이미 있으면 overwrite_ok=True가 아닌 한 FileExistsError → 새 버전 발행 강제.
    - overwrite_ok=True는 loud 경고와 함께만(예외 escape hatch, 조용한 덮어쓰기 아님).
    반복 빌드/스크래치는 save_wip() 사용. 다음 버전 경로는 next_version_path()로.
    """
    from pathlib import Path
    p = Path(path)
    if not _VNM_RE.search(p.name):
        raise ValueError(
            f"산출물 파일명은 vN_M 형식이어야 함(예: _v1_0.xlsx) — got '{p.name}'. "
            f"next_version_path(folder, base_stem)로 생성하라. 반복 빌드는 save_wip().")
    if p.exists() and not overwrite_ok:
        raise FileExistsError(
            f"'{p.name}' 이미 존재 — 덮어쓰기 금지(버전 규율). "
            f"next_version_path(level='minor')로 새 vN_M을 발행하라. "
            f"정말 덮어쓰려면 overwrite_ok=True(비권장·loud).")
    if p.exists() and overwrite_ok:
        print(f"[styles.save_deliverable] ⚠️ 경고: 기존 '{p.name}' 덮어씀(overwrite_ok=True) — 버전 규율 예외.")
    _coerce_nonformula_text(wb)   # '=' 시작 데이터가 formula로 오인돼 파일 손상되는 것 방지(실수식은 보존)
    wb.save(str(p))
    print(f"[styles.save_deliverable] 발행: {p.name}")
    return str(p)


def wip_path(folder, base_stem, *, ext="xlsx"):
    """반복 빌드용 스크래치 경로 `_wip_{base_stem}.{ext}`(버전 아님·덮어쓰기 허용)."""
    from pathlib import Path
    return str(Path(folder) / f"_wip_{base_stem}.{ext}")


def save_wip(wb, path):
    """스크래치(WIP) 저장 — 파일명에 '_wip' 필수. 정식 발행은 save_deliverable()."""
    from pathlib import Path
    p = Path(path)
    if "_wip" not in p.name:
        raise ValueError(f"save_wip은 _wip 스크래치 전용 — got '{p.name}'. 정식 발행은 save_deliverable().")
    _coerce_nonformula_text(wb)   # '=' 시작 데이터가 formula로 오인돼 파일 손상되는 것 방지(실수식은 보존)
    wb.save(str(p))
    print(f"[styles.save_wip] WIP(비발행): {p.name}")
    return str(p)


def selfcheck_from_path(path):
    """Run all self-checks on a saved file. Returns dict with counts and warnings.

    Recommended usage: call AFTER wb.save(path), passing the saved path.
    This avoids in-memory openpyxl quirks where fill/font properties
    aren't yet finalized.
    """
    from openpyxl import load_workbook
    wb = load_workbook(path)
    key_n = count_key_font_cells(wb)
    hi = count_highlight_cells(wb)
    warnings = []
    if key_n > 2:
        warnings.append(f"KEY_FONT cells: {key_n} (policy ≤2). See references/color-policy.md")
    # NOTE: per-table highlight ≤2 is already enforced at make_table() (prints a warning
    # if highlight_rows>2). A per-SHEET cell-count threshold here false-positives on any
    # deliverable with many tables (10 tables × 1-2 rows × several cols easily exceeds 20
    # cells while every table is compliant). So we do NOT raise a sheet-level highlight
    # warning — it was pure noise. The count stays in the returned dict for inspection.

    # RAW Data hyperlink check: every cell containing 'http' must be a clickable hyperlink
    if "RAW Data" in wb.sheetnames:
        raw = wb["RAW Data"]
        unlinked = 0
        for row in raw.iter_rows():
            for c in row:
                if c.value and isinstance(c.value, str) and "http" in c.value:
                    if not (c.hyperlink and getattr(c.hyperlink, "target", None)):
                        unlinked += 1
        if unlinked > 0:
            warnings.append(
                f"RAW Data: {unlinked} cells contain a URL but no clickable hyperlink. "
                f"Use S.raw_data_entry() or S.raw_data_section() (not S.support())."
            )

    # --- 반복 실패 3종 기계 점검 (2026.6 세션: 빌드마다 재발하던 것 — wrap·lead=·탭색) ---
    # ① wrap_text=True 인데 표 셀(4면 box)·헤더가 아닌 셀 = 좁은/오버플로 셀의 의도치 않은 줄바꿈
    #    → 행 높이 폭발 + 그 열이 max_w로 고정(실피드백 "D·E 너비 난리", "표 밖 텍스트 자동 줄바꿈").
    #    표 본문(box)·헤더만 wrap 허용; support()/evidence()/exec 산문은 wrap=False(오른쪽 흘림).
    # ② 평문 URL(http로 시작 + 하이퍼링크 없음) = 링크 누락(행/헤더 수 불일치 stray).
    # ③ '='로 시작 + 한글 = Excel이 수식으로 오인해 셀 제거(파일 열 때 "Removed Records: Formula" 복구 팝업).
    bad_wrap, stray_http, lead_eq = [], [], []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None:
                    continue
                wrap = bool(c.alignment and c.alignment.wrap_text)
                bd = c.border
                is_box = bool(bd and all(getattr(bd, s) and getattr(bd, s).style
                                         for s in ("left", "right", "top", "bottom")))
                is_hdr = False
                if c.fill and c.fill.patternType == "solid":
                    rgb = getattr(c.fill.start_color, "rgb", None)
                    if isinstance(rgb, str) and rgb[-6:].upper() == PALETTE["TBL_HEADER"].upper():
                        is_hdr = True
                if wrap and not is_box and not is_hdr:
                    bad_wrap.append(f"{ws.title}!{c.coordinate}")
                if isinstance(v, str) and v.startswith("http") and not c.hyperlink:
                    stray_http.append(f"{ws.title}!{c.coordinate}")
                if c.data_type == "f" and isinstance(v, str) and (
                        v.startswith("==") or any('가' <= ch <= '힣' for ch in v)):
                    lead_eq.append(f"{ws.title}!{c.coordinate}")
    if bad_wrap:
        warnings.append(f"wrap_text=True on {len(bad_wrap)} non-table cell(s) — 좁은/오버플로 셀은 wrap=False여야(행 폭발·열폭 max 고정): {bad_wrap[:8]}")
    if stray_http:
        warnings.append(f"{len(stray_http)} plain-URL cell(s) without hyperlink (행>헤더 수 불일치 stray): {stray_http[:8]}")
    if lead_eq:
        warnings.append(f"{len(lead_eq)} formula cell(s) with 한글/'==' (저장 가드 우회 — Excel이 수식 오인·제거): {lead_eq[:8]}")
    # ④ Intro/Summary 탭 색 미설정 (Intro=연녹 SECTION·Summary=진녹 TASK)
    for sh in ("Intro", "Summary"):
        if sh in wb.sheetnames and not wb[sh].sheet_properties.tabColor:
            warnings.append(f"{sh} 시트 탭 색 미설정 — set_tab_color(Intro=PALETTE['SECTION']·Summary=PALETTE['TASK'])")

    return {
        "key_font_cells": key_n,
        "highlight_cells_by_sheet": hi,
        "bad_wrap": bad_wrap,
        "stray_http": stray_http,
        "lead_eq": lead_eq,
        "warnings": warnings,
        "ok": len(warnings) == 0,
    }


# =============================================================
# Image embed helpers — anchor drift 방지 + auto-height 보존
# =============================================================

def embed_image_inline(ws, path, anchor_row, anchor_col="F", max_width=500,
                        pin_image_rows=True):
    """Embed an image with explicit OneCellAnchor — guarantees the image lands at
    exactly the requested row.

    Key design: only set row_dimensions on the image's own row range (drift防止),
    leaving all other rows at height=None so make_table()'s auto-height for wrapped
    text remains effective.

    Args:
        ws: openpyxl worksheet
        path: image file path
        anchor_row: 1-indexed Excel row for image top-left
        anchor_col: column letter (e.g. 'F')
        max_width: resize so width <= max_width pixels (height proportional)
        pin_image_rows: if True, set row_dimensions[r].height=15 only for the
                        rows the image occupies, preventing OneCellAnchor →
                        TwoCellAnchor drift on save/load. Other rows untouched.

    Returns dict: {width, height, rows_needed, image_end_row}
    """
    from openpyxl.drawing.image import Image as _XLImage
    from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils.units import pixels_to_EMU
    from PIL import Image as _PILImage
    import io as _io

    with _PILImage.open(path) as pil_img:
        w, h = pil_img.size
        if w > max_width:
            ratio = max_width / w
            new_w, new_h = max_width, int(h * ratio)
            pil_img = pil_img.resize((new_w, new_h), _PILImage.LANCZOS)
        else:
            new_w, new_h = w, h
        buf = _io.BytesIO()
        pil_img.save(buf, format='PNG')
        buf.seek(0)

    xl_img = _XLImage(buf)
    col_idx = ord(anchor_col.upper()) - ord('A')

    anchor = OneCellAnchor()
    anchor._from = AnchorMarker(col=col_idx, colOff=0, row=anchor_row - 1, rowOff=0)
    anchor.ext = XDRPositiveSize2D(cx=pixels_to_EMU(new_w), cy=pixels_to_EMU(new_h))
    xl_img.anchor = anchor
    ws.add_image(xl_img)

    # Excel default row height = 15 pt = 20 px. Image rows = h / 20.
    rows_needed = int(new_h / 20) + 2

    # Pin only the image's own row range — other rows stay height=None (auto)
    if pin_image_rows:
        for r in range(anchor_row, anchor_row + rows_needed):
            if ws.row_dimensions[r].height is None:
                ws.row_dimensions[r].height = 15

    return {
        "width": new_w,
        "height": new_h,
        "rows_needed": rows_needed,
        "image_end_row": anchor_row + rows_needed,
    }


def finalize_row_heights(ws, col_widths_chars=None, max_row=None,
                         line_pt=16.0, base_pt=6.0, min_pt=15.0, usable_frac=0.74,
                         mode="native"):
    """Row heights. DEFAULT mode='native' = let EXCEL's built-in *AutoFit Row Height* size
    each row to its wrapped content when the file is OPENED — no hardcoded estimate (openpyxl
    has no render engine, so any pre-computed height is a guess that breaks when text/columns
    change; the user explicitly asked to use Excel's native autofit instead). Native works
    because we write NO customHeight for text rows, and table body cells carry wrap_text=True
    (make_table default) — Excel then auto-fits on open. Image-pinned rows are preserved.

    mode='estimate' = legacy pre-compute (CJK-aware) for HEADLESS use (PDF/print/render server)
    where Excel never recalculates. Slightly off when text changes — that's why native is default.

    [estimate mode] Set every row's height so wrapped table cells are FULLY visible (no clipping) —
    and so image anchors don't drift. Call at the END of the build, AFTER
    autofit_columns(), right before wb.save().

    Per row:
    - height already set (e.g. embed_image_inline pinned image rows) → left as-is
    - has wrap_text cells → estimate wrap-line count from each cell's text vs its
      column's ACTUAL width, take the max, set height = base + lines*line_pt
    - otherwise → min_pt (one line)

    Line count uses the column's REAL width (from ws.column_dimensions, set by
    autofit_columns), not a hardcoded guess. CLIPPING FIX: a line is assumed to hold
    only `usable_frac` (default 0.80) of the column's nominal width in display units —
    because Korean/CJK glyphs render WIDER than the 1.9 estimate, Excel adds cell
    padding, and word-boundary wrapping wastes trailing space. Under-counting lines is
    what clips content, so we deliberately round generously (slightly tall is fine,
    cut-off is not).

    Args:
        col_widths_chars: optional {col_letter: width} override. Default reads the
                          sheet's real column widths.
        line_pt: points per wrapped line (CJK 11pt ≈ 16pt incl. padding).
        usable_frac: fraction of nominal column width usable per line (lower = taller).
    """
    from openpyxl.utils import get_column_letter
    import math

    # mode='native' (DEFAULT): write NO explicit text-row heights → Excel's built-in
    # AutoFit Row Height sizes each row to its wrapped content on open. make_table already
    # leaves body rows at height=None (customHeight auto-derives False), so Excel auto-fits;
    # image-pinned rows keep their explicit height. No hardcoded estimate. No-op by design.
    if mode == "native":
        return

    real_widths = {}
    for col, dim in ws.column_dimensions.items():
        if dim.width:
            real_widths[col] = dim.width
    widths = dict(real_widths)
    if col_widths_chars:
        widths.update(col_widths_chars)

    def col_w(letter):
        return widths.get(letter, 8.43)  # Excel default

    if max_row is None:
        max_row = (ws.max_row or 0) + 5

    for r in range(1, max_row + 1):
        if ws.row_dimensions[r].height is not None:
            continue

        max_lines = 1
        for c in ws[r]:
            if c.value is None or not isinstance(c.value, str):
                continue
            if not (c.alignment and c.alignment.wrap_text):
                continue  # overflow cells render on one visual line
            w = col_w(get_column_letter(c.column))
            chars_per_line = max(4.0, w * usable_frac)  # conservative usable width
            # count lines per explicit \n segment, then sum (each segment ≥ 1 line)
            lines = 0
            for seg in c.value.split("\n"):
                seg_w = _disp_width(seg, cjk=2.0)  # ROW-HEIGHT은 cjk=2.0(docstring 준수). 1.9/0.82는 긴 한글 셀에서 2줄로 과소→3줄 클립(실피드백 2026.6). 2.0/0.74로 generous=클립 방지 우선
                lines += max(1, math.ceil(seg_w / chars_per_line))
            max_lines = max(max_lines, lines)

        if max_lines <= 1:
            ws.row_dimensions[r].height = min_pt
        else:
            ws.row_dimensions[r].height = base_pt + max_lines * line_pt


# =============================================================
# Wide-model / time-series MODE — financial models & wide numeric grids
# =============================================================
#
# WHY a separate mode: the pyramid layout (narrow A–E layer zone, tables starting
# at F, content-aware per-column autofit) is built for NARRATIVE research notes.
# A WIDE model — e.g. 10 year-columns × many line items — breaks under it:
#   • title/section labels sit at C while tables start at F → a dead C–E gap
#   • autofit sizes each year-column to its own content → widths look random
#   • a large title in a default-height row gets clipped
#   • divider()/single-column bottom borders leave a floating border in B
#
# MODEL MODE fixes all four: everything anchors at column B; section banners span
# the FULL table width (merged, so the fill never cuts off mid-row); the series
# columns share ONE uniform width; borders go on TABLE CELLS ONLY; row height is
# raised only for rows whose note actually wraps (others stay 15pt).
#
# Pipeline:  cols = model_widths(ws, n_series=10)
#            model_banner(ws, r, cols['label'], cols['end'], title,
#                         fill=PALETTE['TASK'], size=14)            # sheet title
#            model_header(ws, r, ['항목','Y1',...,'Y10','비고'], left_col=cols['label'])
#            model_row(ws, r, '활용률', [...], fmt='0.0%', note_col=cols['note'])
#
# In MODEL MODE do NOT call autofit_columns / finalize_row_heights / sheet_title /
# make_table — model_widths sets intentional uniform widths and model_row sets
# per-row heights. Mixing the pyramid helpers re-introduces the gaps/erratic
# widths this mode exists to avoid.
#
# NOTE on labels: a table label that STARTS WITH "=" (e.g. "= 합계") is parsed by
# Excel as a formula → #NAME?. Write summary-row labels without a leading "="
# ("합계 (억)", "· 합계", "Σ 합계"). model_row does not (and cannot) guess intent.

MODEL = {"spacer": 2.2, "label_w": 33, "series_w": 9, "note_w": 48}


def model_widths(ws, *, n_series=10, label_w=None, series_w=None, note_w=None,
                 label_col=2, has_note=True):
    """Set intentional, UNIFORM widths for a wide-model sheet (gridlines off).

    Layout: A=spacer · B=label · C..(C+n_series-1)=series (all ONE width) · last=note.
    The uniform series width is the fix for 'every year column a different random
    width'. Call ONCE at the top of the sheet build.

    Returns {'label','first','last','note','end'} column INDICES (note=None when
    has_note=False; end = note or last — use as the banner span edge).
    """
    label_w = label_w or MODEL["label_w"]
    series_w = series_w or MODEL["series_w"]
    note_w = note_w or MODEL["note_w"]
    ws.sheet_view.showGridLines = False
    if label_col > 1:
        ws.column_dimensions[get_column_letter(label_col - 1)].width = MODEL["spacer"]
    ws.column_dimensions[get_column_letter(label_col)].width = label_w
    first = label_col + 1
    last = label_col + n_series
    for i in range(first, last + 1):
        ws.column_dimensions[get_column_letter(i)].width = series_w
    note_col = None
    if has_note:
        note_col = last + 1
        ws.column_dimensions[get_column_letter(note_col)].width = note_w
    return {"label": label_col, "first": first, "last": last,
            "note": note_col, "end": note_col or last}


def model_banner(ws, row, c1, c2, text, *, fill=None, size=11, bold=True,
                 height=None, color="FF000000", align="left", wrap=False, span_to=26):
    """Full-width banner for MODEL MODE — NO cell merge (merges break copy/sort/filter
    and are visually fragile — avoid them). The colour fills from c1 through column
    `span_to` (default Z=26 — generous, so the banner never 'cuts off' short of the
    content); the title text sits in c1 and overflows rightward over the empty filled
    cells.

    fill=None → SECTION (light green); fill=PALETTE['TASK'] → title green;
    fill=False → no fill (e.g. an Executive-Summary headline). `c2` is accepted for
    backward-compat but the fill now always extends to `span_to`. Keep wrap=False on
    banners — with no merge, wrap would only wrap inside the narrow c1 cell.
    """
    fillrgb = PALETTE["SECTION"] if fill is None else (fill or None)
    c = _put(ws, row, get_column_letter(c1), text, bold=bold, size=size,
             color=color, fill=fillrgb, align=align, wrap=wrap)
    if fillrgb:
        _extend_fill(ws, row, get_column_letter(c1), get_column_letter(span_to), fillrgb)
    ws.row_dimensions[row].height = height or max(20, size + 12)
    return c


def model_header(ws, row, headers, *, left_col=2):
    """Bordered + TBL_HEADER-filled header row for a model table (borders on cells).
    Returns the next row."""
    fill_obj = PatternFill("solid", fgColor=PALETTE["TBL_HEADER"])
    for j, h in enumerate(headers):
        c = _put(ws, row, get_column_letter(left_col + j), h, bold=True, align="center")
        c.fill = fill_obj
        c.border = BOX
    return row + 1


def model_row(ws, row, label, cells, *, left_col=2, fmt=None, note="",
              note_col=None, bold=False, hilite=False, note_w=None):
    """One bordered grid row: label (left) + series cells (right, numfmt applied) +
    optional note (wrap). Row height is raised ONLY when the note wraps to >1 line,
    so numeric rows stay at the default height (no 'rows uselessly tall').

    cells may hold numbers OR formula strings ('=C5*C6'); empty '' cells are left
    unformatted. Pass note_col from model_widths()['note']. Returns the next row.
    """
    note_w = note_w or MODEL["note_w"]
    bg = PALETTE["HIGHLIGHT"] if hilite else None
    fill_obj = PatternFill("solid", fgColor=bg) if bg else None

    lc = _put(ws, row, get_column_letter(left_col), label, bold=bold, align="left")
    lc.border = BOX
    if fill_obj:
        lc.fill = fill_obj
    for j, v in enumerate(cells):
        c = _put(ws, row, get_column_letter(left_col + 1 + j), v, align="right")
        c.border = BOX
        if fill_obj:
            c.fill = fill_obj
        if fmt and v not in ("", None):
            c.number_format = fmt
    if note_col is not None:
        # 근거/비고 색 규칙(2026.6 사용자): 확실한 레퍼런스([벤치마크…])는 검정+10pt로 또렷이
        # (어떤 수치를 어디서 참고했는지 상세 기재), 추정·가정([가정]/[추정]/[초안]/[TBD]…)은
        # 회색 9pt로 흐릿하게 — 읽는 사람이 '확실한 수치'와 '추정'을 한눈에 구분하도록.
        referenced = note.lstrip().startswith("[벤치마크")
        nc = _put(ws, row, get_column_letter(note_col), note, align="left", wrap=True,
                  size=(10 if (note and referenced) else (9 if note else DEFAULT_SIZE)),
                  color=("FF000000" if (note and referenced) else ("FF808080" if note else "FF000000")))
        nc.border = BOX
        if fill_obj:
            nc.fill = fill_obj
        if note:
            lines = max(1, int(-(-_disp_width(note) // (note_w * 0.95))))
            if lines > 1:
                ws.row_dimensions[row].height = 14 * lines + 4
    return row + 1
