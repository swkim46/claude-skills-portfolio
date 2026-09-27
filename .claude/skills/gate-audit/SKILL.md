---
name: gate-audit
description: >-
  단계 게이트(stepgate)의 *심판자*. 두 모드. ⓐ **사후 룰 감사**: 끝난 세션에서 게이트 준수를 결정론으로 점검(`stepgate.py audit` —
  게이트 미호출·봉인 정답지 열람·FAIL 무시·subverdict 위조·순서 위반·비동기감사 미완료·해소게이트 누락 R1~R7). ⓑ **비동기 lens
  감사(백그라운드)**: 작업 스킬이 발행 직후 이 스킬을 *백그라운드 Agent*로 띄우면(FIRE 블록·reqid), audit_rubrics의 전 lens로 산출물을
  *적대적*으로 검수(`audit-judge`)해 위반을 인용·기록한다 — 작업 스킬은 그동안 다음 작업을 한다. 사용자가 "게이트 감사", "단계 게이트 점검", "이번
  세션 게이트 검사", "게이트 잘 지켜졌나", "gate audit", "뭐가 어디서 샜나(게이트)" 등을 요청하면 ⓐ로 활성화. 스킬 *파일을 고치는* 건
  skill-maintainer, 게이트를 *실행*하는 건 각 작업 스킬이고, 여기는 *심판*(사후 감사 + 비동기 lens 심판)을 맡는다.
---

# Gate Audit (단계 게이트 사후 감사 — 룰베이스)

> ### ▶ 단계 게이트 (필수 — 합격은 네가 아니라 *심판*이 판정한다)
> hook 차단 환경의 강제 레버. 헬퍼 `SG` = `python3 <REPO>/.claude/skills/_stepgate/stepgate.py`.
> 보고 전 체크포인트: ① `SG form gate-audit report` 로 관찰 항목을 받아 → ② **정직히** 답한 뒤(합격 기준 비공개·**표현이 회차마다 뒤집히니 지난 답을 재사용하지 말고 이번 문구를 그대로 읽고 답할 것**) → ③ `SG judge gate-audit report <id>=<값> ...`. **GATE PASS가 아니면 진단 제시 금지**(말로 'done' 불가).
> 체크포인트: **report**(audit 스크립트를 *실제로* 돌렸는가·눈으로 추측 안 했는가). 게이트 루브릭 직접 열람 금지(열람 시 무효 — Read는 감사됨). 상세 규약: `skill-maintainer` 〈단계 게이트 프로토콜〉.
> `<REPO>` = 이 저장소를 클론한 **절대경로**(문서상 표기). `dir=`·`deliverable=`은 절대경로여야 하므로 호출할 때 실제 경로로 치환한다.

끝난 세션의 단계 게이트 *준수*를 **사후**에 점검하는 스킬. 핵심 원칙 하나: **판정은 모델이 아니라 `stepgate.py audit`(결정론 규칙)가 한다.** 모델은 transcript를 눈으로 훑어 "괜찮아 보임/문제 같음"을 *추측하지 않는다* — 스크립트의 룰 출력을 그대로 전달하고, 플래그된 항목은 *사실 확인*만 한다.

- **이건 무엇이 아닌가**: 스킬 *파일*을 고치는 건 `skill-maintainer`(편집 시점 정합성), 게이트를 *실행*하는 건 각 스킬. 여기는 *이미 끝난 실행*이 게이트를 지켰는지 본다(사후·외부 감사).
- 별도 컨텍스트(새 세션/에이전트)에서 돌리면 자기감사가 아니라 외부 감사가 된다.

---

## 모드 ⓑ — 비동기 lens 감사 (백그라운드 심판) ★ 신규

작업 스킬이 발행 직후 `judge … async`로 GATE PASS를 내면 **FIRE 블록**(reqid 포함)을 출력한다. **gate-audit은 Skill이라 `subagent_type`이 아니다** → 작업 스킬은 **general-purpose(또는 claude) 백그라운드 에이전트**(`run_in_background=true`)에 그 FIRE 지시를 위임하고 곧장 다음 작업으로 넘어간다. 그 백그라운드 에이전트가 아래 절차로 산출물을 *전체 가이드*로 빡빡하게 심판한다(패킷에 검사표가 다 들어 있어 동일 — 작업과 중첩되어 시간 단축). 절차:

1. **패킷 수령** — `SG audit-judge <reqid>` 로 산출물 경로 + 전 lens(audit_rubrics/<skill>.json) + global_directives를 받는다.
2. **산출물 실물 검수** — 산출물을 *실제로 열고*(xlsx면 openpyxl·docx면 텍스트), lens별로 판정. **규칙: 위반 라인/셀좌표/캡션을 *그대로 인용* 못 하면 그 lens는 PASS**(관대한 PASS·추측 FAIL 둘 다 금지). 너는 산출물을 *떨어뜨리려는* 적대적 검수자다.
3. **needs_fetch lens** — 출처 링크가 그 수치 보이는 페이지로 가는지 *실제 WebFetch*로 착지 확인.
4. **기록** — `SG audit-judge <reqid> --record subverdict=PASS|FAIL findings=<위반 요약·인용> evidence=<핵심 인용>`. high lens가 하나라도 위반이면 FAIL.
5. audit_rubric이 없으면(아직 미작성) **기본 빡빡 검수**: 그 스킬 SKILL.md 가이드 전반을 산출물과 *그대로 인용* 대조(인용 못 하면 PASS).

작업 스킬은 발행 직전 `SG resolve <skill> deliverable=<…>`로 네 기록이 전부 PASS인지 확인한다(T3). 네 FAIL은 그 산출물을 *새 vN_M로 고치게* 만든다 — 그래서 **빡빡할수록 좋다**(시간 압박 없음).

---

## 모드 ⓐ — 사후 룰 감사 워크플로우 (4단계 — 모델 인지 최소)

1. **대상 세션 JSONL 특정** — 사용자가 경로를 주면 그 파일. 없으면 `--latest`로 최신 세션 자동 선택(기본 베이스 `~\.claude\projects`). 다른 프로젝트면 베이스 경로를 인자로.
2. **룰베이스 감사 실행** —
   ```
   SG audit <세션.jsonl>
   SG audit --latest [basedir]
   ```
3. **스크립트 출력을 *그대로* 전달** — `AUDIT PASS`(위반 0) 또는 `AUDIT FAIL — 룰 위반 N건`(R1~R7). 신호 집계(동기·비동기)·통과 cp·각 위반의 라인 번호를 요약·가감 없이 보고.
4. **FAIL이면 *사실 확인*만** — 각 룰이 가리킨 transcript 라인을 열어 *무슨 일이 있었는지 사실*을 확인해 첨부. **위반 확정/오탐 판정은 사람 몫** — 모델이 임의로 "괜찮음"이라 덮거나 "위반"이라 단정하지 말 것(스크립트 플래그 = 결정론 탐지, 최종 판단 아님).

## 룰 카탈로그 (audit가 결정론으로 잡는 것)

| 룰 | 탐지 |
|---|---|
| **R1 게이트 미호출** | 산출물(xlsx/docx/pptx)은 났는데 `GATE PASS` 0건 |
| **R2 정답지 열람** | **봉인** 루브릭(`_stepgate\rubrics`)을 **Read/Grep**(열람=위반; Write=작성은 정상이라 제외). ★ `audit_rubrics`는 *비봉인*(감사자가 읽는 게 정상)이라 **제외** |
| **R3 FAIL 무시** | `GATE FAIL` 이후 후속 PASS 없이 산출물 |
| **R4 subverdict 위조** | `subverdict=PASS`인데 서브에이전트(Agent) 호출 0건(실물 심판 없이 통과; `audit-judge`는 judge로 안 셈) |
| **R5 순서 위반** | 체인 스킬에서 마지막 cp PASS는 있는데 선행 cp PASS 누락(예: publish인데 absorb·fetch 없음) |
| **R6 비동기감사 미완료** | 세션 산출물에 대한 감사 큐가 `PENDING`으로 남음 — 백그라운드 발사/기록 안 됨 또는 `resolve` 미드레인 |
| **R7 해소게이트 누락** | `GATE ENQUEUED`(비동기 게이트 사용)·산출물은 있는데 `RESOLVE PASS` 0 — 감사를 resolve로 안 닫고 사인오프 |

## 정직한 한계 (과대선전 금지)

- **모드 ⓐ는 탐지지 예방이 아니다** — 끝난 뒤 잡는 사후 감사(피드백·책임 추적용). 모드 ⓑ(비동기 lens 감사)는 *예방*에 가깝지만 산출물은 감사 완료 전 디스크에 이미 존재한다(막는 건 사용자 사인오프=resolve이지 파일 생성이 아님 — "빌드됐으나 미사인오프" 구간 존재).
- **모드 ⓑ도 결국 LLM 심판** — 관대 PASS 위험은 lens별 *그대로 인용 못 하면 PASS*(cite-or-pass)·적대적 framing으로 줄이되 증명은 아니다. 커버리지는 audit_rubric에 채운 만큼만(계속 자라는 검사표 — 새 교정은 lens로 승격).
- **통과한 무지성 도장·기록 안 된 행위는 못 잡는다** — 역항목까지 거짓으로 답해 PASS가 났거나, 애초에 게이트를 안 불러 transcript에 흔적이 없으면 사후로도 안 보인다(단 R6/R7이 비동기 미완료·해소 누락을 백스톱).
- **R2는 봉인 루브릭(`rubrics\`) Read/Grep만** — `audit_rubrics\`는 비봉인이라 제외, 루브릭 Write/Edit도 정상 작성이라 제외(오탐 방지). 라이브 세션 JSONL은 잠금/부분 기록일 수 있어 종료된 세션 대상이 정확.
- **게이트셋 인지** — R1/R5는 *실제 호출된 게이트 대상 스킬*(루브릭 보유 + 세션에서 Skill/stepgate 호출 검출)에만 적용. 비게이트 스킬(deckster 등)의 산출물은 오탐으로 잡지 않음. R5 체인은 루브릭 `after`에서 자동 도출(하드코딩 아님 — 새 멀티-cp 스킬도 자동 커버).
- 룰 플래그는 *결정론 탐지*지 확정 위반이 아니다 — 사람이 라인을 확인해 종결.

## 자가 점검 (진단 보고 전)
- [ ] **단계 게이트 통과** — `SG judge gate-audit report …` → **GATE PASS** 라인 붙여넣기(audit 실제 실행·눈 추측 아님). 미통과 시 보고 금지.
- [ ] `stepgate.py audit`를 **실제로 실행**했고, 그 출력을 가감 없이 전달했는가(모델 자의 진단 0).
- [ ] FAIL 항목은 transcript 라인의 *사실*만 확인했고, 위반 확정/오탐을 임의로 단정하지 않았는가.

## 참고
- 감사 엔진: `…\_stepgate\stepgate.py audit`(모드 ⓐ 룰베이스 스캔) · `audit-judge`(모드 ⓑ lens 패킷·기록) · `resolve`(작업 스킬의 사인오프 게이트)
- 빡빡 검수 체크리스트: `_stepgate\audit_rubrics\<skill>.json`(비봉인 lens — 없으면 SKILL.md 가이드로 기본 검수)
- 게이트 시스템 규약: `skill-maintainer` 〈단계 게이트 프로토콜〉 / 도구 메모리 [[stepgate-step-gate-tool]]
