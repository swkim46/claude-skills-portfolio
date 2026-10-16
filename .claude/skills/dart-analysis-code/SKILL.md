---
name: dart-analysis-code
description: >
  Claude Code(네트워크 ○·대용량 컨텍스트) 전용 DART 재무분석. OpenDART API로 상장사 재무제표를
  자동 수집해 수익성·성장성·안정성 표준 비율·다년도 추이를 계산·해석(채팅 요약 + Excel 데이터팩)하고,
  단일 기업 딥다이브와 경쟁사 N사 비교(순위·peer 통계·산업 vs 당사 판별)까지 자동화한다. 분석 엔진
  (비율 레지스트리·analyze/compare/build_excel)은 dart-analysis 스킬과 **공유**하고, Code 네트워크
  이점으로 **종속/관계기업 요약재무 추출**(무키 viewer.do·해외법인 벤치마킹)과 **산출물↔원문 결정적
  수치 검증**을 더한다. "DART 재무분석", "전자공시 재무제표", "○○ 재무분석/비율/실적",
  "경쟁사 재무비교", "○○ vs △△", "비용구조/세그먼트 비교", "해외법인/종속기업 실적"을
  **Claude Code**에서 요청하면 사용. **claude.ai 챗(네트워크 차단) 환경이면 `dart-analysis` 스킬**을 쓸 것.
---

# DART 재무분석 (Claude Code 전용)

> ### ▶ 단계 게이트 (빠른 바닥은 인라인, 빡빡한 검수는 비동기 gate-audit)
> hook 차단 환경의 강제 레버. 헬퍼 `SG` = `python3 <REPO>/.claude/skills/_stepgate/stepgate.py`.
> **★ 동시 작업 격리** — 이 워크플로의 *모든* SG 호출(form·judge·resolve)에 **같은 `dir=<과제폴더 절대경로>`**를 붙인다(다른 과제=다른 폴더→자동 격리, 같은 폴더 다중 산출물은 `deliverable=`로 구분). 누락 시 form↔judge 키가 갈려 오작동.
> **① 빌드 직후(바닥)**: `SG form dart-analysis-code deliver dir=<과제폴더>` → 산출물·raw.json 열고 **정직히** 답(합격 기준 비공개·**표현이 회차마다 뒤집히니 지난 답을 재사용하지 말고 이번 문구를 그대로 읽고 답할 것**) → `SG judge dart-analysis-code deliver <id>=<값> … deliverable=<xlsx 절대경로> dir=<과제폴더>`. **GATE PASS**면 바닥 통과 → FIRE 블록으로 **백그라운드 감사 에이전트(general-purpose) 발사·즉시 다음 작업**.
> **② 발행 직전(해소)**: `SG resolve dart-analysis-code deliverable=<xlsx 절대경로> dir=<과제폴더>` → **RESOLVE PASS 아니면 발행/‘done’ 금지**(HOLD=동기 드레인·FAIL=수정·재감사).
> 체크포인트: **deliver**(산출물↔원문 수치 재대조 §4-2) → 비동기 lens 감사 → resolve. 봉인 루브릭(rubrics/) 직접 열람 금지(감사됨). 상세: `skill-maintainer` 〈단계 게이트 프로토콜〉.
> `<REPO>` = 이 저장소를 클론한 **절대경로**(문서상 표기). `dir=`·`deliverable=`은 절대경로여야 하므로 호출할 때 실제 경로로 치환한다.

`dart-analysis`의 **Claude Code 튜닝판**. 챗(claude.ai) 전용 무게(환경판별·PDF 업로드 게이팅·데이터
반입 모드·컨텍스트 초과 경고)를 들어내고, **네트워크 자동 수집을 기본**으로 한다. 분석 엔진은
**원본 dart-analysis 스킬과 공유**(복제 0·드리프트 0) — 비율 레지스트리·`analyze.py`·`compare.py`·
`build_excel.py`·`peer.py`를 절대경로로 호출한다. Code의 네트워크 이점을 살려 **종속/관계기업 추출**과
**결정적 수치 검증**을 추가했다.

> **원본은 불변(팀·챗용 보존).** 이 스킬은 사본이 아니라 *얇은 워크플로우 래퍼* — 엔진 버그·비율
> 수정은 원본 `dart-analysis`에서 하면 이 스킬에 자동 반영된다(같은 스크립트 공유).

**엔진 경로(상수)**
- 원본 스크립트: `<REPO>/.claude/skills/dart-analysis/scripts`  (이하 `…/dart-analysis/scripts`)
- 원본 references(비율·별칭·렌즈): `…\dart-analysis\references\`  (analyze.py가 `__file__/../references`로 자동 사용)
- 본 스킬 전용 추가 스크립트: `…\dart-analysis-code\scripts\`  (`dart_find.py`·`dart_subs.py`)
- 본 스킬 전용 references: `…\dart-analysis-code\references\`  (증권 프로파일 `ratios-securities`·`account-aliases-securities`·`lenses\securities.md` — §5. 일반 비율은 위 원본 references 사용)

---

## 0. 전제 (Code 환경 고정)

- **OpenDART 인증키** — `opendart.fss.or.kr` 무료 발급(40자). `.env`의 `DART_API_KEY` 또는 `--key`로 전달.
  **키는 코드·git·로그에 남기지 않음.** **키 미보유 시: 발급 안내 또는 무키 원문 반입 모드(§3-bis)** 중 선택.
- **Python 의존성** — 네트워크는 표준 `urllib`. 분석 `pyyaml`(미설치 시 references/*.json 자동 fallback),
  Excel `xlsxwriter`. `pip install pyyaml xlsxwriter`.
- **환경 가정** — 네트워크 ○. **키 있으면 API 자동 수집 기본**(§2·§3) / **키 없으면 원문 반입 모드**(§3-bis, research-verified 연계). 네트워크가 전면 차단된 환경이면 이 스킬이 아니라 `dart-analysis`(챗 PDF 업로드).
- **언어 = 한국어 고정**(인사·질문·산출 전부). 티커·계정명·고유명사·인용은 원문 보존.

---

## 1. 기업 식별 (★ 최우선 가드레일)

잘못 식별하면 **이후 모든 숫자가 엉뚱한 회사**가 된다. 입력을 타입으로 분기:

| 입력 | 처리 |
|---|---|
| 숫자 8자리 | 고유번호(corp_code) 직행 → 확정 |
| 숫자 6자리 | 종목코드 → `corpCode.xml` 매칭 → 상장사 1건 |
| 회사명 | `corpCode.xml` 검색 → 1건이면 확정, **2건↑이면 근거(업종·상장여부) 달아 추천 후 확인** |

- `collect.py`가 식별을 처리(`--ident`); 동명 다수는 `--pick N`. **임의 선택 금지 — 추론-후-확인.**
- 확정 1줄: *"○○증권(종목 0000XX / 고유 00XXXXXX) 기준 2021–2025 분석"*.

---

## 2. 단일 기업 딥다이브

```powershell
$DA = "<REPO>/.claude/skills/dart-analysis/scripts"
# 1) 정형 재무제표 수집 (네트워크) — 다년도 BS/IS/CF + 접수번호
python "$DA\collect.py" --ident "삼성전자" --years 3 --fs CFS --out /tmp/raw.json
#    --year 2024(종료연도) · --reprt 11011|11012|11013|11014(연간/반기/1Q/3Q) · --pick N · --key <KEY>
# 2) 경로 B 드라이버(판관비 명세·부문 P&L) — rcept는 raw.json의 rcept_no (네트워크)
python "$DA\parse_notes.py" --rcept <rcept_no> --what all --out /tmp/notes.json
# 3) 비율·추이 + 드라이버 병합 (오프라인) — references는 원본것 자동 사용
python "$DA\analyze.py" --raw /tmp/raw.json --notes /tmp/notes.json --out /tmp/ratios.json
#    ★ 증권사(고객사 A·증권사 B·한투 등)면 증권 프로파일로(§5·§7) — 안 하면 부채비율 등 오도:
#      $SEC = "<REPO>/.claude/skills/dart-analysis-code/references"   # 증권 프로파일(이 스킬 소유)
#      python "$DA\analyze.py" --raw /tmp/raw.json --ratios "$SEC\ratios-securities.yaml" `
#        --aliases "$SEC\account-aliases-securities.yaml" --out /tmp/ratios.json
# 4) Excel 데이터팩 (오프라인) — Raw/Ratios/Summary 3시트 + 네이티브 차트
python "$DA\build_excel.py" --ratios /tmp/ratios.json --raw /tmp/raw.json --out "<회사명>_재무분석_YYYY-YYYY.xlsx"
```
- 단위 주의: 정형=원, 판관비=백만원, 부문=억원 → 해석·표기 시 통일.
- `analyze` 결과의 `drivers`(판관비 구조·부문 P&L)는 채팅 요약의 **"영업이익 why"** 해석에 반영.

## 3. 경쟁사 비교 (다중 기업) — 원샷

```powershell
$DA = "<REPO>/.claude/skills/dart-analysis/scripts"
python "$DA\peer.py" --idents 코리아세븐 비지에프리테일 지에스리테일 `
  --years 5 --segment-focus 편의점 --out-dir /tmp/cvs --xlsx "편의점3사_비교_2021-2025.xlsx"
#   동명: --idents "현대:2" …  ·  키: --key <KEY>  ·  표준비율만(빠름): --no-notes
```
`peer.py` = 회사별 `collect`+`parse_notes`+`analyze` → `compare` → `build_compare_excel` 일괄.
중간 산출(`raw_*`/`ratios_*`/`comparison.json`)도 out-dir에 남아 검증·재가공 가능.
**비교 모드 핵심 메시지**: peer 통계(중앙값·순위) + **산업(macro) vs 당사(개별) 판별**(최신연도 YoY 전사 동조=산업요인 / 한 회사만 역행=당사이슈, `comove_latest.divergent`).
- ⚠️ **증권사 비교는 peer.py 원샷 불가** — peer.py 내부 analyze가 **일반 프로파일 고정**(`--ratios` 미전달)이라 증권사를 넣으면 부채비율 등 오도. 증권 N사 비교는 회사별로 `collect.py` → `analyze.py`(증권 옵션, §5) 수동 실행 후 `compare.py` + `build_compare_excel.py`로 합칠 것:
  ```powershell
  $SEC = "<REPO>/.claude/skills/dart-analysis-code/references"
  python "$DA\collect.py" --ident 고객사 A(증권사) --years 5 --out /tmp/sh_raw.json   # 사별 반복
  python "$DA\analyze.py" --raw /tmp/sh_raw.json --ratios "$SEC\ratios-securities.yaml" `
    --aliases "$SEC\account-aliases-securities.yaml" --out /tmp/sh_ratios.json
  python "$DA\compare.py" --ratios /tmp/*_ratios.json --out /tmp/comparison.json   # 판관비 미수집 시 비용구조 자동 skip
  python "$DA\build_compare_excel.py" --comparison /tmp/comparison.json --out "증권사_비교.xlsx"
  ```
  (peer.py에 프로파일 플래그를 배선하면 원샷화 가능 — 후속 개선)

## 3-bis. 무키(無key) 원문 반입 모드 (API 키 없을 때 · research-verified 연계)

OpenDART 키가 없거나 "사업보고서 **원문을 직접 확인**해 쓴다"는 원칙(research-verified 사용)일 때.
**분석 엔진은 데이터 출처를 가리지 않으므로**, 원문에서 표준계정만 확보해 `from_input.py`로 raw.json을 만들면
§2·§3과 동일하게 비율·Excel이 나온다(네트워크·키 불요). 반입 스크립트(`from_input`·`notes_from_input`)는
공유 엔진에 있어 Code에서도 그대로 호출된다.

```powershell
$DA  = "<REPO>/.claude/skills/dart-analysis/scripts"
$SEC = "<REPO>/.claude/skills/dart-analysis-code/references"
# 1) 원문 확보 — research-verified로 사업보고서 fetch·출처 정합성 검증
#    (접수번호·viewer URL이 필요하면 §4-1의 dart_find.py로 무키 조회)
# 2) Claude가 원문에서 표준계정 추출 → CSV (references\chat-input-template.csv 양식)
#    헤더: account,2021,2022,…(오래된→최신) · 값은 원 단위(콤마 허용) · 증권이면 행에 '영업수익' 그대로
# 3) CSV → raw.json (무키·오프라인)
python "$DA\from_input.py" --csv in.csv --name "고객사 A(증권사)" --fs CFS --out /tmp/raw.json
# 4) 비율 — 증권이면 증권 프로파일(§5)
python "$DA\analyze.py" --raw /tmp/raw.json --ratios "$SEC\ratios-securities.yaml" `
  --aliases "$SEC\account-aliases-securities.yaml" --out /tmp/ratios.json
# 5) Excel
python "$DA\build_excel.py" --ratios /tmp/ratios.json --raw /tmp/raw.json --out "고객사 A(증권사)_재무분석.xlsx"
```
- **경로 B 드라이버**(판관비 명세·부문)도 반입 가능: 원문 표 → `notes_from_input.py --cost cost.csv --segments seg.csv --out notes.json` → `analyze.py --notes notes.json …`. 양식: `references\cost-input-template.csv`·`segment-input-template.csv`.
- **★ 출처 수기 기록(가드레일 §7)** — `from_input.py`는 `rcept_no`를 `(수동 입력)`으로 박으므로, 채팅 요약 References에 **실제 접수번호·페이지를 직접 기재**할 것. **단위(원/백만원)·연결/별도(CFS/OFS)도 원문에서 확인해 명시**(자동 기록 안 됨).
- **research-verified와 분업** — research-verified = 원문 fetch·**출처 정합성 검증**(수치↔원문 일치), 이 스킬 = 검증된 수치로 **비율·추세·Excel**. 검증된 CSV를 넘기면 두 스킬이 이어진다.
- **언제 API가 더 편한가** — 경쟁사 N사 비교는 회사 수만큼 원문 추출을 반복해야 해 수기 부담이 큼 → 키 있으면 `collect.py`/`peer.py` 자동수집이 빠르다(§2·§3). 단일 기업·소수 비교는 반입 모드로 충분.

## 4. ★ Code 확장 (네트워크 ○라 가능 — 챗판엔 없음)

### 4-1. 종속·관계기업 요약재무 추출 (해외법인 벤치마킹)
정형 재무제표 API 밖(**연결주석 레벨**)이라 무키 `viewer.do`로 직접 파싱한다.
```powershell
$DC = "<REPO>/.claude/skills/dart-analysis-code/scripts"
python "$DC\dart_find.py" "증권사 B증권"                              # 회사명 → 사업보고서 접수번호·접수일
python "$DC\dart_subs.py" <rcpNo> <savePrefix> "Mirae Asset Securities \(Vietnam\)"
#   → 연결주석 '종속기업 요약재무'에서 해당 해외법인 행(자산·부채·자본·매출·순이익) 추출
```
- ⚠️ **python.cmd 셔임 `|` 함정** — `dart_subs.py`의 정규식 인자에 `|`(여러 법인 OR)를 넣어 **`.cmd` 셔임으로** 호출하면 cmd.exe가 파이프로 해석해 **호출 전멸**. → **`python.exe` 직접 호출**하거나 **회사·법인별 1건씩** 실행(인자에 `|` 금지).
- **종속 vs 관계기업 구분 ★** — 두 요약표는 컬럼·등장위치가 달라 단순 정규식이 **관계기업 행을 종속으로 오판**(BIDV 사례) → 표 종류를 명시 구분.
- 단위 사별 상이(천원/백만원)·자본 컬럼 유무 상이 → **회사별 단위/컬럼 구조 먼저 확정** 후 추출.

### 4-2. 산출물 ↔ 원문 결정적 수치 검증
산출물(xlsx)을 1차 원문(저장 HTML)으로 **결정적 재파싱 → 단위환산 → 셀단위 1:1 PASS/FAIL** 검증.
- 절차: 원문 표 재파싱 → **단위환산까지**(천원→백만원→억원) → 산출물 셀과 1:1 → 전수 집계.
- **다년도 교차정합**(비용 0 무결성): *각 연도 보고서의 「당기」 = 익년 보고서의 「전기」* 일치 확인.
- 프로토타입: `<REPO>\과제_해외법인_실적\_raw_sources\verify_numbers.py`(4사 하드코딩 예시 — **과제별로 회사·계정·단위를 바꿔 일반화**해 쓸 것). 동일 검증을 검토자 측에서 돌리려면 `research-source-check` §3-bis.

## 5. 비율·업종 프로파일 (원본 references 공유)

- 비율 정의는 `…\dart-analysis\references\ratios.yaml`(코드 하드코딩 금지). 기본 셋: 수익성(영업이익률·순이익률·ROE·ROA) / 성장성(매출·영업이익 YoY) / 안정성(부채비율·유동비율·이자보상배율) / 현금흐름(영업CF 1줄).
- **증권업 전용 프로파일** (★ 이 스킬 소유 — `dart-analysis-code\references\`, dart-analysis 원본 불변) — 손익구조가 달라(영업수익 총額, 매출원가 없음, 부채 거대) 일반 비율이 오도 →
  `--ratios "…\dart-analysis-code\references\ratios-securities.yaml" --aliases "…\dart-analysis-code\references\account-aliases-securities.yaml"`. ROE·레버리지비율·자기자본비율·영업수익 성장 산출, 부채비율·유동비율·이자보상배율 제외. 엔진 무수정 위해 **영업수익→매출액 fold**(비율 라벨은 '÷영업수익'로 정확, Excel '주요 금액'만 매출액 라벨). 해석 렌즈 `dart-analysis-code\references\lenses\securities.md`.
- 새 지표/업종은 원본 references에 추가(이 스킬도 자동 반영).

## 6. 산출물 포맷 (answer-first 피라미드)

**답부터** — 헤드 메시지(so-what 1~2불릿) → `[범주]` 주장(볼드) → `[근거]/[수치]/[추세]/[드라이버]/[주의]/[경보]` 백업 sub-bullet. 데이터·주제어 나열 금지(메시지 피라미드). 비교 모드도 동일 골격 + `synthesis`(leader/outlier/profit_gap/cost_drivers/signals)를 거버닝 출발점으로.
- **볼드는 결정적 수치·메시지에만**, **명사형 종결**, **끝 마침표 금지(컨설팅 펌 한국 오피스)**, **한국어 고정**, **References(접수번호·페이지)**.
- 비교는 MECE 3개 내외(모든 비율 나열 금지), 각 주장에 수치 최소 1개 + **why**(cost_structure/segments)까지.
- (상세 포맷 예시는 `dart-analysis` §7.1·§13.3과 동일 — 같은 하우스 스타일)

---

## 7. 가드레일 (절대 하지 말 것)

- **단계 게이트(2단계)** — ① `SG judge dart-analysis-code deliver … deliverable=<xlsx> dir=<과제폴더>` → **GATE PASS**(수치 재대조 §4-2) + 백그라운드 gate-audit 발사. ② 발행 직전 `SG resolve dart-analysis-code deliverable=<xlsx> dir=<과제폴더>` → **RESOLVE PASS**(form·judge·resolve 전부 같은 `dir=` — 동시작업 격리). RESOLVE PASS 아니면 발행/‘done’ 금지 · 봉인 루브릭 직접 열람 금지.
- **식별 추측 금지** — 동명·모호 시 임의 선택 말고 추론-후-확인(§1).
- **숫자 환각 금지** — 공시 원문에 없는 값을 추정·생성하지 않음.
- **단위·연결/별도·접수번호 누락 금지** — 원/천원/백만원, CFS/OFS, rcept_no 항상 병기(검증 가능하게).
- **금융업 라우팅** — 증권사는 **증권 프로파일**로(§5). **은행·보험·카드·지주사·특수구조는 안내 후 중단**("표준 재무분석 대상 아님 → FI/INS 전문가 별도 고도화"). 일반 프로파일은 영업수익→매출액 별칭 탓에 금융사를 통과시켜 부채비율 등을 *오도*하므로 라우팅 필수. *(가드레일은 비율분석에만 — 추출·fetch 레이어는 금융업도 재사용)*
- **적자·음수 처리** — 전기 적자(≤0) 시 증가율 'N/A(전기 적자)', 영업적자 시 이자보상배율 무의미.
- **끝 마침표 금지** — 불릿·문단 끝 `.` 안 찍음(중간 문장·`…`은 허용).
- **키 노출 금지** — `DART_API_KEY`를 코드·git·로그에 남기지 않음.

## 참고
- **분석 엔진·비율 정의·상세 메시지 포맷**: `dart-analysis` 스킬과 공유(원본이 정본 — 엔진 수정은 거기서).
- **챗(네트워크 차단) 환경**: `dart-analysis` 스킬(데이터 반입 모드).
- **추출·검증 방법론 일반**(DART 식별·2경로·다년도 교차정합): `research-verified` §1-bis / 수치 재대조 검토자 측: `research-source-check` §3-bis.
- DART fetch / PDF 노하우: 메모리 `sec-edgar-curl-fetch`·`python-env-setup`·`dart-overseas-subsidiary-extraction`.
