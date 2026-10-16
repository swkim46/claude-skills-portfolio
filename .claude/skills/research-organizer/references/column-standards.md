# Column format standards

Inside one table column, **every cell uses the same unit, same format, same tag position.** Inconsistency in a single column breaks scannability instantly — the reader cannot compare rows.

## The rule

Pick one format per column. Apply it to every row. If a value doesn't fit (e.g., the data is unknown), use a standard placeholder, not a different format.

## Standard formats by column type

### Currency / amount

Use English unit suffixes: `M USD`, `B USD`.

| Good | Bad |
|------|-----|
| `4.6B USD` | `46억 달러` |
| `19.5M USD` | `$19.5M` (no space, mixed style) |
| `810M USD` | `8.1억 달러` |
| `2.75B USD` | `약 2.75B 달러 수준` |

**Why English form:** Korean unit conversion (억/조) and English (M/B) get mixed easily, breaking comparison. Pick English.

**Placeholder when unknown:** `비공개` (consistent Korean word, not "N/A" or "—" or "정보 없음")

### Investment amount column

Within one investment-amount column, every cell follows one of these forms:

- `XM USD [등급]` (e.g., `140M USD [검증]`)
- `XM USD 라운드` (e.g., `47M USD 라운드`)
- `자체` (for self-funded entities like BlackRock)
- `비공개`

Do not mix `1.4억 달러`, `9.5M USD`, `5억 달러+`, `수억 USD 수준` in the same column.

### AUM / scale column

Pick one label prefix per column and apply uniformly:

- `AUM X.XB USD` for fund-like entities
- `활성 대출 X.XB USD` for credit platforms
- `누적 분배 X.XB USD` for distribution platforms

If different rows describe genuinely different metrics, **add a metric label inside the cell** — not different formats:

```
AUM 3.4B USD / AUA 24.9B USD          ← Securitize (two metrics, labeled)
AUM 2.75B USD (USDY 2.14B + OUSG 0.61B) ← Ondo (one AUM, broken down)
```

### Reliability tags

Place after the value, in brackets. Position consistent within the column.

| Tag | Meaning |
|-----|---------|
| `[검증·SEC]` | Verified against SEC filing (1차) |
| `[검증·공식]` | Verified against official document (1차) |
| `[검증]` | Verified, single source confirmed (1차/2차) |
| `[2차]` | Secondary source (news, industry report) |
| `[추정]` | Estimated / inferred |

**Do not omit tags in a column where other rows have them.** If most rows have `[2차]` and one is verified, mark that one `[검증]` and leave the `[2차]` rows tagged.

### Percentage column

`0.15%`, not `0.0015` or `15bp`. Format cells with Excel format `0.00%` and store as decimal (0.0015). For prose tables, write the literal "0.15%" string.

### Year column

Text strings ("2024"), not numbers (Excel adds thousand separator: "2,024").

## Header naming convention

Headers in the same table should be **parallel in form**.

| Good | Bad |
|------|-----|
| 본사 / 자산군 / 규모 / 매출 / 누적 투자금 / 투자사 / 유형 | 본사 / what assets / 규모(AUM·거래) / 매출/수익 (공개 시) / Investment to date / 주요 투자사 |

Pick Korean OR English per table, not both. Avoid parenthetical clarifications in headers — put those in a top-right unit annotation instead.

## Top 10 player table — standard 8-column layout

When building a player comparison table, use this standard column set:

| Col | Header | Format |
|-----|--------|--------|
| 1 | 회사 | Plain name; `★` marker if highlight player |
| 2 | 본사 | `미국` / `싱가포르` / etc. |
| 3 | 자산군 | Short category label |
| 4 | 규모 | `AUM X.XB USD` or equivalent |
| 5 | 매출/수익 | `XM USD (시점) [등급]` or `비공개` or `운용보수 X.XX%` |
| 6 | 누적 투자금 | `XM USD [등급]` or `자체` or `비공개` |
| 7 | 주요 투자사·주주 | Comma-separated list, max 4-5 entries |
| 8 | 유형 | Short business-model label (e.g., 사모신용 운용형) |

Adjust columns 4-5 if the players measure different things, but keep the rest fixed.

## "No arbitrary narrowing" reminder

When categorizing items, if the source presents N categories, show all N. Use a **fit-score column** (e.g., 강점/중립/약점, 활성/초기/미발달) to express priority — not omission. See `wording-tone.md` for the rationale and `formatting-spec.md` for table layout details.
