# Color policy

Rationale and anti-patterns for the 5 sanctioned colors. The single most common mistake on this format is **using too much color**, which creates visual fatigue and makes the document look less professional, not more.

## Principle: restraint, not abundance

Color is a tool for **drawing the reader's eye to one thing**. If you color five things in a section, the reader's eye does not know where to land. Net effect: less attention to the actually-important item.

Default state: **black text on white background.** Color is added only when it serves a specific role.

## The 5 sanctioned colors

### 1) Task banner — `8ED973` (dark green)
- **Where:** L0 cell fill (column B, top of each major top-level section)
- **When:** Top-most section banners only. Typically the Task title and the "Summary" / "RAW Data" sheet titles.
- **Frequency:** One per major boundary. Not every section needs this — only the structurally top tier.

### 2) Section banner — `B4E5A2` (light green)
- **Where:** L1 cell fill (column C, major subsection labels)
- **When:** Labels like "Summary", "Implication" — the structural anchors.
- **Frequency:** Roughly one per numbered section.

### 3) Table header — `A6CAEC` (light blue)
- **Where:** Every table's header row.
- **When:** Required for every table. This is the one color that should be used liberally — without it, table boundaries collapse visually.
- **Frequency:** Every table header row.

### 4) Highlight row — `D9F2D0` (pale green)
- **Where:** Table body rows
- **When:** Only on rows carrying the **decisive insight** for that table
- **Hard limit:** Maximum 1-2 rows per table. **NEVER more than 2.**
- **Test:** If you ask "which row would I point at when verbally presenting this table?" — that is the highlight row. If your answer is "all of them are important", you have not done the analysis yet.

### 5) Key font — `0000FF` (blue text)
- **Where:** Cell font color (not fill)
- **When:** The single most critical insight sentence in the entire deliverable
- **Hard limit:** Maximum 1-2 cells across the entire Summary sheet. **NEVER more.**
- **Test:** If the document had to be reduced to one sentence, which would survive? That sentence gets blue. Everything else stays black.

## Anti-patterns (real examples from prior work)

**Anti-pattern 1: Blue text on every conclusion**
- ❌ 13 cells in Summary marked with `key=True` (blue font) — every `<중간 결론>` and every Implication item
- ✅ 1 cell — the single most decisive insight (e.g., "수익원의 미래는 자산수탁에 형성")

**Anti-pattern 2: Highlighting half the table**
- ❌ 5 highlighted rows in a 10-row asset class table because "all 5 are strengths"
- ✅ 0-1 highlighted rows. The "strength vs weakness" distinction belongs in a **column** (e.g., 정합도: 강점/중립/약점), not in row colors. Color only the row that is the *single most important* insight.

**Anti-pattern 3: Multiple fill colors in one section**
- ❌ Task banner + section banner + highlight row + another accent color in 5 consecutive rows
- ✅ Banners at section boundaries only. Body rows stay white.

**Anti-pattern 4: Color as decoration**
- ❌ "This section looks empty, let me add some color"
- ✅ White space is fine. Color must serve a role.

## When to break these rules

When editing an existing file that already uses a different palette: **inherit the existing file's palette**. Do not impose this skill's defaults. The "preserve existing templates" rule from the xlsx skill takes precedence.

## Sheet tab colors (optional)

- `Intro` tab: `95FFA1` (light)
- `Summary` tab: `00FF79` (darker)

Use only if it adds navigational value. Otherwise leave default.

## Why this matters

A consulting-style document is judged on signal-to-noise ratio. Every color you add is a signal claim — "look here, this matters". When everything claims to matter, nothing does. The whole point of pyramid principle is the same: surface the conclusion, demote the supporting detail. Color is the visual version of that hierarchy.
