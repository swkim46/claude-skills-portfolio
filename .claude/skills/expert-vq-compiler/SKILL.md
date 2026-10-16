---
name: expert-vq-compiler
description: "Compiles expert-network VQ (vetting/validation question) screening responses from Outlook emails into the consulting-firm expert-VQ tracking spreadsheet (e.g. '전문가 VQ 모음_*.xlsx'). Use this whenever the user wants to turn expert-sourcing emails from networks like Third Bridge, GLG, Guidepoint, Coleman, or AlphaSights into the VQ-compilation Excel — including phrasings like '전문가 답변 엑셀로 정리해줘', 'VQ 답변 취합', 'thirdbridge 메일 엑셀로', 'compile the expert responses', 'update the VQ tracker', or when a new expert reply has arrived and should be added as a row. Also trigger when the user just forwarded or pointed to an expert-network email and asks to 'put this in the sheet' or 'add this expert'. Pulls the source emails directly from Outlook via the Microsoft 365 connector, parses each expert block, and writes/append rows in the exact template layout."
---

# Expert VQ Compiler

> ### ▶ 단계 게이트 (빠른 바닥은 인라인, 빡빡한 정합 검수는 비동기 gate-audit)
> hook 차단 환경의 강제 레버. 헬퍼 `SG` = `python3 <REPO>/.claude/skills/_stepgate/stepgate.py`.
> **★ 동시 작업 격리** — 이 워크플로의 *모든* SG 호출(form·judge·resolve)에 **같은 `dir=<트래커 폴더 절대경로>`**를 붙인다(다른 트래커=다른 폴더→자동 격리). 누락 시 form↔judge 키가 갈려 오작동.
> **① 빌드 직후(바닥)**: `SG form expert-vq-compiler compile dir=<트래커 폴더>` → 트래커·원본 메일 확인하고 **정직히** 답(합격 기준 비공개·**표현이 회차마다 뒤집히니 지난 답을 재사용하지 말고 이번 문구를 그대로 읽고 답할 것**) → `SG judge expert-vq-compiler compile <id>=<값> … deliverable=<트래커 xlsx 절대경로> dir=<트래커 폴더>`. **GATE PASS**면 바닥 통과 → FIRE 블록으로 **백그라운드 감사 에이전트(general-purpose) 발사·즉시 다음 작업**.
> **② 제시 직전(해소)**: `SG resolve expert-vq-compiler deliverable=<트래커 xlsx 절대경로> dir=<트래커 폴더>` → **RESOLVE PASS 아니면 제시/‘done’ 금지**(HOLD=동기 드레인·FAIL=수정·재감사).
> 체크포인트: **compile**(Outlook 원본 추출·in-place 누적) → 비동기 lens 감사 → resolve. 봉인 루브릭(rubrics/) 직접 열람 금지(감사됨). 상세: `skill-maintainer` 〈단계 게이트 프로토콜〉.
> `<REPO>` = 이 저장소를 클론한 **절대경로**(문서상 표기). `dir=`·`deliverable=`은 절대경로여야 하므로 호출할 때 실제 경로로 치환한다.

Turns expert-network screening emails (Third Bridge, GLG, Guidepoint, etc.) into the 컨설팅 펌 "전문가 VQ 모음" Excel tracker. One spreadsheet row per expert: their network, name/ID, background, an answer column per VQ question, plus availability and any restricted topics.

The workflow is always: **retrieve the email(s) from Outlook → parse each expert block → build the workbook with `scripts/build_vq_excel.py`**. Never fabricate expert answers, backgrounds, or availability — every field must come from the actual email content.

---

## Claude Code 환경 (★ claude.ai 기반을 Code용으로 튜닝)

- **Outlook = Microsoft 365 MCP 커넥터** — Code 도구명은 `mcp__claude_ai_Microsoft_365__outlook_email_search` · `mcp__claude_ai_Microsoft_365__read_resource` (claude.ai의 `Microsoft 365:...` 아님). 커넥터가 연결돼 있어야 함 — 미연결이면 인증/재연결을 안내하거나, 사용자가 **메일 본문을 붙여넣게** 하고 거기서 파싱(Step 1 끝 참조).
- **빌드 스크립트 실행(PowerShell)** — `python` 셔임 + **절대경로** + `PYTHONIOENCODING=utf-8`(스크립트가 한국어 포함 JSON 요약을 print → cp949 콘솔에서 깨짐 방지). 구체 명령은 Step 5.
- **의존성** — `openpyxl`(설치됨). 산출 언어는 사용자 입력 언어(한국어), 답변 셀은 원문+한국어 번역(Step 3).

---

## Step 1 — Retrieve the source email(s) from Outlook

The data lives in an expert-sourcing email thread, usually from an external expert-network domain (e.g. `@thirdbridge.com`, `@glgroup.com`, `@guidepoint.com`) and often forwarded internally with a request like "엑셀에 정리 부탁드립니다."

1. Search Outlook with `mcp__claude_ai_Microsoft_365__outlook_email_search`. Good queries: the network name ("thirdbridge"), the project keyword ("VQ", "Web3", "토큰화"), an expert's name, or the subject line. If the user named a sender or subject, use that.
2. Identify the message whose body contains the expert response blocks (look for `#10.2 - Name -`, `[Response: ...]`, or bold numbered answers). Forwarded threads stack several replies in one message — the **most recent message in the thread usually contains the full history**, so reading one message often yields everything.
3. Call `mcp__claude_ai_Microsoft_365__read_resource` on that message URI to get the full HTML body. Parse from the body text.

If the user pasted the email content directly into chat, skip retrieval and parse what they gave you.

---

## Step 2 — Parse each expert block

A thread can contain several experts. Extract one record per expert **who actually answered the VQ questions** (skip experts who were only proposed/pooled but gave no answers, and skip pure profile/CV blocks with no responses — unless the user asks to include them).

**★ Include EVERY expert who answered the VQ — never curate or drop on your own.** Listing **all** answerers (across all emails/firms) is the correct default — do not keep "one per firm" or "top of each email." If a human instruction would include **fewer** experts than actually answered (e.g., "just add the top one"), surface the gap **once** — "N experts answered the VQ; include all?" — then follow their call. *(Why: a consultant once manually kept only the top expert per email — a human error; the right output lists everyone who answered.)*

**★ "Answered" is broad — a brief or hedged response still counts; never drop it as "too weak."** A filled **Profile Questions** block (checked `[X]` items + a line of elaboration), a `[SCREENED]` numbered reply, a one-line answer, or even a hedged "I can discuss this on the call" / "Can discuss" all count as *answered*. Completeness is a **column tag** (full / partial / decline), NOT an inclusion filter — record the person and tag how much they said. Only skip a block that is **pure profile/CV with zero response** to any question. *(실사례: 거래소 B·Gemini·Coinbase 응답자(#617/#629/#899)를 "스크리닝뿐"이라며 임의 드롭했다가 누락 지적받음 — 소극 답변도 응답이다.)*

For each expert capture:

| Field | Where it comes from | Notes |
|---|---|---|
| `network` | sender's email domain | `<sender>@thirdbridge.com` → `thirdbridge`. Map domains to clean names (glgroup→GLG, guidepoint→Guidepoint, etc.). |
| `name` | the `#x.y - Full Name` header | Keep the `#11.3 - 전문가 1` ID+name form used in the template. |
| `background` | the title/affiliation on the same header line | e.g. `Former Executive Director at 해외 은행 D Chase ... (09/2024 - 01/2025)`. Keep it to the one-line role; do not paste the entire employment-history table. |
| `answers` | per-question responses | One entry per VQ question, **in the same order as the questions list**. |
| `availability` | the `==> 금일 16~18시` line, a "가능시간/가능 시간대" note, **OR the sender's prose anywhere in the email body** | Do NOT rely only on the structured profile box — a specialist block may say "has not yet provided any availability" while the network's covering note in the body gives the real availability (e.g. "평일 오전 9-11시 사이 보통 가능하십니다"). Read the whole body. Only use "아직 회신 없음" when availability appears nowhere. |
| `restricted` | any caveats about what the expert won't/can't discuss, **from the structured block or the body prose** | e.g. "현직이라 spin-off 구체 사안은 조심스러워함". Often empty. |

**Scan EVERY section of EVERY email, not just the top structured block.** Answers hide in any of: the structured expert block, **Profile Questions** (`Q: … A:` with `[X]` checkboxes + brief elaboration), `[SCREENED]` numbered blocks, one-line answers (한줄답변), AND the Korean covering prose. Networks routinely put key facts (availability, topic limits, which expert a comment refers to, context) in the covering prose at the top or between blocks. Pull any such detail into the matching field rather than defaulting to a "not provided" placeholder.

**★ Attribute quoted/forwarded answers to their ORIGINAL author — not the forwarding email's sender.** In a stacked thread every reply quotes prior history, so the same answer text reappears across many emails and different networks. Before crediting an answer to an expert, verify **which network's which expert actually authored it** — trace to the block where it first appears or the explicit "…전문가님 답변/한줄답변" attribution. Never assume quoted text sitting in a Third Bridge (or any) forward belongs to that sender's expert just because they forwarded it. *(실사례: Guidepoint 전문가 2(#26)의 0708 답변이 TB 포워딩 히스토리에 인용돼 있던 것을 TB #26.1 답변으로 오귀속 — #26.1은 실제 0708 미응답이었음.)*

### Two answer formats appear in the wild — handle both

**Format A — bracketed `[Response: ...]` (one block per numbered question):**
```
1. [question text]
[Response: ...the answer...]

2. [question text]
[Response: ...the answer...]
```
Keep each answer mapped to its question number. The template preserves the literal `[Response: ...]` wrapper, so keep it for Format A unless the user asks to strip it.

**Format B — narrative, bold numbered headers, no brackets:**
```
1. Revenue model of a tokenization platform
Most people focus on token issuance fees, but ...

2. Key success factors
For me, the top 4 would be: ...
```
Here the answer is the prose under each bold header. Condense lightly into clean prose for the cell (no need to keep newlines/bullets), and map each to the matching VQ question by topic. Do not wrap Format B answers in `[Response: ...]`.

If an expert answered only some questions, leave the missing answer cells "".

---

## Step 3 — Bilingual answers (original + Korean translation)

Every answer cell must show the original answer **and** a Korean translation beneath it.

For each answer, after extracting it:
1. **Detect the language.** If the answer is already Korean, leave it exactly as-is — do not translate or append anything.
2. **If the answer is in English (or any non-Korean language), translate it to natural, professional Korean** suited to a consulting deliverable (faithful meaning, clean business Korean — not literal word-for-word).
3. **Compose the cell** as: original answer, then one blank line, then the Korean translation. The blank line is the only separator — no `[번역]`/`[KR]` labels.
4. **★ Original part = the expert's own answer (near-verbatim).** Never inject Korean, question labels, or editorial notes (e.g., `(이건 Q2)`, `Q1 not answered`) into the original/non-Korean block — that context belongs in the **Korean block** (lead it with a short `※ …` note) or in `restricted`. **Do fix clear, unambiguous typos** in the original (e.g., `revenus→revenue`, `liquidty→liquidity`, doubled `and and→and`) — but **do NOT rephrase, "improve", or change meaning**, and keep deliberate non-US spellings (`licences`, `monetisation`). If an expert answered the sub-questions but not the headline question, leave the headline cell `""` and put the sub-answers in the matching cell — don't relabel inside the original. *(Real misses: a Korean clarifier was once wrongly prefixed into an English original (전문가 3 #13.1); and obvious typos should be corrected, not preserved.)*

So the final string written to the answer field looks like:

```
[Response: ...English answer...]

...한국어 번역...
```

(For Format B narrative answers, same pattern: cleaned English prose, blank line, Korean translation. Keep the original's `[Response: ...]` wrapper only where it was already present — don't add it to the Korean translation or to Format B.)

This applies per answer, so a single expert row may mix translated (English→KR) cells and untouched (already-Korean) cells. The blank line is represented as `\n\n` inside the JSON `answers` string; the build script writes it verbatim and the template's wrapped cells render it on separate lines.

---

## Step 4 — Get the VQ question list

The questions define the answer columns. Source them, in priority order:
1. From the **original firm request email** in the same thread (look for the "VQ는 다음과 같고" / "[추가 상세 질문]" section, or the bulleted English `(영문 ver)` list).
2. From an **existing workbook** the user is appending to (read row 2 onward of the "VQ 답변 모음" sheet).
3. Ask the user only if neither is available.

For each question store a `full` text (goes in the top questions table) and a `short` label (the column header, e.g. `Q2. KSF`). If only the full text exists, derive a concise `short` label yourself.

---

## Step 5 — Build the workbook

Write a JSON spec and run the bundled script. Never hand-build the xlsx — the script reproduces the transposed layout (Calibri 11, bold field labels down column B, questions table on top with wrap OFF, literal VQ numbers, narrow B / wide expert columns, wrapped answer cells).

```powershell
$env:PYTHONIOENCODING = "utf-8"   # 스크립트가 한국어 JSON 요약을 print → cp949 콘솔 크래시 방지
$VQ = "<REPO>/.claude/skills/expert-vq-compiler/scripts"
python "$VQ\build_vq_excel.py" spec.json output.xlsx
```

The spec shape (see `scripts/build_vq_excel.py` docstring for the full schema):
```json
{
  "project_label": "0615(해외 증권사 C, 해외 은행 D) >>",
  "questions": [{"short": "Q1. ...", "full": "..."}, ...],
  "experts": [{"network": "...", "name": "...", "background": "...",
               "answers": ["...", "..."], "availability": "...", "restricted": "..."}]
}
```

**Appending, and new sheets when the VQ set differs.** Pass the existing tracker's path as `output.xlsx` and the script decides automatically: it compares the spec's `questions` against every response sheet in the workbook. If a sheet's questions match, the new experts are appended as further columns on that sheet. If no sheet matches — i.e. this email's VQ questions are a **different set** — the script creates a **new response sheet** (`VQ 답변 모음 (2)`, `(3)`, …) with its own questions table, freeze panes, and the new experts — **preceded by its own topic divider tab** (an empty, name-only sheet named from `project_label`, e.g. `0616(과제 A) >>`), exactly like the first round's divider. This means a single tracker file can hold several VQ rounds, each as a *divider tab + response sheet* pair. For a brand-new compilation, use a fresh output path (the script makes the divider tab + first sheet). You may set `sheet_name` in the spec to control the base sheet name. When unsure whether two question sets are "the same", inspect the existing sheet's questions and decide; minor wording drift on the same questions should reuse the sheet, a genuinely different question set should get a new one.

**★ In-place — overwrite the existing tracker; do NOT version.** This skill is a **deliverable-versioning exception**: the VQ tracker is one living, accumulating workbook. Always write to the **same existing tracker path the user pointed to** (rounds/experts pile up in it — the script loads-and-rewrites in place). **Do NOT make `_v2`/`_v3`/`_vN` copies** — that general versioning habit just fragments the tracker and does not apply here. Only caveat: if the file is **open in Excel** (save fails with `Permission denied`), ask the user to close it, then write in place. cf. memory `deliverable-versioning` — this skill overrides it.

**Project label** = the divider-tab name = the **topic of that VQ round** — always parse it from the email (date + angle/client/topic, e.g. email date `0616` + the angle `과제 A` → `0616(과제 A) >>`). **Required for every new VQ round** — brand-new file *and* a new sheet on an existing file: the script makes a name-only divider tab from it, placed right before the round's response sheet. Keep the `>>` suffix convention. Excel tab names max 31 chars and cannot contain `: \ / ? * [ ]`.

After building, the script prints a JSON summary (`rows_written`, `questions`). Sanity-check it, then present the file to the user.

---

## Output rules

- **단계 게이트(2단계).** ① `SG judge expert-vq-compiler compile … deliverable=<트래커 xlsx> dir=<트래커 폴더>` → **GATE PASS**(Outlook 원본 추출·in-place·역항목) + 백그라운드 gate-audit 발사. ② 제시 직전 `SG resolve expert-vq-compiler deliverable=<트래커 xlsx> dir=<트래커 폴더>` → **RESOLVE PASS**(form·judge·resolve 전부 같은 `dir=` — 동시작업 격리). RESOLVE PASS 아니면 제시/‘done’ 금지 · 봉인 루브릭 직접 열람 금지.
- **Layout.** Each VQ round = a **topic divider tab** (named, otherwise empty — only the *name* matters) immediately followed by its **response sheet**. A multi-round tracker thus reads `[divider1][response1][divider2][response2]…`. The response sheet (`VQ 답변 모음`, `(2)`, …):
  - A **questions reference table** at the top, NOT shifted: column B = VQ No., column C = full question text.
  - Below it, separated by **exactly one blank row**, a **transposed response block** shifted one column right: field names run down **column C**, and each expert is a wide column from **column D** onward.
  - Column A is a narrow empty left margin.
- Field-row order down column C: 전문가 네트워크 이름 · 전문가 이름 · 주요 경력 · [one row per question, short label] · 가능시간 · 제한 주제.
- **No wrap on the question text** (top table) — questions display on a single line and overflow into the empty column to the right; do not enable wrap there. Answer cells DO wrap (they are long).
- Column widths: A ~2.5 (margin); B ~8 (VQ No.); C ~22 (label column); every expert column (D onward) ~90. The build script sets these — do not run a LibreOffice/recalc pass afterward, as it can reset widths (VQ numbers are literal integers so no recalc is needed).
- **Freeze panes — computed, NEVER hardcoded.** The freeze split is always the body's first data cell: `{first expert column}{body row}` (e.g. `D8` for 4 questions, `D7` for 3, `D10` for 6). This freezes the questions table + the single gap row (rows) AND the label column C (column), so both the questions and the field labels stay visible while scrolling answers down and experts right. The body row is derived from the detected last question row (`last_q_row + 2`), so it shifts automatically when the question count changes. The build script handles this — never write a fixed cell like `D8`.
- Every answer cell carries the original answer followed by a Korean translation (see "Step 3 — Bilingual answers"). Korean-origin answers are left untranslated.
- Keep `background` to the one-line role, not the full CV table. Do not translate the background.
- Never invent an answer, availability, or restriction that isn't in the email. Empty is fine.

---

## Example

**User:** "thirdbridge에서 온 VQ 답변들 엑셀로 정리해줘"

1. `outlook_email_search(query="thirdbridge VQ")` → find the thread; `read_resource` the latest message.
2. Parse the original firm request for the 4 VQ questions; parse each expert block (`#11.3 전문가 1` in `[Response:]` form, `#10.2 전문가 4` in narrative form).
3. Translate each English answer to Korean; compose each answer cell as English original + blank line + Korean translation.
4. Build the spec (project_label `0615(해외 증권사 C, 해외 은행 D) >>`, 4 questions, 2 experts) and run the script.
5. Present `전문가_VQ_모음_Web3.xlsx` with one row per expert.

---

## Edge cases

- **Multiple experts, mixed formats** in one thread → produce one row each, handling Format A and B per expert.
- **Expert proposed but not yet answered** (profile/CV only, "has not yet provided availability") → skip, unless the user wants a placeholder row.
- **Availability only, no answers yet** → if the user wants the row anyway, fill availability and leave answers "".
- **Questions differ from the existing tracker** → the script automatically puts the new experts on a new response sheet (`VQ 답변 모음 (2)`, …) instead of forcing them into mismatched columns. Tell the user a new sheet was created. If the questions are the same set with only minor wording changes, it appends to the existing sheet.
- **No expert-network connector / Outlook access fails** → tell the user it looks like a Microsoft 365 connector permissions issue and ask them to reconnect, or to paste the email content directly.
- **Several candidate emails** → prefer the most recent message in the thread (it usually contains the full forwarded history); confirm with the user if ambiguous.
