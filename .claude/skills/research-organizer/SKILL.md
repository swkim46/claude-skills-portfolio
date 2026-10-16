---
name: research-organizer
description: Use this skill whenever the user wants to turn already-prepared research/notes/findings into a clean, consulting-house-style consulting Excel deliverable. Triggers include Korean phrases like '자료 정리', '리서치 정리', '조사 내용 엑셀로 정리', '과제 정리해줘', '컨설팅 펌 양식', 'pyramid 구조로 정리', '보고서 양식 엑셀'. The point of this skill is NOT analysis quality — it is putting already-prepared content into a good-looking, structured .xlsx with a 3-sheet layout (Intro → Summary → RAW Data) and consulting house-style formatting (column-based hierarchy, restrained color use, minimal-line tables, source tags on the right). Apply this for both creating a new deliverable from scratch and editing/extending an existing organized file. Do NOT use this skill when the user is asking for analysis quality, source verification, or a consulting-grade client deliverable — for those, use `research-docx` instead. This skill assumes the research is already done.
---

# Research Organizer

> ### ▶ 단계 게이트 (빠른 바닥은 인라인, 빡빡한 가이드 심판은 비동기 gate-audit)
> hook 차단 환경의 강제 레버. 헬퍼 `SG` = `python3 <REPO>/.claude/skills/_stepgate/stepgate.py`.
> **★ 동시 작업 격리** — 이 워크플로의 *모든* SG 호출(form·judge·resolve)에 **같은 `dir=<과제폴더 절대경로>`**를 붙인다(다른 과제=다른 폴더→자동 격리, 같은 폴더 다중 산출물은 `deliverable=`로 구분). 누락 시 form↔judge 키가 갈려 오작동.
> **① 빌드 직후(인라인 바닥 ~5s)**: `SG form research-organizer publish dir=<과제폴더>` → 산출물 열고 **정직히** 답(합격 기준 비공개·**표현이 회차마다 뒤집히니 지난 답을 재사용하지 말고 이번 문구를 그대로 읽고 답할 것**) → `SG judge research-organizer publish <id>=<값> … deliverable=<xlsx 절대경로> dir=<과제폴더>`. **GATE PASS**면 바닥 통과. PASS 직후 출력된 **FIRE 블록으로 백그라운드 감사 에이전트(general-purpose)를 발사(run_in_background)하고 즉시 다음 작업으로** — 대기 금지.
> **② 사용자 보고 직전(해소 게이트)**: `SG resolve research-organizer deliverable=<xlsx 절대경로> dir=<과제폴더>` → **RESOLVE PASS가 아니면 'done' 보고 금지**. HOLD=감사 미완(지금 동기 드레인) · FAIL=새 vN_M로 수정 후 `judge … async` 재실행(재감사).
> 흐름: **publish 바닥** → 비동기 lens 감사(audit_rubrics) → **resolve 사인오프**. 봉인 루브릭(rubrics/) 직접 열람 금지(열람 시 무효·감사됨). 상세: `skill-maintainer` 〈단계 게이트 프로토콜〉.
> `<REPO>` = 이 저장소를 클론한 **절대경로**(문서상 표기). `dir=`·`deliverable=`은 절대경로여야 하므로 호출할 때 실제 경로로 치환한다.

A formatting skill: turn already-researched content into a consulting house-style Excel workbook.

**Important boundary:** This skill does NOT do research, source verification, or analysis. It ONLY formats already-prepared content. If the user wants source verification or consulting-grade output, use the `research-docx` skill instead.

**⚠️ 검증 가드 (수치 무단 게재 금지 — 절대 건너뛰지 말 것).** "already-prepared content"는 **출처 검증을 통과한** 내용만을 뜻한다. 입력의 수치·통계·인용이 **WebSearch 요약 또는 deep-research 초안에서 왔고 원문 대조를 안 거쳤다면, 그대로 클라이언트 산출물(xlsx)에 빌드하지 말 것.** WebSearch 출력은 원문이 아니라 LLM 요약이라 **어느 페이지에도 verbatim으로 없는 숫자**를 만들어낸다(목표↔실측, 기간·연도 혼용 등). 빌드 전 필수: (a) 데이터형 수치(GDP·환율·통계)는 권위 API(WDI/WGI/공식통계)로 일괄 재대조, (b) 각 출처 태그를 '그 출처가 그 값을 말하는가'로 건건 확인, (c) 미검증이면 `research-verified` 3단계 또는 `출처정합성_검토_워크플로우_가이드.md`를 먼저 돌린다. "리서치는 끝났으니 형식만"이라고 단정해 검증을 건너뛰는 것이 가장 흔한 실패다.

**★★ 수정·재빌드 시 필수 게이트 (STOP — 편집할 때마다 매번 적용).** 기존 산출물을 *수정/재빌드*할 때 같은 실수가 가장 자주 재발한다(워딩·출처귀속·단위). 본 스킬로 빌드하든 아니든, **편집 후 사용자에게 보여주기 직전 아래 4항목을 1회 스윕**하고 통과해야만 제시한다(통과 전 제시 금지 → 고치고 재빌드). cf. 메모리 `research-organizer-predelivery-gate`·`consulting-register-always`·`verify-claims-before-publishing`.
1. **워딩** — 표 헤더·제목·본문 전부 **명사형 컨설팅체**. 구어·1인칭·Q&A·**의문형 소제목**·비유/은어/외래약어 0; **버전·내부메타·문서밖 맥락**(정정·이전 주장·'요청서' 참조) 비노출 → 첫 독자가 그것만 보고 읽히게(standalone). (상세 §C·`references/wording-tone.md`)
2. **출처 귀속** — 각 셀 출처가 *그 내용을 실제로 말하는가* 건건 확인. 점값 출처 ≠ range/밴드 귀속; 다출처 종합·판단·추정은 `[분석]` + **도출근거 출처 동반(무근거 분석은 삭제)**; 계산값은 '자체 산출' 표기. (상세 §D·[[analysis-must-be-reference-grounded]])
3. **단위·자족성** — 값은 단위까지("$10T·5bp·20%"; raw "10000" 금지)·표시 산식은 산술·차원 정합("$5T×38bp=$19B"이지 "=38bp" 아님). 같은 단위라도 다른 지표는 각 라벨(라벨 없는 "15%·62%" 병치 금지)·약식 `A→B`는 관계어 명시 — 셀만 떼어 봐도 '무엇이 얼마' 읽힘. (상세 §B-3·3-bis)
4. **§G 자가점검** — 레이어/색/필드분해/하이퍼링크/스토리라인 리드 체크리스트 통과.
5. **초안 대비 변경 전수 문서화** — PPT/초안과 다른 모든 셀(산식·수치)은 비교 섹션에 *근거와 함께* 명시. 조용히 바꾸지 말 것.
6. **미래시점(2030 등) 산출 punt 금지** — "현재 부재/초기/nascent"로 회피 말고, 그 시점 성숙 가정 + **proxy(크립토 등)·comparable로 구성식** 제시(rate proxy는 기존 리서치로 ground, forward share/turnover는 `[분석]`).
> 빌드 스크립트 grep 스윕 권장: `우리|진짜|왜 더|왜 |의미 \(|무엇을|무슨 ?일|무슨 ?말|어떻게|어떡|했나|하나\?|인가\?|되나|정정|요청서|V4|PPT|★|되는 시장|부재|초기 단계|nascent|마중물|빡|조이|서빙|굴리|되사고|크로스보더 핏` 0건 확인 후 발행(★=편집·교정 마커; **무엇을·무슨 일·무슨 말·어떻게·왜·~했나/하나?/인가?/되나 = 의문형·구어 라벨 — *제목뿐 아니라 셀 머리말*('무슨 일 — …' 류)도 명사구로**; 정정·요청서=문서밖 맥락 참조; 뒤쪽=비유·구어). **+ 빌드 후 신규/변경 수치·단정 셀에 출처 하이퍼링크가 붙었는지 `cell.hyperlink` 스캔**(링크 0이면 출처 누락 → 부착).

**★ 검토 반복 루프 중 작업 순서 (속도 — 실세션 교정).** 내용을 두고 *사용자 검토를 빠르게 주고받는 반복 루프*에서는 매 반복마다 **검토 가능한 정본 산출물(클라이언트 xlsx)부터 빠르게 빌드해 피드백을 먼저** 받는다. **부수 정리(작업마스터 .md 동기화·버전 이관·CHANGELOG·지식지도 갱신)는 *콘텐츠가 수락된 뒤 한 번에* 처리** — 중간 교정본마다 housekeeping을 반복하면 시간만 버린다(사용자: "쓸데없는 동기화 말고 빌드부터"). 즉 *내용 합의 = 매 반복 / housekeeping = 수락 후 1회 배치*. cf. 메모리 [[build-routine-changelog-only]]·[[deliverable-versioning]].

**One skill call = one output.** If the user invokes this skill, build `research_<task>.xlsx` only — do NOT also build a `consulting_<task>.xlsx` alongside. If they want a consulting-grade version too, they'll invoke `/research-docx` separately. Pre-emptively building both versions creates file clutter and forces the user to track which is canonical.

---

# ▣ 0. 기본 원칙 · 시작 전 확인

## Three core principles

1. **Pyramid principle** — top-level conclusion first, supporting evidence below, source tag on the right.
2. **Column = hierarchy** — deeper levels shift one column to the right. Do NOT use cell indent property; use column position only.
3. **Storyline lead (서사 척추) ★** — 산출물은 위에서 아래로 그대로 읽으면 **사람이 구두로 하는 보고**가 되어야 한다: `핵심 메시지 → 핵심 수치 → 인사이트 흐름(주장+근거) → 종합 결론`. **리트머스 테스트: 사용자가 이 산출물의 핵심을 전달하려고 텍스트로 다시 풀어써야 한다면 실패다 — 산출물 자체가 이미 그 서사여야 한다.** 분석 섹션 표만 나열하지 말 것. 상세 규칙은 아래 "스토리라인 리드 (8차)".

> Technical mechanics (openpyxl, formula recalc, etc.) come from the `xlsx` skill. This skill governs *what content goes in what structure*.

---

## Before starting

Confirm these before doing any work. If the user already answered in their message, skip; do not re-ask.

- **What is the task/topic?**
- **Is there source material?** (uploaded files, pasted text, link list). If yes, read it first.
- **New file vs editing existing?** If editing, follow the existing file's font/column/color conventions (xlsx skill rule: "preserve existing templates"). Do NOT impose this skill's defaults onto a file with established patterns.
- **How should sources be cited?** (link, document name, "source not yet identified")

If source material is incomplete or sources are missing, **do not fabricate**. Leave placeholders like `[출처 확인 필요]` and tell the user.

---

---

# ▣ A. 문서 내용 구성 (구조·스토리라인·내용 규율)

## Output: 3-sheet structure

| Sheet | Role | Contents |
|-------|------|----------|
| `Intro` | Task metadata | Task name, date, editor, task description, approach (step-by-step ToC) |
| `Summary` | Organized conclusions | Pyramid structure of findings. Each claim gets source tag on the right. Tables, intermediate conclusions, implication |
| `RAW Data` | Source pool | Sources collected per section (links, document names). Summary's `<출처>` / `<Source N>` tags point here |

For very simple tasks: Intro can be omitted, Summary-only is allowed. Default is 3 sheets.

---

### 7) Intermediate conclusions

After multi-item analysis, add a `<중간 결론>` or `Implication` block summarizing "so what" in 1-2 lines. This is the pyramid principle's payoff — without it, the document is just a data dump.

**★ 7-bis) 비교·해석 의무 — 기호·팩트 나열로 끝내지 말 것.** 비교(다국가·다모델·다항목) 산출물은 ●/◐ 같은 기호나 사실만 늘어놓으면 독자가 *'그래서 무슨 의미·왜 이 결론'*인지 모른다 → **각 항목의 의미·시사점(so-what)과 *항목 간 비교 해석*을 같은 표의 '해석'열(또는 인접 비교표)로 함께** 적어, *그 산출물만 읽어도*(외부 지식·작성자 설명 없이) 결론이 읽히게 한다. 예: 규제 비교표에 `열린 모델 / 조건부 모델 / 규제 전개 논리(어떻게·왜 열고 닫았나) / 출처` 열을 두어 '왜 A는 1위·B는 3위인가'가 표 안에서 설명되게. **리트머스: 표에서 기호·숫자를 가렸을 때도 '무엇을·왜'가 글로 읽히는가** — 안 읽히면 데이터 덤프(실패).

**★ 7-ter) 비교 축은 '유형'으로 묶고, 모든 행·라벨은 *차이*를 드러내라.** ① **원자료 평면 나열 금지** — 비교 축(고객·투입·채널 등)을 'A·B·C·D…' 나열로 두면 대상끼리 맞비교가 안 된다 → **소수 유형 택사노미로 추상화**(예 고객 → 기관/Wealth/거래소참가자)해 *같은 기준으로* 줄세운다(기능 매트릭스가 사업을 OTC·MM 등으로 추상화한 것과 같은 원리). ② **변별 없는 행·라벨 삭제** — '셋 다 함(정도 차이)'처럼 차이를 안 드러내는 행, 거의 모든 칸에 같은 값이 붙는 라벨(예 9칸 중 6칸 '핵심')은 정보가 0 → *삭제하거나 분류축을 다시 잡아* 변별이 생기게 한다(라벨이 거의 다 같으면 그 축은 죽은 것). *실사례: 고객 비교의 '리테일에도 도달(정도차이)' 무변별 행 → '확보 방식: 관계형 > 비관계형'으로 교체; 9칸 중 6칸 '핵심' 라벨 제거.*

**★ 7-quater) 문장·논리 전개 — 엑셀 셀·열로 '한 번에 이해되게' (docx/md 서술 규율의 엑셀판).** 문장을 잘 못 써 '무슨 뜻인지' 안 읽히는 실패를 셀 구조로 막는다:
- **리드 셀 먼저(리드 앵커)** — 각 블록·행 묶음은 *결론/주제를 말하는 리드 셀*(상위 레이어 열 라벨·`key_line`·`section_label`)로 시작하고 근거 행은 그 아래·오른쪽으로(컬럼=위계 §B-1과 결합). 표·수치부터 던지지 말 것 — 독자가 '이게 무슨 표인지'부터 헤맨다.
- **논리를 셀·열로 *드러내라*(점 잇기 금지)** — 'A라서 B, B라서 C'의 근거·so-what을 셀·열로 명시(`해석`열·`Implication` 행·근거 표). 결과·기호만 두고 왜·연결을 독자가 추론하게 두지 말 것(7-bis·3-bis 인수분해와 결합).
- **한 셀 = 한 주장** — 한 셀에 여러 주장을 대시·쉼표로 뭉친 만연체 금지(필드 cram 금지 〈셀 분리〉 A와 별개로 *서술*도 하나씩) → 완결 문장·명사구로 짧게(익썸 산문 (a)).
- **메시지·헤드라인은 양면으로** — `key_line`/`exec_summary` 핵심 메시지 문장은 기회와 제약(강점+한계)을 함께(❌'매력적 시장' → ✅'매력은 있으나 ~로 수익성은 제한적').

**리트머스: 셀에서 기호·숫자를 가려도 리드→근거→so-what이 글로 읽히는가**(7-bis 리트머스와 동일).

### 9) "No arbitrary narrowing" rule

When categorizing items (asset classes, players, regions, etc.), if the original source presents N categories, **show all N**. Do not silently drop "weak" or "uninteresting" ones. Use a fit-score column (강점/중립/약점, 활성/초기/미발달 등) to express priority instead of omission.

Bad: show 4 "strong" asset classes, omit the other 5.
Good: show all 9-13 classes, tag each with fit score.

## 엑셀 형식 피드백 (Intro 라벨 · Exec Summary 배치 · 용어 해설)

실제 산출물 리뷰에서 나온 형식 피드백. 빌드 시 필수 반영.

### Intro 시트 — 라벨 어휘 교정
- **'내용 기준' 행 제거** — 불필요한 메타. 넣지 않음.
- **단독 '시계' 금지** → '분석 기간' 또는 '전망 시계(향후 3~5년)'처럼 무엇의 기간인지 명확히.
- **'접근 방식' → '분석 구조'**(또는 '분석 프레임'). `intro_meta_label()` 라벨 텍스트로 사용.
- **'출처 등급' → '출처 유형'** — R/C/A/N/O는 신뢰 '등급'이 아니라 자료 '유형' 분류이므로 명칭을 유형으로.
- **'작성'(editor) 기본값 = "운영자"** — 사용자(스킬 소유자)가 별도 지정하지 않으면 Intro '작성' 메타값은 **"운영자"**로 둔다. `intro_meta_value(intro, r, "운영자")`.

### Summary 시트 — Executive Summary 분리·배치
- **헤드라인은 D(결론 레이어)에**: Exec Summary의 핵심 결론 문장을 `key_line()`(기본 D)로 둠. E(본문 레이어)에 두면 헤드라인이 본문처럼 보임 — 실제 지적 사항.
- **Exec Summary ≠ 본문 (시각 분리)**: `exec_summary()` 배너 → D 헤드라인(전체 핵심 1~몇 줄만) → `divider()` → 본문 섹션(①~⑥)은 각자 배너로 시작. Exec Summary엔 **핵심만**, 상세 근거·표는 본문에. 둘이 구분 안 되면 안 됨.

```python
r = S.exec_summary(sm, r)
S.key_line(sm, r, "신용/GDP가 낮고 은행 편중이라 인니 금융시장은 규모가 작고 고마진인 구조 …", key=True); r += 2
S.divider(sm, r); r += 1                       # ← 본문과 분리선
S.task_banner(sm, r, "① 거시·산업경제"); r += 1   # 본문 시작
```

### 용어 해설 (필수)
- **★ 용어는 *그 표·셀·문장 안에서* 풀어 설명 — 별도 '용어사전' 한 곳에 모아두고 독자가 알아서 찾아 이해하게 떠넘기지 말 것.** 표 셀의 약어·전문어는 셀 안 괄호로 1차 풀이(예: "DTSP(해외 고객 전용 영업 차단)"·"VATP(가상자산 거래소 면허)"·"CMS(자본시장 라이선스)"). 용어사전(glossary)은 *보조·중복 안전망*일 뿐 본문 self-containment를 대체하지 않으며, 둘 때도 **문서 뒤쪽**에 배치(맨 앞에 두면 본문을 못 읽음).
- 고유명사·전문용어(예: **샤리아·수쿠크·takaful·NIM·hilirisasi·FD Index·Depth/Access/Efficiency/Stability·QRIS**)는 처음 등장 시 **오른쪽 또는 아래에 해설**. 독자가 용어에서 막히지 않게.
  - 1줄 인라인: `S.term_note(sm, r, "샤리아: 이슬람 율법(이자·투기·하람 산업 금지). 펀드는 이에 부합하게 설계", col="F")`
  - 섹션 끝 모음: `S.glossary_block(sm, r, [("수쿠크","이슬람 채권 — 이자 대신 실물자산 수익 배분"), ("NIM","순이자마진")])`
  - 약어는 풀어쓰기(BIES 등 비표준 약어는 그대로 두지 말고 정의 또는 정식 명칭 사용).

---

## 스토리라인 리드 / 서사 척추 (★ 문서 내용 구성의 최상위 규칙)

**원칙:** 분석 섹션 표만 빽빽하게 나열하면 실패다 — 위에서 아래로 읽으면 구두 보고가 되도록 *결론→근거 흐름(스토리라인)*을 한데 모은다(피라미드·Exec Summary가 '한 줄 결론'에 그치지 않고 '왜 그렇게 보는가'의 근거 흐름까지 담게).

**리트머스 테스트 ★★:** *"사용자가 이 산출물의 핵심을 누군가에게 전달하려고 텍스트로 다시 풀어써야 한다면, 그 산출물은 실패다."* 산출물은 **그 자체가 이미 그 서사(narrative)** 여야 한다. 표는 서사를 뒷받침하는 근거이지 서사를 대체하지 않는다.

**★ 입력이 *이미 구조화된 검증 완료 작업마스터*(.md 등)면 그 골격·섹션 순서를 *미러링*하라 — 임의 격자로 *재분해* 금지.** 잘 짜인 검증 산출물을 클라이언트 포맷으로 옮길 땐 그 섹션 구성·순서를 따르고, '핵심수치 표·인사이트 ①②③' 같은 격자로 재구성해 **원본보다 읽기 불편하게** 만들지 않는다(스토리라인 리드는 *익썸 산문*으로 충족 — 아래 〈익썸=문장형 산문〉). **사용자가 섹션 순서를 재지정하면(예 1-3-2) 라벨 번호를 그 순서대로 순차(①②③) 재부여**한다(원 번호를 순서 어긋난 채 두지 말 것). 리트머스: 산출물이 *원본 .md만큼(이상)* 읽히는가 — 더 불편하면 과(過)구조화.

### 필수 — 모든 리서치 산출물엔 '스토리라인 리드'를 둔다
위에서 아래로 그대로 읽으면 구두 보고가 되는 **서사 척추**. 4개 블록 고정:

1. **핵심 메시지 (governing message)** — 전체를 관통하는 단 하나의 'so what' 한 문장. `exec_summary()` 배너 + `key_line(key=True)`(D열). 근거·조건을 한 문장에 욱여넣지 말 것(§L: 단문, 근거는 아래 블록으로).
2. **핵심 수치 (anchor facts)** — 메시지를 떠받치는 3~5개 수치를 작은 표로. 열 = `["수치","의미","출처"]`(출처 셀은 하이퍼링크). 사람이 보고 첫머리에 던지는 숫자들 = 이 표.
3. **인사이트 흐름 (argument chain)** — 각 인사이트 = **주장(섹션 헤더) + 근거(표/불릿)** 한 쌍. 논리 구조가 표에서 드러나게: 예) `필요 조건 → 근거① 실현 사례 → 근거② 추가 요인`. 주장만 있고 근거가 없거나(공허), 표만 있고 주장(so what)이 없으면(데이터 덤프) 둘 다 실패. **★ 주장(인사이트)은 *왜 그렇게 됐는가(근본 원인·메커니즘)*까지 — 현상 재진술('X 부담이 없다'·'A라서 A')·동어반복(1+1=2 류)은 인사이트가 아님("한 단계 더 *왜?*"를 자문해 근본 원인까지).**
4. **종합 결론 (`<종합 결론>`)** — 인사이트들을 하나로 묶는 마무리 1~2줄(명사형).

**★ 리드 문장 위생 — *결론·판단·핵심 수치·so-what*만.** 핵심 메시지·종합 결론 등 리드/익썸 문장에 *용어·개념 정의·교차참조('§X/타 시트 참조')·검증/절차 캐비엇·동어반복* 금지(정의·과정·근거는 상세 섹션으로). **리트머스: 리드 각 문장이 '결론/판단/핵심 수치'인가** — 아니면 빼라.

### 배치
- **단일 과제(시트 1개)**: `Summary` 시트 **최상단 리드 블록**(Exec Summary → 핵심수치 표 → 인사이트 흐름 → 종합결론). 상세 근거 섹션은 그 아래.
- **다(多)시트·다모델 파일**: `Intro` 바로 다음에 **전용 '스토리라인' 리드 시트**를 둔다. 개별 딥다이브 시트(#1·#3…)는 그 **뒤에 근거 자료로** 배치 → 리드 시트만 읽어도 보고가 끝나게.
- 개별 딥다이브 시트도 **각자 상단에 자기 스토리라인**(핵심메시지 1줄 + 핵심수치 표)을 갖고, 상세 표는 그 아래로.

### 빌드 패턴 (헬퍼만 사용 — 레이아웃 하드코딩 금지)
```python
r = S.exec_summary(st, r)
S.key_line(st, r, "<전체 so what 한 문장>", key=True); r += 2
S.divider(st, r); r += 1
S.section_label(st, r, "1. 핵심 수치"); r += 1
r = S.make_table(st, r, ["수치","의미","출처"], rows)   # 출처 셀 하이퍼링크. ★하이라이트는 *decisive 행이 있을 때만* highlight_rows={n}(보통 생략). 핵심수치 표라고 기계적으로 첫 행을 칠하지 말 것 — color-policy: 표당 1-2행·그것도 '구두 발표 때 가리킬 단 한 행'만, 강점/약점 구분은 행 색이 아니라 *컬럼*으로
S.section_label(st, r, "2. 인사이트 — <주장1>"); r += 1                      # 주장 헤더
r = S.make_table(st, r, [...], 근거rows)                                    # + 근거 표
S.section_label(st, r, "<종합 결론>"); r += 1
S.support(st, r, "<묶는 결론>", col="E")   # 종합 결론은 section_label(C) 아래 본문 → support 기본열 E (C→E 계단). F로 두면 sub-subsection처럼 보임
```

### 안티패턴 (이번에 실제로 나온 것)
- ❌ 개념·수치구조·측정함정·출처별·TradFi분모 같은 **분석 섹션 표만 7개 나열** — 독자가 스스로 스토리를 재구성해야 함.
- ❌ Exec Summary에 결론 한 줄만 있고, **근거 흐름(왜 그렇게 보는가)**이 본문 어디에도 한데 모여 있지 않음.
- ✅ 리드 스토리라인에서 **메시지→수치→근거흐름→결론**이 한 흐름으로 끝나고, 상세 분석 표는 그 뒤 '근거'로.

(같은 원칙이 `research-docx` 산출물에도 적용 — 리드 스토리라인이 먼저, 상세 분석은 뒤.)

### 익썸(Executive Summary)은 *문장형 산문*이다 — 분해된 아웃라인이 아니다 ★ (반복 교정 일반화)
**배경:** 위 스토리라인 리드를 *구조*로만 받아들여 익썸을 **섹션 라벨 + 단일토큰 불릿 + 표**로 잘게 분해하면, "형식"은 갖췄으나 *읽히는 요약*이 아닌 산출이 나온다(실세션 반복 교정: "이렇게 쓰면 그게 익썸이냐"). 익썸은 **위에서 아래로 읽으면 그대로 구두 보고가 되는 *완결 문장의 산문***이다(핵심수치·근거 표는 산문을 *뒷받침하는 전시물*로 둘 수 있으나, 익썸의 척추는 문장이어야 한다).

- **(a) 완결 문장형.** 각 포인트는 *완결 문장*(주어+서술). 대시·슬래시로 토막 낸 전보식 파편(`"X: A·B·C / Y: D"`)·단일토큰 불릿 금지. 문장마다 줄바꿈은 좋으나 *문장*이어야 한다.
- **(b) 핵심만, 검증은 본문.** 익썸엔 *핵심 인사이트*만 담고, 각 인사이트는 *아래 본문/상세를 보면 확인·이해되도록* 가리킨다(§N 참조). 상세 표·딥다이브를 익썸 안에 복제하지 말 것 — 익썸=결론, 본문=근거.
- **(c) '요약' 요청엔 요약만.** 익썸을 요청받았는데 그 아래 *요청하지 않은 상세 표·섹션을 무더기로 덧붙이지 말 것*(핵심이 묻힌다). 상세는 기존 본문 섹션이 담당.
- **(d) 순차 논리.** 정해진 스토리라인 순서(예: 현황 → 원인·이유 → 결론·권고)대로 *흐르게*. 분해된 격자(grid)로 재구성 금지.
- **(e) 결론은 *결정*한다 — 우산 용어로 회피 금지.** 분석이 한 선택지를 지지하면 *명확히 그것으로 결론*(A vs B 중 택일)하고, 실제 후보가 둘인데 둘을 뭉뚱그리는 모호한 상위 개념(포괄어)으로 판단을 비키지 말 것. 단서·대안은 결론 *뒤에* 짧게.
- **(f) 용어는 직관적이되 프로페셔널.** 비전문가도 이해할 쉬운 말 — 영어 전문용어·약어(jargon/loanword) 남발 금지, 단 아마추어 표현도 금지. **대상 독자의 업(業) 표준 어휘**가 가장 적확(독자가 매일 쓰는 분류·용어로 번역; 불가피한 전문어는 1회 평이하게 풀이).

**빌드:** 익썸 산문 = `key_line`(so-what, key=True) + *완결 문장* `support()` 줄들(같은 열, 문장마다 1줄). 섹션라벨+`make_table` 파편으로 쪼개지 말 것; 상세 표·딥다이브는 `divider` 아래 본문 섹션으로.
**리트머스:** 익썸만 소리 내어 읽었을 때 *완결된 구두 보고*로 들리는가(파편·표 낭독이 아니라). 안 들리면 실패.

---

---

# ▣ B. 엑셀 형식 · 레이아웃

## Mandatory formatting rules

### 1) Column-based hierarchy

Each layer of depth shifts one column right. The exact column letters can vary, but the **rule "one layer = one column, deeper = righter"** must hold within a file.

**Critical — columns split into two zones with different roles**:

| Zone | Columns | Role | Width |
|------|---------|------|-------|
| **Layer zone** | A~E | Banners, section labels, numbered headers, one-line body conclusions | NARROW (2~13) |
| **Content zone** | F~M | Table headers, table bodies, single-cell bullets | WIDE (11~47) |

Standard layer-to-column map:

| Layer | Column | Use |
|-------|--------|-----|
| L0 | B | Task / top-level section title (bold, with bottom border) |
| L1 | C | Subsection label (e.g., "Summary"), bold |
| L2 | D | Numbered analysis step "1) ...", "2) ..." (bold) |
| L3 | E | Body explanation sentence (one-liner) |
| Content | F~M | Table headers, table bodies, single-cell bullets |
| Source | K (or L) | Source tag on the header row of each claim |

**Stop the hierarchy at D for numbered sections.** Sub-sections within a numbered section are NOT expressed by deepening into E, F, G — they are expressed as **table-title labels in F** (e.g. `<3-1> Sub-section A`, `<3-2> Sub-section B`) each followed by a fresh table at F.

Result: tables always start at F (the widest content column), KEY_FONT insight lines stay in E (the layer zone's body column where they're readable). When the hierarchy goes deeper than D, table-start columns get pushed rightward and content area gets cramped — this is the most common visual failure mode.

**Bullets are single-cell.** Put `"[1] text..."` as the whole string in one F cell. Do NOT split the marker into F and the text into G. The cell overflows right.

### 1-bis) Critical sub-rule — never indent body LEFT of its parent title

A title placed at column N must be followed by body text at column N or further right (N, N+1, N+2). Body text MUST NOT appear at column N-1 or earlier — that creates inverted indentation where the body looks shallower than its own title.

**The most common violation**: `evidence(col="F")` (sub-section title at F) immediately followed by `support(col="E")` (body explanation at E). The `support()` default column is E, so this happens implicitly whenever an F-column sub-section title is followed by a default support() call. Visually the body text appears one column to the LEFT of its parent title, producing inverted hierarchy.

**Rule**: when an `evidence()` sub-section title sits at column F (the standard convention for `<X-N>` sub-section titles), any `support()` body sentence that explains that sub-section MUST be placed at column F (or further right). Override the default `support()` column with `col="F"` explicitly.

```python
# WRONG — body appears LEFT of its own title
S.evidence(sm, row, "<3-1> Sub-section title", col="F")
row += 1
S.support(sm, row, "Body sentence for that sub-section.", col="E")  # ← inverted

# RIGHT — body stays at or right-of title
S.evidence(sm, row, "<3-1> Sub-section title", col="F")
row += 1
S.support(sm, row, "Body sentence for that sub-section.", col="F")
```

**Self-check**: after build, scan for any `support(col="E")` (or default support) that follows an `evidence(col="F")` within 2-3 rows. Either the support belongs to the parent numbered section (then E is correct) or it belongs to the F sub-section (then it must move to F).

### 2) Color policy — MINIMAL USE

This is the most common mistake. **More color does NOT mean more professional. It causes visual fatigue.** Restraint is the principle.

The 5 sanctioned colors and their narrow roles:

| Color | RGB | Where | When |
|-------|-----|-------|------|
| Task banner | `8ED973` (dark green) | L0 cell fill | Top section banners only |
| Section banner | `B4E5A2` (light green) | L1 cell fill | Major section labels only |
| Table header | `A6CAEC` (light blue) | Table header row only | Every table header — required for readability |
| Highlight row | `D9F2D0` (pale green) | Table body rows | **Per table: max 1-2 rows.** Only the row carrying the decisive insight |
| Key font | `0000FF` (blue text) | Cell font color | **Whole document: max 1-2 cells.** Only the single most critical insight sentence |

**Wrong:** Color every conclusion row blue. Highlight 5 rows in a table. Use 3 different fills in one section.

**Right:** Black text body, one table highlight row, the key insight in blue font — once.

**★ 강점/약점·정합도 구분은 행 색이 아니라 *컬럼*으로** (예: `정합도: 강점/중립/약점` 열). 하이라이트는 그중 *구두 발표 때 가리킬 단 하나의 decisive 행*에만 — '핵심수치 표라서'·'중요해 보여서' 기계적으로 칠하지 말 것(decisive 행이 없으면 0개). 한 표 연속 3행+ 하이라이트는 게이트(`xlsx_highlight`)가 결정론으로 FAIL. (color-policy anti-pattern 2)

For sheet tab colors: `Intro` light green, `Summary` darker green. Use only if needed.

See `references/color-policy.md` for the full rationale and anti-patterns.

### 3) Column format consistency

Within one column of a table, every cell uses the **same unit, same format, same tag position**. Mixing breaks scannability instantly.

**Wrong (one column, real example from prior work):**
- "AUM 50억 달러 [2차]"
- "활성 대출 약 11억 달러 [2차]"
- "23억 달러 [2차]"
- "수억 USD 수준"

**Right:**
- "AUM 4.6B USD"
- "Active loans 1.1B USD"
- "AUM 2.3B USD"
- "수억 USD"

Standard format conventions:
- **Currency**: `X.XM USD` or `X.XB USD` (English unit). Avoid mixing 한글 "억/달러" with English "M/B USD".
- **Investment amount**: `XM USD [등급]` or `자체` or `비공개`. Pick one form per column.
- **AUM/scale**: `AUM X.XB USD` or `활성 대출 X.XB USD`. Whatever the label, keep it consistent.
- **Reliability tags**: place after value, in brackets, English-Korean mixed OK (`[검증·SEC]`, `[2차]`, `[추정]`).

See `references/column-standards.md` for full conventions.

### 3-bis) 수치 자족성 — 압축 ≠ 암호화 ★ (cold reader 가독)

**배경:** "압축/간결화" 요청을 받으면 수치를 암호식으로 축약("세전 15%·EBITDA 62%", "A→B", "밸런스시트 미달")해 *무엇을 나눈 값인지·무슨 관계인지* 알 수 없게 만드는 실패가 잦다. **압축은 군더더기(중복·수식어) 제거이지, 그 수치를 해석 가능하게 하는 라벨·맥락 제거가 아니다.**

- **수치 = 단위 + 지표 정의.** 단위(%·$)만으로 부족 — "무엇을 나눈 비율 / 무엇의 금액인지(지표명)"까지 라벨. 바 숫자 "15%"가 아니라 "세전이익률 15%".
- **같은 단위·다른 지표를 나란히 둘 때 각 라벨(+필요 시 1줄 산식) 필수.** 예: "이익률 15%(이익÷매출)" vs "EBITDA 마진 62%" — 라벨 없이 "15%·62%"로 병치하면 독자가 *서로 다른 이익 라인*임을 모르고 모순으로 오독.
- **약식 기호도 자족적으로.** 화살표 `A→B`·축약어는 관계(인수/지분/투자/공급 등)를 모르는 cold reader 기준으로 관계어를 명시.
- **실측 vs 추정 시각 구분.** 추산 표의 값 자체도 실측=볼드 / proxy·추정=이탤릭(또는 §C 회색)으로(§C 근거셀 검정/회색 규칙의 값셀 확장).
- **★ 산식 결과는 *인수분해* — 수식에 들어가는 모든 인자를 (행으로) 나열 (cold reader가 결과를 재계산할 수 있게).** 어떤 산출값이 *다른 값들의 함수*(예: 결과 = 베이스 × 비중 × 요율, 또는 ③ = f(①,②))면 결과 한 칸만 두지 말고 **베이스·각 곱셈 인자(비중·요율·배수)·중간 소계를 각각 행으로 펼치고**, 각 인자에 측정/추정 라벨·도출 근거·출처를 단다(합성 결과 행은 `= A×B + C×D` 산술로 보이게). 전 항목 공통 인자(공통 요율·배수)는 별도 *인자 표*로 *각 수수료가 왜 그 값인지* 1차 실측 도출을 분리. **리트머스: 표만 보고 그 결과값을 손으로 재계산할 수 있는가** — 못 하면 인자 누락(결과만 던진 블랙박스). *실사례: 권역 수익풀을 "≈$X"로만 두지 말고 거래량·체결요율·배수·수탁요율을 각 행으로 나열·③=거래량×요율+AUM×요율로 분해.* (값 볼드/이탤릭은 위 항목·다중출처는 §D `link_cols`와 결합.)
- **리트머스:** *작성자의 머릿속 맥락 없이* 그 셀만 떼어 봐도 "무엇이 얼마"가 읽히면 통과.

### 3-ter) 반복되는 분류·시점 축은 한 양식으로 통일 + 범례에 정의 ★
- 한 표에서 **반복 등장하는 축**(유형·분류·시점·상태 등)은 *한 칸에서 정한 표기 양식을 표 전체에서 동일*하게 쓴다 — 어떤 칸은 "상시", 다른 칸은 "2020~"처럼 양식이 뒤섞이면 독자가 같은 축인지 모른다(**같은 부류 셀 = 같은 문법**).
- 그 **표기 규칙(라벨 종류·양식)을 범례/머리글에 *정의*로 명시** — 독자가 한 칸을 읽으면 나머지를 같은 틀로 해석하게(예: "각 칸 = [분류]·[시점]; 분류는 A/B/C 중 하나").
- **리트머스:** 한 칸의 표기 양식을 다른 칸에 그대로 대입해 읽히면 통과; 칸마다 형식이 달라 별도 해석이 필요하면 실패.

### 5) Tables

- Thin border on every data cell.
- Header row: light fill (`A6CAEC`) + bold.
- Unit annotation (e.g., `(단위: 백만원)`) goes top-right above the table, right-aligned.
- **Calculated values MUST be formulas**, not Python-computed hardcodes (e.g., `=F29/F28`, not `0.43`). This follows the `xlsx` skill rule.
- **라이브 수식을 유지하면 셀은 숫자 + `number_format`** — bp는 bp단위로 저장하고 수식에서 `/10000`, %는 `0.0%`. raw 정수("10000") 노출 금지(표시는 단위까지).
- **★ 텍스트(비수식) 셀은 `=`로 시작 금지 — Excel이 수식으로 오인해 *제거*.** 도출근거·노트·라벨 등이 `=`로 시작하면(예: "= 1 − A …") Excel이 깨진 수식으로 보고 삭제 → 파일 열 때 *"We found a problem … Removed Records: Formula"* 복구 팝업이 뜬다. (MODEL MODE 합계라벨 `=`→#NAME?도 같은 뿌리.) ⚠️ openpyxl은 그런 문자열을 `data_type='f'`로 분류해 "비수식인데 `=`로 시작" 검사(LEADING_EQ)를 *우회*하므로, 빌드 가드는 **`=`로 시작 + 한글 포함 셀 0건 `assert`**로 별도 점검한다. 셀이 `=`로 시작해야 하면 앞에 서술어를 두거나 `→`·`( )`로 치환.
- Year headers: text strings ("2024"), not numbers (avoids thousand-separator commas).
- Percentages: `0.0%` or `0.00%` format.
- **셀별 출처 링크가 필요한 표는 `make_table(..., link_col=N, links=[url,...])`로 짓는다 ★** (출처코드·DART 접수번호·rcpNo 등 한 열을 클릭 링크로). **손으로 C~E에 표를 짓지 말 것** — `clear_layer_borders(A~E)`가 그 표의 테두리 절반만 벗겨 깨뜨린다('F7~H12 이상한 테두리' 버그). 표는 항상 F+에 두고 link_col로 linkify. (셀별 링크는 `_fill_src_cell`식 다중 링크가 아니라 *열 단위 1셀=1링크*임에 유의.)
- (styles.py 내부) `autofit_columns`는 4면 BOX 또는 하이퍼링크가 있는 셀을 오른쪽이 비어도 must-fit으로 잡는다 → 표의 **마지막 열(출처 링크 열)이 indent_w로 붕괴하던 버그** 해결(과거엔 `overrides={…:17}` 수동 우회 필요했음).

### 6) Section dividers

- Top-level section titles (B column): bold + bottom border.
- Leave one blank row between major analysis steps for breathing room.

### 8) Font

- Korean: **맑은 고딕 11pt** (default).
- English-heavy docs: Arial 11pt acceptable.
- When editing an existing file: use that file's font, not this default.

## 적응형 레이아웃 — autofit/행높이 (매직넘버 금지)

실제 빌드에서 레이아웃이 깨진 원인과 수정. 형식 사고의 대부분이 아래 A에서 비롯됨.

### 0) 대원칙 — 레이아웃은 콘텐츠에서 '계산'한다, 매직넘버를 '박지' 않는다 ★★최중요
고정 열폭(GRID dict)·표별 `col_widths`·고정 행높이는 **한 데이터셋에 맞춰 튜닝한 매직넘버**다 — 텍스트 길이·행 수·열 구성이 바뀌면 무너져 사용자가 모든 열을 손으로 다시 드래그하게 된다. 박지 말 것.

**해결: 빌드 마지막에 콘텐츠/오버플로우 상황으로 전 열 폭을 산정하는 적응형 패스를 돌린다.**
```python
# 모든 셀을 다 채운 뒤, 시트별로 (Intro 제외) 마지막에:
S.autofit_columns(sm)        # A열부터 전 열을 상황 기반 산정. 매직넘버 GRID·set_layer_widths(extra) 모두 대체
S.finalize_row_heights(sm)   # 실제 열폭을 읽어 wrap 줄 수 추정 → 행높이
```
- **레이어 열도 하드코딩하지 않는다.** `autofit_columns`는 각 셀이 자기 열에 **반드시 맞아야 하는지(must-fit)** vs **오른쪽 빈 칸으로 흘릴 수 있는지(overflow)** 를 판정한다. must-fit = wrap=True(표 본문) OR 표 헤더(TBL_HEADER) OR **바로 오른쪽 칸이 비어있지 않음**(잘리므로). overflow = wrap=False인데 오른쪽이 빔(섹션 라벨·`1) …`·`<중간 결론>`·`support()`·`definition_list` 정의).
- 열 폭 규칙: **must-fit 셀이 있으면** 그 셀들로 산정(짧은 토큰 ≤16은 최댓값 꽉, 긴 산문은 percentile 0.78 → 일부만 wrap), **must-fit 셀이 없는 순수 오버플로우 열(레이어/인덴트 zone B·C·D·E, 좌측 여백 A)은 얇은 인덴트(`indent_w`≈3.2)** 로 자동 축소 → C/D/E가 Excel 기본(~8.43)으로 넓어지는 문제 없음. **더 이상 `set_layer_widths`로 레이어 폭을 박지 않는다**(autofit 미사용 빠른 경로일 때만 보조).
- 여러 표가 같은 열(F~K)을 공유해도 **풀링해서 최댓값 만족** → 표별 `col_widths` 충돌 원천 제거.
- **'매직넘버 금지'는 행(row) 앵커에도 적용 — 라이브 수식이 절대주소로 참조하는 하류 표의 시작행을 고정 상수로 박지 말 것 ★ (밸류풀 세션 충돌 버그).** §1 보드가 §2 인자표 셀을 `=H{n}`처럼 **forward-reference(상류가 하류를 절대주소 참조)**하는 구조에서 하류 표 시작행을 `ASM_HDR=17`식 상수로 박으면, 상류 `support()` 노트가 늘 때 두 섹션이 **행이 겹친다**. → 하류 앵커를 상류 콘텐츠로부터 **동적 산출**(`ASM_HDR = bd_hdr + (보드행수+노트수+갭)`; 노트 수 바뀌면 함께 갱신) + 빌드 말미 **`assert 상류_끝행 <= 하류_시작-1`**로 충돌 0 검증.
- 특정 열만 핀해야 하면(예: RAW Data 소스 라인 열) `autofit_columns(raw, overrides={"B":26,"C":120})`. 결과가 어색하면 매직넘버로 되돌리지 말고 `overrides`·`max_w`·`pct`로 조절.

### A. (구) 표별 col_widths 금지 — 이제 autofit으로 자동 해결
- 배경: Excel 열 너비는 **열 단위 전역값**이라, 표마다 `make_table(col_widths=...)`를 다르게 주면 마지막 표가 앞 표·배너를 덮어써 깨졌다.
- **현 규칙**: `make_table`에 `col_widths`를 주지 말고(단일 고립 표 예외), 위 `autofit_columns(sm)` 한 번으로 전 표를 정렬한다. 더 이상 `set_layer_widths(extra={고정 그리드})`로 콘텐츠 열을 박지 않는다 — `set_layer_widths`는 레이어 열(A·B, 선택적 C~E)만 담당.
- **표는 2열(라벨 F + 내용 G) 우선**: 모양이 다른 3·4열 표가 같은 열을 공유하면 한쪽이 어색해질 수 있다. 단순 정의·비교는 `["지표","값"]`·`definition_list()`로 2열 통일. 단 autofit이 공유 열을 풀링해 맞추므로, 과거만큼 치명적이지 않다.

### A-2. 정의·용어 목록 = 2열 표, 단일 셀 불릿 금지 ★
- 용어 해설을 `■ 용어 — 정의`처럼 **한 셀에 욱여넣지 말 것**(정렬 깨지고 셀이 과도하게 길어짐 — 실제 지적). **`definition_list(sm, r, [("ATS","Alternative Trading System — …"), …])`** 로 용어(F)·정의(G) 2열 정렬한다(정의는 wrap=False로 오른쪽 흘림, 일반 11pt). 테두리·헤더가 필요하면 `make_table(headers=["용어","정의"], …)`.

### A-3. openpyxl 함정 — 열폭을 '읽기'만 해도 13으로 박힘 ★
- `ws.column_dimensions["C"].width`를 **접근(read)** 하는 순간 openpyxl이 그 열에 `width=13 customWidth` dim을 생성해, 저장 시 C열이 13으로 고정된다. 미설정으로 둘 레이어 열을 **빌드 중 인덱싱하지 말 것**. (`finalize_row_heights`는 `.items()`만 순회해 안전.) 레이어 열 폭은 `set_layer_widths`로만 설정.

### A-4. openpyxl 함정 — 셀 *부분* 서식(리치텍스트) 금지 ★
- 한 셀에서 *일부 글자만* 볼드·색(예: 라벨만 굵게)은 openpyxl `CellRichText`/`TextBlock`로만 가능한데, 이게 **Excel '콘텐츠 복구' 경고(파일 손상) + 셀 내 줄바꿈(`\n`) 소실**을 유발한다(openpyxl 3.1.5 확인). **셀 부분 서식을 쓰지 말 것.** 강조는 *일반 텍스트*로 — 라벨을 `【 】`로 감싸거나 빈 줄(`\n\n`)·기호(●/▸)로 구분한다. **셀 내 줄바꿈(다단 셀)과 부분 서식은 양립 불가 → 줄바꿈을 우선**한다. (셀 *전체* 볼드는 `make_table`·`key_line` 등 헬퍼로 OK — *부분*만 금지.)

### B. 시트 위계 — 큰 제목 B / Executive Summary·섹션 C
- 콘텐츠 시트 최상단에 **큰 제목(`sheet_title`, B열, size 16)**. 이게 없으면 아래 배너들이 제목 없이 떠 보임("큰 제목이 사라졌다").
- **Executive Summary와 섹션 타이틀(①②③…)은 모두 C열(`section_label`)로 통일** — 큰 제목(B)이 한 칸 들여 이들을 감싸는 구조. Exec Summary만 C, 섹션은 B처럼 열이 어긋나면 안 됨.
- 위계: **B 큰 제목 > C Exec Summary·섹션 > D 단계(1)2)3), key_line) > E 본문(support)**.

### C. term_note는 wrap=False
- 용어 해설을 좁은 열에서 `wrap=True`로 두면 행 높이가 폭발하고 셀이 깨짐. `term_note` 기본은 `wrap=False`(오른쪽 빈 셀로 한 줄 흘림). 같은 행에 출처 태그(K)가 있으면 해설을 짧게 유지해 K 앞에서 잘리지 않게.

### D. 레이어 열(A~E)에 테두리 금지 ★ (B열 '부유 border' 반복 지적)
- `sheet_title`·`task_banner`·`divider()`는 좁은 레이어 열에 **단일 셀 bottom border**를 남겨, 떠 있는 짧은 밑줄로 보임. 표는 F열부터이므로 **A~E에는 테두리가 없어야 함**.
- 섹션 구분은 `divider()`(테두리) 대신 **빈 행**으로. 빌드 끝에 **`clear_layer_borders(sm)`**를 호출해 A~E의 모든 잔여 테두리를 제거(autofit·finalize 전후 무관, save 직전).

### F. 배너/제목 컬러는 Z열까지 ★
- `task_banner`·`section_label`·`sheet_title`의 `span_to` 기본값 = **"Z"**(K 아님). 제목 행 컬러를 여유있게 채워 빈약해 보이지 않게.
- **오버플로우도 Z에서 끝나게 — 빈 끝열 패딩 ★**: `autofit_columns`가 콘텐츠 끝열 이후~`pad_to`(기본 **"Z"**)까지의 빈 열을 `pad_w`(기본 **11**)로 넓힌다. 레이어 존(좁은 B~E)에서 오른쪽으로 흐르는 긴 `support()`·`<중간 결론>` 문장이 **화면 밖(Z 너머)으로 새지 않고 A→Z 안에서 끝나도록**. 배너 fill도 Z까지이므로 fill·텍스트 정렬이 맞음. 끄려면 `autofit_columns(pad_to=None)`. **단, 한 문장이 너무 길어 여전히 넘치면 패딩에 기대지 말고 row를 적당히 끊는다**(주체·자산군 등 메타 줄은 2~3 row로 분할).

### G. 한 문장 = 한 row, 단 '동일 열거'는 한 셀 ★
- 가독성을 위해 본문은 한 문장 한 row가 기본. **그러나 같은 열거·리스트(예: 샤리아 핵심원칙+금지항목, 한 개념의 구성요소 나열)는 쪼개지 말고 한 셀에 묶는다** — 의미 단위가 끊기면 오히려 가독성이 나빠짐.

### H. 표 헤더의 연도는 괄호 표기 ★
- 표 헤더에서 연도는 **괄호로**: `1인당 GDP (2014)`, `매출 (2024)` 식. 깔끔하고 라벨/연도 구분이 명확.

### I. autofit 여유 — 헤더에 '딱 맞게' 좁히지 말 것 ★
- `autofit_columns(padding=...)` 기본을 **2.6**으로 상향(1.6→2.6). 열이 표 제목 폭에 딱 맞아 답답해 보이던 문제 완화. **하드코딩(고정 col_widths)으로 되돌리지 말고** padding·max_w·overrides로 조절.

### J. make_table `col_wrap=False`는 '짧은 라벨 열'에만 ★ (긴 셀 잘림 — 실피드백)
- `col_wrap=False`는 그 열을 wrap 안 함 → **표 안에선 옆 칸이 항상 차 있으므로**, 그 셀이 길면 오른쪽에서 **잘림**(3-2·3-3 표 첫 열이 안 보이던 원인). 표 본문 셀은 **기본 `wrap=True`로 두어** `finalize_row_heights`가 행높이를 늘리게 한다.
- `col_wrap=False`(또는 `wrap=False`)는 **코드·연차·짧은 라벨(≤ 약 8자, 한 줄 확정)** 열에만. 길어질 수 있는 라벨/설명/매핑 열엔 쓰지 말 것. 의심되면 그냥 기본 wrap.
- (참고: 출처 잘림은 §E — 출처 태그는 `key_line`/`section_label` **헤더 행에만**, 긴 `support` 행 K열엔 금지. 두 규칙이 셀 잘림의 양대 원인.)

---

## 셀 분리(필드 분해) + 행높이 = Excel 네이티브 AutoFit

**원칙:** 수치·연도·출처를 한 컬럼에 몰지 말고 필드별 셀로 나눠 가독성을 확보하고, 행높이는 Excel 네이티브 AutoFit에 맡긴다(아래 A·C).

### A. 한 셀에 여러 필드 cram 금지 — 필드별 컬럼 분리 ★★
표에서 **수치·연도·출처는 각각 별도 컬럼/셀**로 둔다. 한 셀에 `"$92.3B (2025) / 토큰화펀드 $600B (2030) [실적+전망]"`처럼 여러 수치·연도·구분을 몰아넣지 말 것 — 셀이 길어져 줄바꿈·클립·스캔 불가.
- ❌ `현재규모 = "Coinbase AUC $300B (2025) / 커스터디 시장 $803B (2025)"`
- ✅ 컬럼 분해: `수치=$803B | 연도=2025 | 출처=Grand View`(서로 다른 수치는 행 분리 또는 별도 컬럼)
- 현재/미래 규모 표는 최소 `모델 | 현재수치 | 현재연도 | 현재출처 | 미래수치 | 미래연도 | 미래출처 | CAGR`처럼 **필드를 펼친다**(넓어도 OK, 스캔이 우선).

### C. 행높이 = Excel 네이티브 AutoFit (하드코딩 금지) ★
**행높이를 파이썬으로 추정해 박지 말 것.** openpyxl엔 렌더 엔진이 없어 추정치는 텍스트가 바뀌면 깨지는 '하드코딩'. 대신 **행높이를 미설정(customHeight 미기록)으로 두면 Excel이 파일 열 때 'AutoFit Row Height'를 자동 적용**한다. `finalize_row_heights(ws)` 기본 `mode="native"` = 텍스트 행 높이를 쓰지 않음(이미지 핀 행만 보존). **표 본문 셀 wrap_text=True 유지**(make_table 기본)해야 Excel이 열 때 자동 맞춤. 빌드 끝 `autofit_columns(ws)` → `finalize_row_heights(ws)`(native) 순서 유지.
- `mode="estimate"`(CJK-aware 사전계산, usable_frac=0.74/cjk=2.0)는 **headless(PDF·print·렌더서버)** 처럼 Excel이 재계산 안 하는 경우에만.
- 셀에 들어가는 내용을 잘게 분리(A·B)하면 셀이 짧아져 자동 맞춤도 더 깔끔. 병합셀은 autofit 안 되므로 배너도 병합 대신 fill-span 사용.

---

### B. 시장규모/비교 표 — '단위'·'범위(scope)' 별도 열·태그 ★
단위(발행잔액·거래대금flow·AUC·AUM·보유수량·서비스매출·TVL)나 범위(크립토 전체 / RWA 한정 / 혼합 / 인프라)가 **다른 수치를 한 표에 섞을 때**는 그 차이를 **별도 열 또는 태그로 명시**(셀에 몰아넣지 말 것). 단위·범위가 다르면 **합산·교차비율 금지**를 표/각주에 명기 — 독자가 'flow를 stock으로'·'크립토 전체를 RWA로' 오독하는 것을 구조적으로 차단. 동일자산이 트래커 방법론(distributed/represented/단순합)에 따라 3~5배 갈리면 기준·기준일·트래커명 라벨. (cf. 9차 셀 분리, 8차 스토리라인)

**★ 비교 대상은 *항구적 정체성(주력 사업·기능·고객)*으로 기술 — *일시적 상태*(정상화 국면·실적 등락·당기 손익)로 규정 금지.** 비교표·프로필에서 각 대상(회사·은행 등)을 '무엇을 *하는가*(주력 사업·세그먼트)'로 적고, 일시적 상태는 부차 열·주석으로만. *(실사례: 경쟁사를 '정상화 국면'[상태]이 아니라 'UMKM·리테일 중심'[주력 사업]으로 기술.)*

---

# ▣ C. 워딩 · 톤 · 표현

### 10) Wording tone — consulting register

Section titles: **noun phrases**, not questions or colloquialisms.

- ❌ "되는 시장인가? → 그렇다, 단 기관성 자산 중심"
- ✅ "시장 개괄"
- ❌ '무엇을 넣었나'·'어떻게 키웠나'(의문형 소제목) → ✅ '투입 내역'·'전개'

**표 헤더·컬럼명도 명사구**(섹션 제목만이 아님). 1인칭·구어·Q&A·버전메타 금지(실제 반복 위반):
- ❌ "우리 재계산" · "우리 실측" → ✅ "재산정" · "실측치"
- ❌ "새 수치·산식을 쓴 사유 (왜 더 정확한가)" → ✅ "변경 근거" / "적용 근거"
- ❌ "진짜 돈은 ~에 고임" → ✅ "수익 집중 영역은 ~"
- ❌ 시트에 버전·내부 메타("PPT 초안(V4)" · "v4 유지") → ✅ "초안" · "초안 유지" (버전은 CHANGELOG에만)

Body text: avoid casual register.

- ❌ "가장 큼", "어떻게 버나", "되는 시장"
- ✅ "최대 규모", "수익 구조", "성장 가능 시장"

Use `<중간 결론>` (Korean) consistently, not `<중간 Conclusion>` (mixed).

See `references/wording-tone.md` for full guidelines.

### 10-bis) AI patterns & non-professional wording — sweep before delivery

LLMs frequently emit phrases that read as "AI-generated" or hand-holding. In a research deliverable read by paid consultants and senior reviewers, these phrases reduce perceived credibility. Sweep the body text for the patterns below and rewrite into objective, declarative consulting-register prose.

**A. Hand-holding introducers** — remove or convert to objective phrasing:
- ❌ "쉽게 말하면 —", "쉬운 말로 하면 —", "한 줄로 정리하면 —"
- ❌ "흔한 오해 정정 —", "주목할 포인트:", "여기서 핵심은 —"
- ❌ "이 챕터의 가장 중요한 질문:"
- ✅ Either delete entirely, or convert to declarative sentence: "X는 Y이다 (자주 오해됨)" / "X의 핵심은 Y이다"

**B. 종결어미 — 명사형(개조식)으로 끝낸다 ★** (consulting/관청 보고서 표준):
- ❌ "~합니다/~봅니다" (polite-formal, Intro 메타 줄에서만 허용)
- ❌ "~한다/~이다/~된다/~한다." (declarative verb endings — 보고서 본문에 부적합, "갑자기 ~다 문장이 많다"는 실피드백)
- ✅ **명사형 종결**: "~함/~임/~됨/~음" 또는 순수 명사구로 끝냄. 예: "~에 가깝다" → "~에 가까움", "~기본값이다" → "~기본값", "~성립한다" → "~성립", "~봉쇄된다" → "~봉쇄", "~존재한다" → "~존재"
- ✅ 표 안의 해설 셀·support()·key_line()·`<중간 결론>`·`<종합>` 모두 명사형으로 통일(한 문서 안에서 ~다와 명사형 혼재 금지)
- ✅ 직접 독자 호명("독자는 ~")은 객관 3인칭·목적 진술로: "본 섹션은 X를 정리" 식 명사형

**C. Q&A / colon-answer pattern** — convert to single declarative sentence:
- ❌ "왜 X인가? 답: Y이다. 본질이 ~"
- ✅ "X는 Y이다 — 본질이 ~이기 때문이다"
- ❌ "X가 누구인가? = ~"
- ✅ "X는 ~이다"

**D. Over-emphasis adjectives** — drop or downgrade:
- ❌ "결정적", "본질적", "매우 중요한", "절대적" (used 3+ times in one section)
- ❌ **막연한 최상급·중요도 단어 금지 ★**: "아시아 최고", "역내 최고/최상위", "최고 수준", **"최강/최강자/최강의"**, "단연 1위", "압도적" — 막연한 자화자찬·아마추어 워딩. **+ 중요도·중심성 단어 "핵심"·"결정적"·"본질적"도 근거 있을 때만 ★** — 특정 대상에 *볼륨·비중·순위 같은 근거 없이* "X의 핵심/결정적"이라 붙이면 과장(사용자: "두바이가 해외 증권사 C의 핵심인 이유를 설명해봐"). 대신 **구체 수치/순위**로 대체("점유율 24~25%로 1위", "NIM 4.62%로 역내 가장 높음", "스코프 내 유일하게 가동되는 OTC 영업"처럼 근거 동반). 사실적 순위(IDX 1위 등 출처 있는 것)는 허용하되 형용 과장은 제거.
- ✅ Used at most once per section, where genuinely warranted. Otherwise replace with neutral descriptors ("주요한", "구조적", "핵심").

**E. Meta-commentary** — strip:
- ❌ "이게 RWA 시장 이해의 출발점.", "사실은 —", "흥미롭게도 —"
- ❌ "본 표에서 무엇을 읽어야 하는가" 도입 → 표 자체로 의미 전달되도록 작성, 안내문 최소화
- ✅ Delete or convert to one-line objective framing

**F. Excessive emoji / decorative symbols in body** — remove from prose:
- ❌ ✓ ✗ ⓐ ⓑ 🔴 🟡 🟢 in body sentences
- ✅ Acceptable: ★ (1-2 instances for top-priority item flag), 한국어 원/숫자 (①②③), 표 head rows only
- ✅ Tables and headers can use these sparingly for navigation; body prose should be plain

**G. Analogy overuse** — keep analogies but soften framing:
- ❌ "~와 같은 원리이다", "마치 ~처럼" (used as standalone sentence)
- ✅ "~와 동일한 구조이다", "~에 해당한다", or embed analogy inside a declarative claim
- ✅ Keep the analogy itself when it adds clarity (e.g., 자판기·회원제 클럽·예대 마진); just frame it as fact-statement rather than teacher-explanation

**H. English-Korean register mismatch in body** — Korean prose should not insert raw English nouns where a Korean term works:
- ❌ "narrative 재작성", "case study로 다루는 이유"
- ✅ "서술 재작성", "사례 분석 사유"
- Exception: established industry terms (KYC, AML, AUM, broker-dealer, atomic settlement, transfer agent, RWA) stay in English where Korean equivalents would be longer or unfamiliar to the audience

**I. Verbose preambles to tables** — table caption should be a noun phrase, not a teaching sentence:
- ❌ "<X-N> 이후 X-N+1~X-N+4에서 각 차원을 자세히 다룸. 일단 큰 그림은 이 표 한 장."
- ✅ "<X-N> 전후 비교 (4 차원)"

**J. Version-history language in body** — the integrity-check sheet (research-docx only) records version history. Body prose should NOT contain "v13에서 재작성됨" or "[v14 신규]" — these belong in the integrity sheet, not the Summary body.

**K. 인위적 신조어·억지 약어 금지 — 쉬우면서도 공식적인 표준어로 ★** 'professional = 어려운 말'이 아니다. 만들어낸 축약·전문 신조어는 독자가 못 알아본다. **공식적이면서도 직관적인 표준 용어**를 쓴다.
- ❌ "저심화"(coined) → ✅ "경제 규모 대비 금융이 작다/덜 발달"
- ❌ "표준 렌즈"(coined) → ✅ "평가 기준" / "분석 기준"
- ❌ 모호한 은유 "얕은 시장" → ✅ 구체 서술 "신용/GDP·시총/GDP가 낮아 규모가 작다"
- 예외: 결론 등 핵심 한 문장에서는 정제된 어휘로 격을 높여도 됨(단 여전히 명확해야 함).

**L. Exec Summary 헤드라인은 한 줄로 즉시 읽히게 ★** 근거·조건·결론을 한 문장에 다 욱여넣은 '구구절절' 헤드라인 금지. **핵심 주장 하나만 단문**으로(상세·근거는 본문 불릿으로 분리).

**M. 구어·비유 동사/명사 → 정식 실무어 치환(전수 스윕) ★** 셀·헤더·해설 전체에서 아래 부류를 훑어 치환한다(지목된 대표어만 고치고 끝내면 같은 부류 다른 구어가 남아 워딩 피드백이 반복됨):
- **①동사·서술**: 열다→개설 · 두다→보유 · (자금)굴리다→운용 · 삼다→활용 · (수요)몰리다→집중 · 넘어오다→전환 · (사례)나오다→발생·이뤄지다 · 묶다/묶음→결합 · 얹히다→더해지다 · 넓히다→확대 · 만들다→마련·도입 · 쓰이다→활용 · 보여주다→제시
- **②부사·연결**: 한꺼번에→일괄 · 곧바로→즉시 · 이제 막→이제 · ~라서/~이라(구어 인과)→~이므로·~이어서
- **③비유·은어 명사**: 문턱→진입 장벽 · 발판→기반 · 먹거리→수익원 · 대못→핵심 제약 · 死제도→미가동 · 물량→거래량·거래 규모 · 양방향/강자끼리→상호(양측)·선도사 간 · 마중물→초기 시장 조성 · 빡셈→엄격
- **④구문**: '아무 ~나 ~한 게 아니라'→'임의로 ~한 것이 아니라' · '~게 아니라'→'~것이 아니라'
- (표는 대표 스캔 목록일 뿐 — 같은 부류 전체를 훑을 것. cf. [[avoid-translationese-korean]])

**N. 엠대시(—) 절잇기·자명한 부연·섹션 중복 금지 ★**
- **엠대시(—) 절잇기 리드는 번역투** → 콜론(':')이나 완결 문장으로(셀·헤더·해설 모두).
- **자명한 기본 부연 삭제**: 정의만 읽어도 자명한 되풀이 금지(예: 방향을 '외국인→한국주식'으로 정의하고 다시 '기존 거래와 정반대'라 반복 = 군더더기). 같은 jargon·개념어를 여러 셀에 남발하지 말 것.
- **섹션 간 제목·내용 중복 금지**: 두 섹션 제목에 같은 말('시사점' 등)이 겹치면 내용이 실제 겹치는지 확인해 합치거나 논지를 가름(예: 한쪽은 수익 메커니즘, 다른 쪽은 전략 시사점). 소제목은 간결 명사구 — 의문형뿐 아니라 완결 문장형('~로 ~으로'·'~해 ~됨')도 금지.

**Sweep procedure**: after the build is otherwise complete, do one read-through of the Summary sheet body cells and convert A-N patterns. The goal is prose that reads like a McKinsey/consulting-house written-deliverable — declarative, objective, third-person, no hand-holding. Analogies and metaphors stay (they add clarity); the hand-holding framing around them is what gets removed.

---

## 클라이언트 산출물 표현 (단정어·내부 메타 비노출)

산출물은 **읽는 사람(컨설턴트/실무자)이 알아야 할 내용만** 담는다. 작성자만 아는 내부 사정·작업 메타는 넣지 않는다.

- **단정적 단어 금지 ★★**: "확정", "확정본", "결정", "최종" 등 단정 표현을 쓰지 말 것. 산출물은 **리서치 결과를 전달하고 피드백을 받는 것**이지 작성자가 확정하는 게 아니다. 잠정성은 **본문의 `(제안)`·`(가설)`·`(분석)`·`(추정)` 라벨이나 Intro 과제설명**으로 전달한다. **단, 제목·배너에 `[검토용]`·`[초안]`·`[WIP]`·`[v2]` 같은 작업상태 메타마커는 붙이지 않는다**(제목은 과제 내용만; 사용자 지적: 제목의 '[검토용]' 워딩 제거 요청). (사용자 지적: *"내가 뭐라고 확정을 해 — 리서치 전달하고 피드백 받아야 하는데 확정이라 쓰면 되겠냐"*)
- **내부/메타 비노출 ★**: 버전 번호(v4·v6 등), 내부 방법론 이름("v4 방법"), 진행 상태("1차 보고"·"작성예정") 등은 **시트에 쓰지 않는다**(버전·이력은 CHANGELOG 파일에서만). 단, 분석 데이터의 출처·근거 태그(벤치마크/가정/추정/TBD)는 신뢰도 판단에 필요하므로 유지. 내부 문서명은 중립 표기("내부 전략자료" 등).
- **문서 밖 맥락 참조도 비노출 ★ (첫 독자 standalone)**: '정정'·'기존엔 X였으나'(이전 주장)·'요청서대로'(내부 요청 brief)처럼 *편집 이력·요청 맥락*을 아는 사람만 이해되는 표현은 빼고, **처음 보는 사람 기준으로 사실만** 진술한다(위 버전·내부메타 비노출의 일반화).
- **★/편집·교정 마커도 클라이언트 시트 비노출 ★**: 셀·헤더에 박은 **★교정·★신규·★강등·★완결성·★심화·"v1→v2"·"수정"** 류 작업용 변경 표식은 산출물에 노출하지 말 것(위 버전·내부메타 비노출의 연장 — 사용자: "엑셀에서 별표 붙은 내용 다 빼"). 무엇이 신규/교정인지는 **CHANGELOG·검증메모에만** 기록하고, 시트는 *최종 내용만* 담는다(독자는 변경 이력이 아니라 결과를 봄).
- **용어집은 Intro에 넣지 않는다**: 필요하면 Summary 하단, 아니면 생략. Intro는 가벼운 표지(과제·기간·정의·시트맵)만.
- **셀 병합 지양**: 배너는 `model_banner`(병합 없이 fill-span)로. 병합은 복사·정렬·필터를 깨고 시각적으로 취약 → 피한다.
- **배너 색은 여유롭게 Z열까지**: 제목/섹션 배너 색을 콘텐츠 끝에서 애매하게 끊지 말고 Z열까지 채운다(`model_banner` 기본 `span_to=Z(26)`).
- **근거/비고 — 확실한 레퍼런스는 또렷이, 추정은 흐릿하게 ★**: `model_row`의 근거/비고는 **`[벤치마크…]`로 시작하면 검정 10pt**(어떤 수치를 어디서 참고했는지 *수치+출처* 상세 기재), **`[가정]`·`[추정]`·`[초안]`·`[TBD]`는 회색 9pt**로 흐릿하게 자동 처리. 읽는 사람이 "확실한 수치 vs 추정"을 한눈에 구분하게 하기 위함(사용자 지적). → 빌드 시 벤치마크 항목 근거는 *구체 수치+출처코드*를 충분히 적을 것.

---

---

# ▣ D. 출처 · 인용 · 데이터 규율

### 4) Source tagging

- Source tag (`<출처>`, `<Source N>`) sits in **the same row as the section header, in the rightmost column** (K by default).
- Actual links/document names go in the `RAW Data` sheet, grouped by section.
- Every factual claim should have a traceable source. If unknown, write `[출처 확인 필요]` — do not invent.

## 데이터·출처 규율 (실적/전망 · Tier 비노출 · 중복제거 · 출처 재대조)

- **실적 vs 전망 명시**: 성장률 등 셀/표에 아직 오지 않은 연도가 섞이면 "2023~24 실적 / 2025~28 전망"처럼 구분 표기(과거 실적과 예측 혼동 금지). [E-5]
- **콘텐츠 중복 제거 + '비약 트리거' 정의**: 같은 사실을 두 섹션(예: Bull vs 비약 트리거)에 중복 금지. '비약(비선형) 성장 트리거'에는 외생 충격으로 급격 확대 가능한 것만, 점진적 동력은 Bull로. [E-10]
- **Tier 라벨 비노출**: 시트에 `(Tier 0~1)` 등 등급 분류 노출 금지(출처 코드 R/C/A/N/O는 유지). 클라이언트 산출물 원칙(§7 메타 비노출 연장). [E-9]
- **비전문 워딩 sweep**: 역설→비대칭 구조, 별볼일 없다/좋다→하방/상방 시나리오, 파격→비약, 폭발→급증, 윈드폴→유동성 전이/수혜. 본문 셀은 명사형·declarative(§10-bis 연장).
- **출처 귀속 재대조 전제**: 입력 리서치가 deep-research 등 자동 합성이면, 정리 전 각 claim 출처 귀속이 원문과 재확인됐는지 전제(이 스킬은 검증을 하지 않으므로 미검증이면 사용자에게 고지). [E-3]

---

### Summary 시트 — 출처 하이퍼링크 (필수)
- Summary의 출처 태그/소스 코드도 **클릭 가능**해야 함(RAW·docx와 동일 원칙). 평문 출처 금지.
  - 외부 1차자료 직접 링크: `S.source_tag(sm, r, "C1·R2", url="https://...")`
  - RAW Data 섹션으로 점프: `S.source_tag(sm, r, "<Source 2>", internal_ref="'RAW Data'!B14")`

### E. 출처 태그는 헤더(key_line) 행에 — 장문 support와 같은 행 금지 ★ (분석 잘림)
- 긴 `support()`/`[분석]` 문장과 같은 행 K열에 `source_tag`를 두면, 문장이 K 앞에서 **잘림**(autofit이 K를 막는 것으로 판정·오른쪽 흘림 불가; 사용자 지적: "43·100행 분석이 출처 때문에 잘림").
- 규칙: 출처 태그는 **`key_line`(번호 헤더) 행**에 붙이고, 그 아래 `support` 설명 문장은 **태그 없이**(오른쪽 빈 셀로 흘러 안 잘림). `[분석]` 문장은 해석이므로 태그 불필요.

## 출처·인용 엄밀성 (외부 출처·인덱싱·원문 2열)

리서치 산출물의 신뢰는 **출처 엄밀성**에서 나온다. 아래는 해외 증권사 F/증권사 B 전략비교 작업에서 확립된 규칙.

- **출처는 외부 리서치 자료만 ★★**: 인용 출처는 **외부에서 조사한 1차/공개 자료**여야 한다. 내부 장표·미팅 자료는 **리서치 참고용일 뿐 인용 금지**(사용자: "내부 장표는 인용하면 안 되고 외부의 확실한 출처를 인용해야"). 내부 자료의 주장도 외부 출처로 재확인해 인용한다.
- **출처 인덱싱 — 그룹+인덱스 ★**: RAW Data는 출처를 성격별 그룹(A·B·C·D…)으로 묶고 **각 출처에 인덱스(A1·A2·B1…)**를 부여한다. Summary의 모든 인용은 **어느 인덱스를 인용했는지** 표기(표의 출처 열·섹션 source_tag 모두 인덱스로). "출처 A" 같은 그룹 단위 표기는 추적이 안 되므로 지양(사용자: "출처A 중에서도 어떤 출처를 인용했는지 인덱싱"). 표의 출처 열 인덱스는 RAW의 해당 URL로 하이퍼링크.
- **확실한 출처 = 원문 인용(그대로)→번역·해설 2열 ★**: 공식·1차 출처를 인용할 때는 **`원문 인용 (그대로)` 열 + `번역 및 해설` 열**의 2열 구조가 가장 명확(사용자: "본문 인용(그대로)-번역 및 해설 이렇게 제공하는 형태가 제일 명확"). 따옴표 인용은 **원문 어순·연결어 그대로**(의역·재배열 금지).
- **사실 vs 분석 분리 — 인라인 괄호 라벨 ★**: 확실한 출처에서 derive한 사실과 작성자 해석을 분리. 해석·추정·가설은 **문장/셀 안에 `(분석)`·`(가설)`·`(추정)` 괄호 라벨**을 직접 붙인다(섹션 제목의 `[분석]` 라벨과 병행). 출처 없는 주장을 사실처럼 단언하지 않는다.
- **분석 주장도 가능하면 객관 출처로 + 정직한 귀속 ★**: 전략 논리(예: "수직통합→락인 유리")도 **객관적 메커니즘 출처**(기관 리서치 등)로 뒷받침을 시도한다. 단 **출처가 그 사실을 말하는 관점**까지 확인 — 출처가 같은 현상을 '리스크'로 규정하면 "사업자에게 유리"는 분석으로 남기고 *메커니즘만* 출처 귀속(과대귀속 금지; cf. 출처정합성 가이드 §0.5 귀속·§0.6 맥락).
- **단정·작업용어 어휘 스윕**: "판매 불가"·"필수"·"도태" 등 단정어, "공신력 자료"·"verbatim" 등 작업과정 용어는 산출물에서 제거(평이한 한국어로). 명사형 종결과 함께 게재 전 1회 스윕.

---

### B. 출처 = 1출처 1셀 1하이퍼링크 ★★ (반복 리셋되는 항목)
한 셀에 여러 출처를 나열하면 **하이퍼링크가 1개만 걸린다**. 출처가 여럿이면 **셀(또는 행)을 나눠 각 셀에 고유 링크**를 건다. 출처 셀에 `"A·B·C"`처럼 몰아넣지 말 것. (과거 "summary 하이퍼링크 셀을 여러 개로 나눠" 지적이 빌드마다 리셋됨 → 이 규칙으로 고정.)
- **보조 출처는 '지우지 말고 분리'한다 ★ (단일링크 강제의 흔한 오용).** 한 주장을 메인+보조 여러 출처가 받칠 때, 셀당 1링크를 지키려고 보조 출처를 **삭제하면 근거가 사라진다**. 대신 메인 아래 들여쓴 하위 행으로 분리하고 **고유 하위 인덱스(R3a·R3b)**를 부여한다. **각 출처는 자기 셀에 자기 '핵심 확인 수치'**를 적는다(메인과 보조의 수치가 다를 수 있으므로 둘 다 보존). **본문 인용은 그 수치가 실제로 적힌 인덱스**를 가리킨다 — 보조 출처에만 있는 값을 본문이 쓰면 R3b로 인용.
- **분리 방식 = 하위 *행* 또는 출처1/출처2 *열* — 행 분리를 기본 가정하지 말 것 ★.** 한 항목(한 행)에 출처가 2개면 (a) 하위 행(R3a/R3b) *또는* (b) **출처1·출처2 두 *열***로 나눌 수 있다(한 항목을 한 행에 유지). *행 분리를 기본 가정하지 말 것* — 실사례에서 사용자는 *열* 추가를 원함(행 분리본을 열 분리로 재작업). **`make_table`은 `link_col` 1개만 linkify**하므로, 둘째(이후) 출처 열은 `link_cols={col_offset: [urls,...]}`(아래 신설)로 함께 클릭 링크. 빈 보조 출처 셀은 그대로 두면 됨(링크 없이).
- **출처 무결 점검(빌드 말미):** 본문 인용 인덱스 ↔ 출처 시트 인덱스가 **1:1**인지 기계 점검 — 본문에 있는데 시트에 없는 **dangling 0**, 시트에 있는데 본문 어디서도 안 쓰인 **unused 0**, 결번 0.
- **`make_table(link_col=, links=)` 쓸 때 행은 헤더 수와 정확히 일치 — URL을 행 데이터에 또 넣지 말 것 ★ (stray 링크 원인).** 링크는 **`links=[...]` 리스트로만** 전달한다. 행(`rows`)에 출처명 *그리고* URL을 둘 다 넣어 **행 원소 수 > 헤더 수**가 되면, `make_table`이 헤더 밖 여분 열(예: 5열 표인데 6번째 원소 → K열)에 **URL을 평문으로** 써넣고 **Excel이 그 평문 URL을 자동 하이퍼링크화**한다(실피드백: "K50부터 이미 걸린 링크가 또 들어가 있다"). 즉 같은 출처가 J열(정상 link_col)·K열(평문 stray) 두 번. → 행은 `[…, 출처명]`까지만, URL은 `links` 리스트로. **빌드 말미 가드**: `STRAY = [셀 for c if str(c.value).startswith("http") and not c.hyperlink]` 가 **0**인지 확인(평문 URL이 셀에 남으면 행/헤더 수 불일치 신호).
```python
# 표의 출처 컬럼: 행마다 한 출처 + 그 행 출처셀에 그 출처 URL
for i, url in enumerate(src_urls):
    c = ws[f"{src_col}{top+1+i}"]; c.hyperlink = url; c.font = S.house_font(size=11, color=BLUE)
```

---

# ▣ E. 와이드 재무모델 모드 (MODEL MODE)

## 와이드 재무모델 모드 (MODEL MODE) — 개요·헬퍼

서술형 pyramid 레이아웃(좁은 A~E 레이어, 표 F열 시작, 콘텐츠 autofit)은 **10년×다항목 재무모델·와이드 수치 그리드**에는 구조적으로 안 맞는다. 실제 빌드에서 나온 증상: C~F 빈 공백, Y1~Y10 열폭 제각각, 큰 제목 행높이 부족으로 제목 클리핑, B열 부유 border. 이런 산출물은 pyramid 헬퍼 대신 **MODEL MODE 헬퍼**로 빌드한다.

### 원칙
- 모든 콘텐츠를 **B열 기준**으로 정렬(제목·배너·표 동일 시작) — pyramid의 F열 시작/A~E 좁은 레이어 미사용.
- **시계열/수치 열은 균일 폭**(예: Y1~Y10 전부 9). 라벨 열 넓게(≈33), 비고 열 넓게(≈48, wrap).
- 배너(제목·섹션)는 **표 전체 폭으로 병합** → 색이 K에서 끊기지 않음 + 큰 제목 행높이 자동(폰트>행높이 클리핑 방지).
- 테두리는 **표 셀에만**. `divider()`·단일 B열 bottom border 금지(부유 border 원인).
- **비고가 있는 행만** 행높이 자동, 나머지는 기본 → 불필요하게 높은 행 방지.

### 헬퍼 (styles.py)
```python
cols = S.model_widths(ws, n_series=10)                     # A=spacer·B=라벨·C~L=균일·M=비고, 인덱스 반환
S.model_banner(ws, 1, cols['label'], cols['end'], "제목",   # 풀폭 병합 배너(제목/섹션)
               fill=S.PALETTE['TASK'], size=14)            # fill=None→섹션색, fill=False→무채움(헤드라인)
r = S.model_header(ws, r, ["항목","Y1",...,"Y10","비고"], left_col=cols['label'])
r = S.model_row(ws, r, "활용률", [...], fmt='0.0%', note_col=cols['note'])     # 숫자/수식 셀 + 비고
r = S.model_row(ws, r, "누적 ROI", [...], fmt='0.0%', note_col=cols['note'], bold=True, hilite=True)
```
- 이종(異種) 비교표(열마다 단위 다름: 금액·%·연차)는 `model_row`(단일 fmt) 대신 **`make_table(left_col="B", number_formats={...})`** 를 B열에서 시작해 쓴다(열별 포맷·하이라이트 지원).
- MODEL MODE에서는 `autofit_columns`/`finalize_row_heights`/`sheet_title`/pyramid `make_table`(F시작)를 **쓰지 않는다** — 섞으면 이 모드가 없애려는 공백·들쭉날쭉 폭이 되살아난다.

### 라벨 선행 `=` 금지 ★ (#NAME? 원인)
- 합계/소계 행 라벨을 `= 수익 합계`처럼 **`=`로 시작하면 Excel이 수식으로 오인** → `수익`을 정의되지 않은 이름으로 보고 **#NAME?** 에러. 합계 라벨은 `수익 합계 (억)`·`Σ 운영비`·`· 순이익`처럼 `=` 없이 쓴다. (빌드 후 점검: 어떤 셀도 비수식인데 `=`로 시작하지 않는지 grep.)

---

# ▣ F. 빌드 워크플로우 · 파일 · 산출

## Workflow

1. **Read sources** — read every uploaded/pasted file. Extract key facts + source references.
2. **Design structure** — decide how many numbered sections (1, 2, 3, ...) and what each contains. Write the ToC into `Intro`. Each section follows conclusion → evidence → source.
3. **Build the workbook** — import helpers from `scripts/styles.py` and fill cells. Direct openpyxl is fine but must honor the rules above.
4. **Formulas for calculated values** — never Python-hardcode.
5. **Recalc and verify** — use the `xlsx` skill's `scripts/recalc.py`. Confirm zero formula errors.
6. **Self-checklist** (see below) — go through it before delivering.
7. **Deliver** — save as `research_<task>.xlsx` in `/mnt/user-data/outputs/` and call `present_files`.

---

## Filename convention (mandatory)

Output file MUST be saved as `research_<short-task-name>.xlsx` in `/mnt/user-data/outputs/`.

Examples:
- `research_제조업_비용구조.xlsx`
- `research_RWA_시장조사.xlsx`
- `research_커피체인_경쟁사.xlsx`

This prefix distinguishes research-organizer outputs from research-docx outputs (which use `consulting_` prefix). The two skills produce different deliverables — see research-docx's SKILL.md for the comparison.

**Exception**: when editing an existing file the user provided, preserve the existing filename (the "preserve existing templates" rule takes precedence).

### Version collision — never overwrite existing files

When iterating on the same task multiple times in a conversation (v1 → v2 → v3 ...), the build MUST NOT overwrite a previously-saved file. The user keeps earlier versions for comparison and silently overwriting forces them to rename or delete files manually.

**★ 버전 표기·이관 규칙 (vN_M — research-docx §6·[[deliverable-versioning]]와 동일, 전 산출물 일관).** 구버전은 **삭제 아님(이동만)**:
1. 표기 **`vN_M`** — **flat 번호(v1·v2·v3) 금지**(major/minor 구분이 사라짐). **N=주요(구조·핵심 수치·결론 변경), M=경미(워딩·소소 보강·재빌드).** ★ **N은 *주요 변경이 생긴 즉시* 올린다 — 전달/사인오프 시점까지 minor로 끌고 가지 말 것**(표 전면 재설계 등 구조 변경이면 그 자리에서 N++).
2. **M++(경미 재빌드)** 시 직전 minor를 `이전버전\`으로 이관 → 메인엔 *현 major의 최신 minor 1개*만.
3. **N++(주요 변경)** 시 직전 major는 메인에 *그대로 보존*(주요 버전은 메인 누적, 이전버전 이관 X). 예: `v1_2→v2_0`이면 v1_2 메인 유지. **N++ 후엔 v2_0에서 다듬어(v2_1, v2_2…) 어느 `v2_N`에서 보고·전달**한다 — *v2_0 자체를 보고본으로 쓰지 않는다*(v2_0은 주요 변경의 시작점).
4. **주요 버전(N)이 메인에 5개를 넘으면** 가장 오래된 major부터 `이전버전\`으로 이관(메인엔 최근 ~5 major).
- ⚠️ **`resolve_output_path` 자동 bump는 *flat `_vN` 충돌회피*라 vN_M을 자동 생성하지 않는다** → 빌드 시 파일명을 **`research_<task>_vN_M.xlsx`로 직접 지정**(경미 재빌드=`_v1_1` 등). **같은 세션 반복 빌드는 작업본(`_wip.xlsx` 고정 경로 덮어쓰기)으로** 돌리고, 체크포인트/합의에서만 `vN_M`로 승격(세션 중 v1~v5 flat이 쌓이던 원인 차단).

**Rule**: every build call resolves its output path through `S.resolve_output_path(desired_path)` BEFORE calling `wb.save(path)`. This helper:

1. If the desired path doesn't exist → returns it unchanged
2. If the desired path exists → scans the directory for sibling versions, picks the next available `_vN` suffix, prints a notice, returns the new path

Standard build pattern:

```python
def build():
    desired = "/mnt/user-data/outputs/research_제조업_비용구조.xlsx"
    path = S.resolve_output_path(desired)   # ← required step
    wb = Workbook()
    # ... build sheets ...
    wb.save(path)
    return path
```

**Do NOT hardcode `_v2`, `_v3`, etc. in the build script just because earlier runs created those files** — `resolve_output_path()` picks the next number automatically. Hardcoding works once but breaks on the next iteration.

**★ 가드 세이브 — 덮어쓰기·비-vN_M을 *하드 차단*(권장; 모델 자의 우회 방지).** `wb.save()`/`resolve_output_path`(flat _vN·우회 가능) 대신:
- **발행**: `path = S.next_version_path(folder, "research_<task>")`(다음 *vN_M*·minor bump·존재 안 하는 경로) → `S.save_deliverable(wb, path)`. 파일명이 vN_M 아니면 `ValueError`, 기존 파일이면 `FileExistsError`(조용한 덮어쓰기 차단). 정말 덮어써야만 `overwrite_ok=True`(loud 경고).
- **반복 빌드(같은 세션 튜닝)**: `S.save_wip(wb, S.wip_path(folder, "research_<task>"))` — `_wip` 스크래치(버전명 아님·덮어쓰기 OK). 체크포인트/사인오프에서만 `save_deliverable`로 승격(세션 중 v1_0을 반복 덮어쓰지 말 것).
- **게이트 floor(우회 불가)**: publish 게이트가 `deliverable` 파일명이 `_vN_M`인지 *결정론* 검사 → 아니면 **GATE FAIL**(빌드 코드로 가드를 꺼도 게이트가 막음). cf. [[deliverable-versioning]]·[[apply-skill-rules-at-execution]].

**Exception**: 사용자가 명시적으로 "덮어써라"라고 할 때만 `save_deliverable(..., overwrite_ok=True)`. 기본은 항상 비파괴·새 vN_M.

---

## Editing existing files

When the user asks to add to or modify an existing organized file:

- **Do NOT overwrite the existing template.** Inherit the file's font, column-hierarchy depth, source-column position, and color conventions (xlsx skill rule: "preserve existing templates").
- Detect the existing column-hierarchy mapping first, then insert new content at the matching layers.
- If the existing file uses different colors than this skill's palette, follow the existing file's palette.

---

### 빌드 = 항상 스킬 함수로 ★★
- 빌드 스크립트는 **데이터 + 수식 조립 + 스킬 헬퍼 호출**만 담고, 레이아웃 로직(폭·병합·테두리·행높이)은 **전부 styles.py에** 둔다. 빌드 스크립트 안에 `put/banner/write_row`류 레이아웃 함수를 새로 정의하지 말 것 — 필요하면 styles.py에 헬퍼로 추가해 호출한다(하드코딩 엑셀은 스킬 작동을 검증하지 못함).

---

## 빌드 워크플로우 + 시장규모/비교 표

### A. 검증 내장 + 단일 빌드스크립트 (별도 _verify.py 양산 금지) ★
**원칙:** 빌드마다 별도 `_verify_vN.py`·`_grep.py`를 새로 만들면 실행 경로가 매번 달라져 권한 prompt가 재발하고 폴더가 임시 py로 지저분해진다 → 검증은 빌드 스크립트 말미에 내장한다(아래).
- **검증을 빌드 스크립트 말미에 내장**: `wb.save(path)` 뒤에 산출물을 재로드해 핵심 지표(시트 목록·하이퍼링크 수·explicit-height 행 수·keyfont/highlight)를 `print("VERIFY ...")`로 출력 → 별도 verify 파일 불필요.
- **한 과제 = 한 빌드 스크립트**: 새 .py를 양산하지 말고 **기존 빌드 스크립트를 수정·재실행**(명령·경로 불변). 그래야 "don't ask again" 한 번으로 세션 내내 권한 prompt 0(경로가 매번 바뀌면 allowlist가 재사용 안 됨). UTF-8은 `sys.stdout.reconfigure(encoding="utf-8")`로 스크립트 내장(`$env:` prefix 복합명령 금지).
- ad-hoc 점검이 꼭 필요하면 **하나의 스크래치 파일을 덮어쓰며** 재사용(경로 고정). 작업 끝나면 임시 _*.py 정리.

### B. 환경 함정 (python 셸링·인코딩·버저닝) — 국내사 해외법인 세션 피드백
- **python.cmd 셔임 인자에 `|`(파이프) 등 cmd 메타문자 금지 ★**: 이 PC의 `python`/`python3`는 cmd 셔임이라 `& python.cmd ... "a|b|c"` 호출 시 cmd.exe가 `|`를 파이프로 해석해 **호출이 통째 실패**(인자 정규식에 `|` 넣었다가 20개 fetch 전멸 사례). → CLI 인자에는 `|` 쓰지 말고, 복잡한 정규식·다중 패턴은 **스크립트 파일 안에 넣어** 실행하거나 `python.exe` 직접 호출.
- **콘솔 한글 mojibake(cp949)**: python stdout 한글이 PowerShell 콘솔에서 깨짐. 디버그·검증 출력은 **UTF-8 파일로 쓰고 Read 도구로 확인**하는 패턴이 안정적(콘솔 print에 의존 금지). 스크립트 자체는 `sys.stdout.reconfigure(encoding="utf-8")`.
- **항상 `_vN`로만 발행, base(무버전) 산출 금지 ★**: `resolve_output_path`의 자동 bump는 *base가 존재할 때만* 다음 _vN을 준다. 한번 base로 냈다 지우면 다음 빌드가 다시 base로 떨어져 수동 rename 필요. 처음부터 **`research_<task>_v1_0.xlsx`처럼 `vN_M` 형식**으로 발행한다(첫 발행 = `_v1_0`). **flat `_v1`(M 없음) 금지** — major/minor 구분이 사라지고 'Version collision' 규칙의 vN_M과 어긋난다(예시를 `_v1`로 적어 첫 발행을 plain v1로 내는 실수 차단). cf. [[deliverable-versioning]]

---

# ▣ G. 자가 점검 (게재 전 통합 체크리스트)

## Self-checklist (before delivering)

Run through these. If any fails, fix before delivery.
- [ ] **단계 게이트 (2단계)** — ① 빌드 직후 `SG judge research-organizer publish … deliverable=<xlsx> dir=<과제폴더>` → **GATE PASS** 라인 붙여넣기(바닥 통과) + 백그라운드 gate-audit 발사. ② 게재 직전 `SG resolve research-organizer deliverable=<xlsx> dir=<과제폴더>` → **RESOLVE PASS** 라인 붙여넣기(모든 SG 호출에 같은 `dir=` — 동시작업 격리). RESOLVE PASS 아니면 게재/‘done’ 금지 · 봉인 루브릭 직접 열람 금지.

- [ ] **★ 첫 게이트 전 1패스 일괄 스윕 (반응식 N차 감사 금지)** — 노출/게재 전에 상단 '수정·재빌드 필수 게이트' 4항목(워딩 register 전체 grep·출처귀속 건건·단위 자족성·§G)을 *게이트에 걸기 전* 한 번에 훑어 통과 상태로 만든다. 감사가 잡아주길 기대하고 미완본을 노출 게이트에 걸어 *지적된 한 부류(lens)씩* 고치는 반복(버전·감사 N회 낭비) 금지 — 감사 지적은 '누락 스윕'의 신호이지 정상 루프가 아니다.

**Hierarchy & structure:**
- [ ] Each layer uses a consistent column (one layer = one column)
- [ ] **Layer zone (A~E) stays narrow; content zone (F~M) gets the width**
- [ ] **Numbered sections stop at D — sub-sections are F-column table titles, not deeper layers**
- [ ] Tables always start at F (no table starts at G or deeper)
- [ ] Bullets are single-cell strings, not split across columns
- [ ] Top-level sections have bold + bottom border

**Color:**
- [ ] Total `Key font` (blue text) cells in Summary: ≤ 2
- [ ] **Per table: highlighted rows ≤ 2 — decisive 행에만(없으면 0)** ★ 강점/약점·정합도 구분은 행 색이 아니라 *컬럼*으로; '핵심수치 표라서' 기계적으로 첫 행 칠하지 말 것; 연속 3행+ 하이라이트는 `xlsx_highlight` 게이트가 결정론 FAIL
- [ ] No section has 3+ different fill colors
- [ ] Black body text by default

**Column format:**
- [ ] Every column in every table uses ONE unit format (no mix of 50억 / 5B USD / 수억)
- [ ] Reliability tags positioned consistently within a column
- [ ] Currency in English M/B USD format
- [ ] **필드 분해 ★** — 수치·연도·출처를 한 셀에 cram 금지, 별도 컬럼으로 펼침. 출처는 1출처 1셀 1하이퍼링크(여러 출처 한 셀 금지). 행높이는 finalize_row_heights(mode="native")=Excel AutoFit. [9차]
- [ ] **빌드검증 내장·단일스크립트 + 시장규모표 단위/범위 열 ★** — 별도 _verify.py 양산 금지(검증은 빌드 스크립트 `VERIFY print` 내장)·한 과제=한 스크립트 재실행(경로 불변, 권한 prompt 최소화); 단위·scope 다른 수치는 별도 열/태그 + 합산·교차비율 금지 명기. [10차]
- [ ] **수치 자족성 ★ (압축 ≠ 암호화)** — 바 숫자/%가 아니라 지표 정의까지(예: "세전이익률 15%"), 같은 단위 다른 지표는 각 라벨+산식("15%·62%"로 라벨 없이 병치 금지), 약식 기호(`A→B`)는 관계어 명시, 추산값 실측=볼드/추정=이탤릭. 셀만 떼어 봐도 "무엇이 얼마"가 읽힘. [11차]

**Content discipline:**
- [ ] Every claim has a source tag or `[출처 확인 필요]` (no silent omission)
- [ ] No invented facts
- [ ] Categorization shows all items, not just "interesting" ones
- [ ] Each multi-item section has `<중간 결론>` or `Implication`
- [ ] **스토리라인 리드 존재 ★** — 문서 최상단(또는 Intro 다음 리드 시트)이 `핵심 메시지 → 핵심 수치(수치·의미·출처 표) → 인사이트 흐름(주장+근거) → 종합 결론`으로 위→아래로 읽힘. **리드만 읽어도 구두 보고가 됨**(분석 섹션 표만 나열 금지). 각 인사이트는 주장(헤더)+근거(표/불릿) 한 쌍. [8차]
- [ ] **익썸 = 문장형 산문 ★** — 익썸이 *완결 문장*(파편·단일토큰 불릿·표 낭독 아님)·핵심만(상세는 본문에)·순차 논리·결론은 *결정*(우산 용어 회피)·직관적이되 프로페셔널 용어. 요청 안 한 상세 섹션 무더기 0. 소리 내 읽어 구두 보고로 들리는가.
- [ ] **문장·논리 전개 명료성 ★ (§A 7-quater)** — 리드 셀 먼저(결론/주제 리드로 시작)·논리를 셀·열로 명시(A→B→C, 결과/기호만 나열 0)·한 셀 한 주장(만연체 0)·메시지는 양면(기회+제약). 셀에서 기호·숫자 가려도 '무엇·왜' 읽힘.

**Wording:**
- [ ] Section titles are noun phrases
- [ ] No colloquialisms ("되는 시장", "가장 큼", etc.)
- [ ] Consistent Korean labels (`<중간 결론>`, not `Conclusion`)
- [ ] **AI틱·아마추어 패턴 sweep ★ (§C-10-bis A~N)** — hand-holding('쉽게 말하면·주목할 포인트·여기서 핵심은')·Q&A 콜론답·근거 없는 과장 최상급('최강·압도적·아시아 최고·최고 수준')·meta-commentary('사실은·흥미롭게도')·본문 산문 속 이모지(✓✗🔴🟡🟢)·인위적 신조어·불필요 영한혼용 0 · **구어·비유 어휘(열다·두다·물량·문턱·발판·삼다·몰리다·한꺼번에·아무~게 아니라 류→정식 실무어) 전수 스윕 0**(M) · **엠대시(—) 절잇기·자명한 부연·섹션 중복 0**(N)
- [ ] **명사형 종결 통일 ★** — 본문·표 해설·support·`<중간 결론>` 모두 명사형(~함/~임/~됨/~음); 동사 종결('~한다/~된다/~이다')·polite('~합니다/~봅니다')을 본문에 혼재시키지 않음(Intro 메타 줄만 예외)

**Mechanics:**
- [ ] Calculated values are Excel formulas, not hardcoded
- [ ] `recalc.py` reports zero formula errors
- [ ] Font is consistent (맑은 고딕 11pt by default, or matches existing file)
- [ ] **Every sheet calls `set_clean_view(ws)` — cell gridlines off**
- [ ] **Task banner and section_label fills span from start column through K (or further if needed) — no merge, just extended fill**
- [ ] **Intro sheet uses lighter register — `task_banner()` only, NO `section_label()`. Meta labels via `intro_meta_label()` / `intro_meta_value()`**
- [ ] **Intro sheet column widths: use `set_intro_widths(intro)` (D widened to ~15 for meta labels), NOT `set_layer_widths()`. Other sheets (Summary, RAW Data, 정합성 검증) keep `set_layer_widths()` with default D=3.2**
- [ ] **Row height — DO NOT bulk-set to 15pt ★** — DO NOT iterate `for r in range(...): ws.row_dimensions[r].height = 15` at the end of build. That silently clips multi-line wrapped cells. Instead use the new helper `S.finalize_row_heights(ws)` which inspects each row, estimates wrap line count from cell text length × col width, and sets a sensible height per row. Empty rows get 15pt; wrap cells get 22pt + (lines-1)*20pt. Image rows are left untouched (already pinned by `embed_image_inline`).
- [ ] **Image anchor stability** — When images coexist with auto-wrap text, `finalize_row_heights()` must be called BEFORE `wb.save()`. Otherwise OneCellAnchor → TwoCellAnchor drift on save/load and image position shifts.
- [ ] **wrap=True is for content cells, NOT narrow layer columns ★** — `support()` / `evidence()` default `wrap=False` is correct for narrow columns (B=3.2, C=3.2, D=3.2, E=3.2 chars). If you set `wrap=True` on a column that narrow, finalize_row_heights will explode the row to 400-600pt (1 char fits per visual line). Rules:
  - Long body text starting at E (or any narrow column) — keep `wrap=False`. Excel will overflow text into empty cells F→K visually as a single line. This is the standard pattern.
  - If neighboring cells (F~K) are NOT empty, the text WILL be clipped — split into multiple shorter cells (`row += 2` between each).
  - `wrap=True` is appropriate for: (a) table body cells where col_widths give 20+ chars per column, (b) K-column analysis cells next to images (K width ≥ 60).
- [ ] **KEY_FONT split when too long ★** — KEY_FONT cells with > ~100 characters become unreadable. Split the message: keep KEY_FONT on the single most important sentence; put the rest in adjacent regular `support()` cells (one bullet per row, `■` marker prefix, two rows apart). Maintains KEY_FONT ≤ 2 per workbook rule while preserving readability.
- [ ] **Column widths — content/overflow-aware autofit for ALL columns, nothing hardcoded ★** — Do NOT bake a fixed GRID and do NOT hardcode layer widths via `set_layer_widths()`. Build all tables WITHOUT `col_widths`, then call `autofit_columns(sm)` ONCE at the end (before `finalize_row_heights()`). It sizes every column from A onward: must-fit cells → content width; pure-overflow layer columns (B/C/D/E) → thin indent automatically (no more C/D/E stuck at Excel's wide ~8.43 default). Pin a column only via `autofit_columns(sm, overrides={...})` (e.g. RAW Data source column).
- [ ] **Source-credibility separation ★** — When information comes from official sources (company website, regulatory filings, press releases), use the exact official wording as a quote where possible. LLM-derived interpretation (industry mapping, framework comparisons, scenario implications) MUST be labeled `[분석]`, `[추정]`, `[분석·매핑]`, or `[분석 framework]` next to the section title. Do not blend interpretation into a sentence that reads as if from the source.
- [ ] **Table col_widths — data columns ≥ 25 chars, content columns 30-50 ★** — `make_table()`'s default col widths are often too tight for Korean+English mixed content (wraps to 4-5 lines). For consulting deliverables: index/marker columns 8-12, label columns 18-25, data/description columns ≥ 30 (often 40-50 if it holds prose). One row should typically wrap to 2-3 lines max, not 5-6.
- [ ] **No `#`/`①②③` numbering columns unless the order is semantically meaningful ★** — A `#` or `①` column eats 6-10 chars that could go to content. Use it only when (a) the rows are an ordered sequence that callers reference by number, or (b) there are 10+ rows that need a quick visual count. For 3-7 row reference tables (back-office, comparison, fee model), drop the numbering column entirely. Don't compensate by starting at E — keep tables at F to preserve column hierarchy.
- [ ] **Bullet/marker font consistency in scattered cells ★** — When splitting long text into multiple `support()` cells using `■` markers (or any other bullet symbol), every cell must use the same Font (name, size, color). Don't mix `key=True` (KEY_FONT color) and default in one bulleted list — the visual register breaks. Either all bullets are KEY_FONT (limit 2 cells) or all are regular.
- [ ] **Image embed: use `embed_image_inline()` helper, NOT raw `ws.add_image()`** — the helper applies explicit OneCellAnchor + AnchorMarker so the image lands at exactly the requested row, and pins only the image's row range (preserves auto-height elsewhere). Raw `ws.add_image(img, "F84")` often converts to TwoCellAnchor on save/load and the anchor row drifts.
- [ ] **RAW Data lines use `raw_data_entry()` or `raw_data_section()` — URLs MUST be clickable hyperlinks, not just text**
- [ ] **Intro 라벨 교정 ★** — '내용 기준' 행 없음 / 단독 '시계' 아님(→'분석 기간/전망 시계') / '접근 방식' 아님(→'분석 구조') / '출처 등급' 아님(→'출처 유형')
- [ ] **Executive Summary 분리·배치 ★** — 헤드라인 결론이 D(결론 레이어, `key_line`)에 있음(E 아님). `exec_summary()` 배너 + `divider()`로 본문과 분리. Exec Summary엔 전체 핵심만, 상세는 본문
- [ ] **Summary 출처 하이퍼링크 ★** — `<출처>`/소스 코드가 클릭 가능(외부 URL 또는 RAW 내부 점프). 평문 출처 0. `source_tag(url=/internal_ref=)` 사용
- [ ] **용어 해설 ★** — 고유명사·전문용어(샤리아·수쿠크·NIM·hilirisasi·Depth/Access/Efficiency/Stability 등) 첫 등장 시 `term_note()`/`glossary_block()`로 해설, 약어 풀어쓰기
- [ ] **큰 제목 + 위계 ★** — `sheet_title`(B열, size 16) 존재 / Executive Summary와 섹션 타이틀이 같은 C열(`section_label`) 정렬 (큰제목 B > Exec·섹션 C > 단계 D > 본문 E)
- [ ] **열 너비 적응형 규칙 ★** — `make_table`에 `col_widths` 미사용, 레이어 폭도 `set_layer_widths`로 박지 말고, 빌드 끝에 `autofit_columns(sm)` 1회(→`finalize_row_heights(sm)`)로 A열부터 전 열 자동 산정(레이어=얇은 인덴트, 콘텐츠=내용기반). 매직넘버 GRID 금지. 정의 목록은 `definition_list()`(2열), `■ 용어—정의` 단일 셀 금지
- [ ] **term_note wrap=False** — 용어 해설 행 높이 폭발 없음
- [ ] **Build script calls `S.resolve_output_path(desired)` before `wb.save()` — never overwrites existing files. One change = one new version file. Don't `rm` and re-save to the same path.**
- [ ] Output filename starts with `research_` prefix (unless editing user-provided existing file)
- [ ] Saved to `/mnt/user-data/outputs/` and presented via `present_files`

---

---

# ▣ H. 헬퍼 스크립트 · 참고

## Helper scripts

- **`scripts/styles.py`** — house-style helpers. Read function signatures once at the start. Key functions:
  - Banners: `task_banner()`, `section_label()` (Summary only — NOT for Intro)
  - Hierarchy: `key_line()`, `support()`, `evidence()`, `bullet()`
  - Tables: `make_table()` (wrap/col_wrap params), `definition_list()` (2열 용어·정의), `source_tag()`
  - Layout (적응형): `set_clean_view()`, `set_layer_widths()` (레이어 열만; cde_width=None이면 C~E 기본폭), **`autofit_columns()`** (콘텐츠 기반 열폭 — 빌드 끝), **`finalize_row_heights()`** (실제 열폭 읽어 행높이), `rightmost_used_col()` (배너 span 자동), `set_intro_widths()` (Intro), `set_widths()`
  - **RAW Data**: `raw_data_entry()` / `raw_data_section()` — URLs auto-detected and linkified
  - **Intro meta**: `intro_meta_label()` / `intro_meta_value()` — lighter register than Summary. Pair with `set_intro_widths()`
  - **제목/Exec Summary / 용어 / 출처링크**: `sheet_title()` (큰 제목 B열 size16), `exec_summary()` (Exec Summary 배너 C열 + 헤드라인 D 배치), `term_note()` / `glossary_block()` (고유명사·전문용어 해설), `source_tag(url=… / internal_ref=…)` · `linkify_internal()` (Summary 출처 클릭 하이퍼링크)
  - Self-check: `selfcheck_from_path()`(★ 저장 후 호출·`warnings`=0 확인 — KEY_FONT·RAW 링크 + **반복 실패 3종 기계 검출**: 비표 셀 wrap=True[행 폭발·열폭 max 고정]·`=`+한글 셀[Excel 수식 오인·제거]·Intro/Summary 탭색 미설정. 반환 dict에 `bad_wrap`·`stray_http`·`lead_eq` 좌표 포함), `count_key_font_cells()`, `count_highlight_cells()`

  Newer helpers covered in `references/formatting-spec.md`.
- **`scripts/example_build.py`** — minimal working example showing all 3 sheets.

If you skip the helpers and call openpyxl directly, you still must follow the formatting rules above.

---

## References (read as needed)

- `references/formatting-spec.md` — exact cell coordinates, RGB values, layout analysis of reference files
- `references/color-policy.md` — color rationale, anti-patterns, when each color is allowed
- `references/column-standards.md` — column format conventions (units, tags, currency)
- `references/wording-tone.md` — consulting-register tone guide

---
