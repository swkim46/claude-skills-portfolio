# 리서치 방법론 + 팩트 정합성 검증 방법론 가이드 (v2)

> v2: 인도네시아 경제/금융 리서치 세션(2026.6)의 검증·산출물 빌드 교훈을 **Part E**로 추가. v1 본문은 그대로 유지.

컨설팅 보고서 작성 시 적용하는 리서치 수행 절차와 팩트 정합성 검증 절차를 정리한 표준 작업 가이드. 본 문서는 작성자의 작업 절차를 정의하며, 보고서 본문에는 노출하지 않음.

---

## 목차

- [Part A. 리서치 방법론](#part-a-리서치-방법론)
  - [A-1. 핵심 원칙 — 양보단 질](#a-1-핵심-원칙--양보단-질)
  - [A-2. 출처 신뢰도 Tier 체계](#a-2-출처-신뢰도-tier-체계)
  - [A-3. 출처 카테고리 라벨링 체계](#a-3-출처-카테고리-라벨링-체계)
  - [A-4. 기준 사업자(Anchor) 디테일 분석 패턴](#a-4-기준-사업자anchor-디테일-분석-패턴)
  - [A-5. 매출 라인 분해 — 산업 일반화의 핵심](#a-5-매출-라인-분해--산업-일반화의-핵심)
- [Part B. 팩트 정합성 검증 방법론](#part-b-팩트-정합성-검증-방법론)
  - [B-1. 검증 5단계 워크플로우](#b-1-검증-5단계-워크플로우)
  - [B-2. WebSearch summary 사용 금지 원칙](#b-2-websearch-summary-사용-금지-원칙)
  - [B-3. PDF 원문 우선 검증 원칙](#b-3-pdf-원문-우선-검증-원칙)
  - [B-4. 본문 미확인 시 처리 — 3가지 옵션](#b-4-본문-미확인-시-처리--3가지-옵션)
  - [B-5. 분석 합성 framework의 솔직 acknowledgement](#b-5-분석-합성-framework의-솔직-acknowledgement)
  - [B-6. 사실 / 추정의 명확한 분리](#b-6-사실--추정의-명확한-분리)
  - [B-7. 검증 자동화 도구 모음](#b-7-검증-자동화-도구-모음)
- [Part C. 실제 검증 실패 사례 — 교훈](#part-c-실제-검증-실패-사례--교훈)
- [Part D. 메타 코멘트 노출 금지 원칙](#part-d-메타-코멘트-노출-금지-원칙)

---

# Part A. 리서치 방법론

## A-1. 핵심 원칙 — 양보단 질

**다수의 약한 출처보다 소수의 1차 자료가 우월**. 한 보고서 안에 출처가 30개 들어가도, 그중 검증되지 않은 매체 인용이 다수면 신뢰도는 떨어짐. 10~15개의 Tier 0~1 자료로 구성된 보고서가 더 강함.

### 실행 원칙

1. **Tier 0~1 1차 자료를 우선적으로 확보**한 후, 부족한 부분만 Tier 2~3 보조 활용.
2. **검증 불가능한 출처는 의도적으로 삭제** (남겨두지 않음).
3. **모든 인용은 페이지 번호 또는 정확한 위치 명시** (가능한 경우).
4. **인용 표현은 verbatim 그대로** — 의역하지 않음.
5. **출처 섹션은 카테고리화 + 라벨링** — 라벨로 본문 인용과 1:1 매핑.
6. **모든 인용 링크는 게재 전 "접속 가능"을 검증** — 사용자에게 제공하는 최종 URL은 반드시 열려야 한다. 404/dead/리다이렉트 깨짐은 **접속 가능한 미러·대체 URL로 교체하거나 인용 삭제**. 자동 fetch가 403/타임아웃이어도 브라우저·curl로 도달하면 "접속 가능"으로 보되, **반드시 도달 가능한 형태의 URL을 본문에 기재**(예: 봇 차단되는 deep-link 대신 열리는 공식 랜딩/PDF). PDF·로그인·검색결과 전용 링크처럼 클릭 시 원문이 안 뜨는 URL은 금지. (절차: [B-1 1단계](#1단계--url-접근성))

---

## A-2. 출처 신뢰도 Tier 체계

| Tier | 정의 | 예시 | 활용 원칙 |
|---|---|---|---|
| **Tier 0** | 규제기관·중앙은행·국제기구 1차 자료 | OCC handbook, FINMA risk monitor, Fed press release, BIS, IMF | 최우선 인용. 정의·규제·통계 등 권위 있는 사실 |
| **Tier 1** | SEC·금감원 등 공식 의무 공시 + 글로벌 컨설팅 1차 보고서 | 10-K / 20-F / 6-K / 8-K, firm GWR, McKinsey survey, Deloitte report | 정량 데이터·기업 실적·산업 framework |
| **Tier 2** | 기업 자체 공시 (보도자료·연차 보고서·홈페이지) + 산업 데이터 사업자 | 기업 IR 페이지, Capgemini 보고서, Datos Insights | 보조 인용. 기업 자체 framing이 필요할 때 |
| **Tier 3** | 트레이드 매체 — 이해당사자 인터뷰·재공시 | finews, CoinDesk, Hubbis, Korea Times | 1차 자료가 부족할 때만 보조 |
| **Tier 4** | 검증 불가 매체·블로그·이해당사자 의역 | 신원 미상 블로그, 출처 추적 불가 매체 | **인용 회피**. 부득이 사용 시 별도 caveat 필수 |

### Tier 선정 시 유의사항

- **단일 Tier로만 구성된 보고서는 약함**: Tier 0 규제 자료만 있으면 시장 현실 못 잡음. Tier 3 매체만 있으면 권위 부족. **혼합 전략**이 필요.
- **같은 정보가 Tier 0/1과 Tier 3에 동시 존재하면 무조건 Tier 0/1 인용**: 예) Custodia Bank Fed 가입 거부 사실 → Federal Reserve press release(Tier 0) 직접 인용, CoinDesk 보도(Tier 3)는 보조.
- **WebSearch 결과 summary는 Tier를 결정할 수 없음**: 원문 fetch 후 Tier 판별. WebSearch 자체는 발견 도구일 뿐 인용 출처가 아님.

---

## A-3. 출처 카테고리 라벨링 체계

본문 인용과 출처 섹션을 1:1 매칭하기 위해 카테고리화된 단문자 라벨 사용.

### 카테고리 prefix 예시

| Prefix | 카테고리 | 사용 예 |
|---|---|---|
| **R** | Regulator (규제기관) | R1 OCC handbook, R2 FINMA, R3 OCC Anchorage approval, R4 Fed Custodia denial |
| **C** | Consulting / Research | C1 Capgemini, C2 컨설팅 펌, C3 McKinsey, C4 Deloitte |
| **S** | SEC / Corporate disclosure | S1 MS 4Q25 earnings, S2 UBS 4Q25 full report, S3 UBS PWM 페이지 |
| **K** | Credit rating (한국 신평사) | K1 KIS, K2 NICE |
| **M** | Mirae / Company official (기업 공식) | M3 증권사 B Global Business |
| **N** | News (일반 매체·외신) | N1 한국 매체, N11 Bloter 등 |
| **B** | the Bell (산업 매체) | B1 the Bell 기사 |
| **P** | Press release / 보도자료 | P1 PR Newswire, P5 Korea Times |
| **H** | Historical / Heritage 1차 | H2 KCMI, H4 FSC 등 |
| **Y/A** | 회사별 prefix (Sygnum=Y, AMINA=A 등) | 케이스별 정의 |

### 라벨 사용 패턴

```
본문 인용: "해외 비중 39%" [N12] [N11]
출처 섹션 도입: "[N12] 매체명 — 기사 제목"
                URL (Consolas, 작은 글씨)
```

→ 본문의 [N12]를 보고 독자가 출처 섹션에서 즉시 찾을 수 있도록 동일 라벨 prefix 유지.

---

## A-4. 기준 사업자(Anchor) 디테일 분석 패턴

다수 회사 비교보다 **1~2개 기준 사업자를 깊게 분석**해 시장 본질 도출.

### 케이스별 anchor 선정 예시

| 시장 | 선정한 기준 사업자 | 선정 근거 |
|---|---|---|
| TradFi PB | Morgan Stanley WM + UBS GWM | 글로벌 #1·#2, SEC 풍부 공시, 매출 라인 분해 명확 |
| Crypto PB | Sygnum + AMINA | FINMA 풀스택 은행 라이선스 보유 유일 사업자, 같은 거점 |
| IB 재편 | 증권사 B증권 (단일 anchor) | 직접 사례 분석 대상 |

### Anchor 분석 4축 (필수 포함)

1. **사업 부문 구조** — 그룹 내 해당 사업부의 위치·정의
2. **매출 라인 분해** — 정량 분해 (% 비중)
3. **핵심 상품** — 시장 차별화 product 1~2개 디테일
4. **재무 비교** — 동일 시장 내 다른 anchor와 직접 비교

---

## A-5. 매출 라인 분해 — 산업 일반화의 핵심

본 사례 회사의 매출을 라인별 % 분해 → 다수 anchor에서 동일 패턴 확인 → **산업 일반화**.

### 표준 분해 표 형식

```
| 라인 | Anchor1 (FY) | Anchor2 (FY) | 평균 비중 |
|---|---|---|---|
| 운용 자산 수수료 | 58.7% | 52.7% | 약 55% |
| 순이자수익 | 24.9% | 27.0% | 약 26% |
| 거래 수수료 | 14.4% | 20.1% | 약 17% |
```

### 일반화 도출 패턴

> "두 기준 사업자가 거의 동일한 매출 구성을 보임 → 'X% + Y% + Z%'가 [시장명] 수익원의 일반화 가능한 패턴."

핵심: 1개 사례만으로 일반화하지 않음. 최소 2개 anchor의 매출 구성이 유사해야 산업 패턴으로 인정.

---

# Part B. 팩트 정합성 검증 방법론

## B-1. 검증 5단계 워크플로우

각 인용에 대해 다음을 순서대로 확인:

### 1단계 — URL 접근성

- HTTP 200 또는 브라우저 정상 접근
- Cloudflare 봇 차단 / 403 / 404 발생 시 별도 표기 또는 인용 회피
- 동일 정보의 다른 미러 사이트 확인

### 2단계 — 원문 형식 판별

- **웹페이지 텍스트** vs **PDF 원문** 둘 다 존재할 경우 **PDF 우선 검증** (자세히 [B-3](#b-3-pdf-원문-우선-검증-원칙))
- PDF가 있으면 다운로드 후 `pdftotext -layout` 으로 텍스트 추출
- 동영상·이미지 자료는 별도 OCR 또는 자막 확인 필요

### 3단계 — 본문 인용 일치

- 인용하려는 표현·수치가 원문에 **그대로** 등장하는지 직접 확인
- 단순 키워드 매칭이 아닌 **맥락 정합성**까지 확인
- "유사한 표현"이 있다고 인용하지 않음 — 정확히 일치하는 표현만 verbatim 인용

**한정자(qualifier) verbatim 유지**:
- "first natively tokenized bitcoin yield fund" → "최초 natively tokenized 비트코인 yield 펀드" (한정자 "natively tokenized" 유지)
- ❌ "업계 최초 비트코인 yield 펀드" — 한정자 누락 시 broad한 과장 주장 발생
- "first for any subsidiary of a Japanese financial institution" → "일본 금융기관 자회사 최초" (한정자 유지)
- ❌ "일본 최초" — 한정자 누락 시 부정확
- "first" / "only" / "largest" / "leading" 같은 superlative 표현은 항상 어떤 범위·조건에서 first인지의 한정자를 동반함. 한정자를 누락한 채 broad 변환하지 않음

### 4단계 — 위치 정보 라벨링

- PDF의 경우 페이지 번호 명시 (예: `C4 p.7`)
- 웹페이지의 경우 section/heading 명시 가능
- 검증 추적성을 높여 후속 리뷰가 빨라짐

### 5단계 — 시점 정합성

- 보고서 시점 (2023 vs 2024 vs 2025) 명확히 구분
- SEC 공시의 fiscal year vs calendar year 구분
- 보도자료 vs 정정 공시 구분 (특히 수치 변경 가능성)

**시점 확정 — metadata 직접 확인 절차**:

본문에 시점 명시가 없거나 "yesterday / last week" 같은 상대 표기일 때, HTML metadata에서 published date 추출:

```bash
# HTML 원본에서 published date metadata 추출
grep -ioE 'article:published_time[^>]{0,100}|datePublished[^,}]{0,100}' page.html | head -5
```

- `article:published_time` 또는 `datePublished` 속성이 가장 신뢰성 높은 시점 정보
- 사용자/외부 검토자의 "이 사건은 2024년이다" 주장과 metadata가 충돌하면 metadata 우선
- 케이스 예시:
  - Korea Herald "Published Jan. 29, 2026" metadata가 외부에서 "2024년 1월 발행"이라 주장한 hallucination 무효화
  - Ledger Insights `article:published_time` 2025-01-16이 "시점 미상"으로 둔 v1을 명확한 2025.1로 정정 가능하게 함

**본문 "Friday"·"Thursday" 같은 요일 표기와 published date 교차 검증**:
- 보도자료가 "on Friday published a disclosure"라 했고 published date가 2026.2.13이면 → 발행일은 그 주 금요일 또는 그 직전 금요일
- 2026.2.13은 금요일 → 발행일도 2026.2.13으로 추정 가능

---

## B-2. WebSearch summary 사용 금지 원칙

WebSearch 결과는 **발견 도구**일 뿐, **인용 출처가 아님**. WebSearch summary를 직접 검증 없이 보고서에 반영하면 잘못된 정보를 propagation할 위험이 큼.

### 실제 사례 — Hanwha ADFW 2025 케이스

WebSearch summary가 ADFW 2025 Strategic Partners를 다음과 같이 종합:

> "Strategic partners included Abu Dhabi Commercial Bank (ADCB), Abu Dhabi Investment Office (ADIO), ADI Foundation, Abu Dhabi Department of Economic Development (ADDED), Emirates NBD, First Abu Dhabi Bank (FAB), Mubadala, PGIM, SAAS Properties, UBS, and Solmate."

→ 직접 ADGM 공식 페이지 fetch 후 검증 결과, **UBS·HSBC·Standard Chartered·ADCB·FAB·Emirates NBD·PGIM 등이 본문에 등장하지 않음**을 확인. WebSearch summary가 다른 출처의 정보를 잘못 종합한 케이스.

### 원칙

- WebSearch는 **이런 1차 자료가 존재한다**는 발견 용도로만 사용
- 보고서에 인용하는 모든 사실은 **반드시 WebFetch 또는 직접 다운로드 후 원문 검증**
- WebSearch summary 표현을 그대로 보고서에 옮기지 않음

### 워크플로우

```
1. WebSearch로 후보 출처 발견
2. 후보 URL을 WebFetch 또는 직접 다운로드
3. 원문 본문에서 인용할 표현 직접 추출 (verbatim)
4. 추출한 표현으로 보고서 인용 작성
```

### 본 보고서 사용 출처 ≠ WebSearch에서 보이는 모든 정보

**케이스 예시 — Nomura/Boostry 컨소시엄 참여사**:

- WebSearch summary: "ibet for Fin' network is a consortium network founded by BOOSTRY, Nomura Securities, **SMBC Nikko Securities and 해외 증권사 F Securities**"
- 본 보고서가 사용한 출처 N9 (Boostry STO Report 공식) 본문 grep 결과: **SMBC·해외 증권사 F 미명시**
- 본 보고서가 사용한 출처 N10 (Hitachi 그린본드 공식) 본문 grep 결과: **SMBC·해외 증권사 F 미명시**
- → 보고서 본문에 "SMBC + 해외 증권사 F 컨소시엄 참여" 명시는 **외부 정보 차용에 해당**, "Nomura Securities 주도 컨소시엄"으로 축소해야 함

**원칙 강화**: WebSearch summary뿐 아니라 **WebSearch 결과의 발췌·요약에 등장하는 모든 사실**도, 본 보고서가 사용하는 N개 출처 본문에 등장하지 않으면 인용 금지. 사용 출처 본문 내에서 직접 grep으로 검증되지 않은 정보는 본 보고서의 근거 범위 밖.

**점검 절차**:
```bash
# 본문에 추가하려는 사실 (예: SMBC, 해외 증권사 F) 을 사용 출처 텍스트 전체에서 grep
grep -i "SMBC\|해외 증권사 F" sources/*.txt
# 미발견 시: 본 보고서 본문에서 해당 사실 제거 또는 축소
```

---

## B-3. PDF 원문 우선 검증 원칙

**웹페이지 본문과 PDF 원문이 다른 경우가 다수 존재**. 둘 다 있을 경우 PDF가 진실의 출처.

### 실제 사례 — Deloitte IWMC Ranking 2024

- 웹페이지 URL의 본문 텍스트에는 "4 dimensions" framework 미언급
- PDF 원문 p.7에는 verbatim 명시:
  > "Our study measures competitiveness across four dimensions: business environment, provider capability, stability, and tax and regulation."
- 첫 검증 시 웹페이지만 보고 "4기준은 출처에 없다"고 판단했다가 클라이언트 지적으로 PDF 재검증 후 확인

### 원칙

- 보고서·연구·백서 형태의 자료는 **반드시 PDF 본문 추출 후 검증**
- 웹페이지에 PDF 다운로드 링크가 있으면 PDF 우선 확인
- 출처 라벨에 PDF 페이지 번호 포함 (예: `[C4 p.7]`)

### PDF 본문 추출 표준 명령

```bash
# PDF 다운로드 후 텍스트 추출
pdftotext -layout source.pdf source.txt

# 인용 핵심 표현 grep
grep -in "인용 핵심구" source.txt | head -20

# 페이지별 추출 (페이지 번호 함께)
pdftotext -layout -f 1 -l 50 source.pdf - | head -200
```

---

## B-4. 본문 미확인 시 처리 — 3가지 옵션

웹페이지·PDF 본문에서 인용 표현이 확인되지 않으면 다음 3가지 옵션 중 택1:

### (a) 인용 자체 삭제 (가장 권장)

- 검증 불가한 인용은 보고서에서 완전 제거
- 빈 칸이 생기더라도 검증된 내용만으로 보고서 구성
- "본 보고서에서 별도 기재하지 않음"이라는 표기조차 가능한 한 회피 (보고서가 무엇을 제외했는지 설명하지 않음)

### (b) 다른 출처로 대체

- 동일 사실이 다른 1차 자료에 있으면 그 출처로 대체
- Tier 0/1로 대체 가능하면 우선
- 대체 후 새 출처 라벨로 재인용

### (c) Framing 전환 — 사실에서 분석으로

- 사실 인용이 어려우면 "본 보고서 분석" 또는 "본 보고서 framing"으로 명시 전환
- 이 경우에도 분석의 근거가 되는 1차 자료들은 함께 표기 ([B-5](#b-5-분석-합성-framework의-솔직-acknowledgement))

### 절대 금지

- 인용 표현을 임의 수정해서 본문에 있는 척 사용
- 추론을 사실로 위장
- "유사한 표현이 있다"는 이유로 verbatim 아닌 표현을 따옴표 안에 인용

---

## B-5. 분석 합성 framework의 솔직 acknowledgement

분류체계·평가 framework이 단일 1차 자료에 존재하지 않는 본 보고서의 분석 합성인 경우, **반드시 명시**.

### 명시 패턴 — 보고서 본문에 작성

```
"X 분류는 공식 규제 카테고리가 부재하여 단일 1차 자료에 존재하지 않음.
 아래 분류는 OCC R1 PB 정의(#T1)와 각 사업자의 라이선스·인가 사실
 (#C7 / #C17 / #C8 / #C3 / #C11)을 종합한 본 보고서의 분석 framework임."
```

### 분해 매트릭스 패턴

각 구성 요소의 출처 상태를 매트릭스로 명시 → 어느 부분이 1차 자료 유도 가능하고 어느 부분이 분석 합성인지 한눈에 구분:

| 항목 | 출처 상태 |
|---|---|
| ① 조건 A | T1 (OCC handbook) 유도 가능 |
| ② 조건 B | T1 유도 가능 |
| ③ 조건 C | 부분 유도 |
| ④ 조건 D | **순수 본 분석** — 1차 출처 없음 |

### 원칙

- 분석 합성을 1차 사실로 위장하지 않음
- "본 보고서의 분석 framework"이라는 표기는 메타 코멘트가 아닌 **출처 정직성의 일부** — 이건 본문에 노출해야 함
- 단, 검증 절차 자체(어떻게 verbatim 확인했는지 등)는 본문에 노출하지 않음 ([Part D](#part-d-메타-코멘트-노출-금지-원칙))

---

## B-6. 사실 / 추정의 명확한 분리

### 4단계 구분

| 유형 | 표시 방법 |
|---|---|
| **① 출처 직접 verbatim 인용** | Q() 블록 + 출처 라벨 (예: `Q("...", source_label='C4 p.7', source_url=...)`) |
| **② 출처 derive 사실** | 본문 + `[LABEL]` 하이퍼링크 |
| **③ 본 보고서 분석 합성** | "본 보고서의 분석 framework" 명시 + 근거 출처 다중 라벨링 ([B-5](#b-5-분석-합성-framework의-솔직-acknowledgement)) |
| **④ 추정·추론 시나리오** | 보고서 본문에 포함하지 않음. 별도 working note에만 기록 |

### 추정 정보 처리 원칙

- **클라이언트 보고서 본문에는 추정 시나리오 포함 금지**
- 추정이 필요하면 "본 보고서 분석" 형태로 명시 전환 후 근거 출처 다중 라벨링
- "→ 시장 분석 관점의 핵심 인사이트:" 같은 표시 어구로 분석/사실 명확 구분

### 추정 정보의 예시 (모두 보고서 본문에서 제외)

- "한화가 ADQ·Mubadala로부터 자금 유치 추정"
- "K-defense + K-culture 통합 펀드 시나리오"
- "신규 진입자가 X 모델을 차용할 가능성"
- "Y가 Z의 잠재 파트너 후보"

→ 위 모든 표현은 1차 자료 근거가 없으면 보고서에 등장하지 않음.

---

## B-7. 검증 자동화 도구 모음

### 웹페이지 텍스트 추출 + 키워드 검증

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0"
curl -s -A "$UA" -L --max-time 25 "$URL" | python3 -c "
import sys, re, html
raw = sys.stdin.read()
raw = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.DOTALL)
raw = re.sub(r'<script[^>]*>.*?</script>', ' ', raw, flags=re.DOTALL)
t = re.sub(r'<[^>]+>', ' ', raw)
t = html.unescape(t)
t = re.sub(r'\s+', ' ', t).strip()
# 인용한 핵심 표현·수치가 본문에 존재하는지 검증
for kw in ['핵심표현1', '핵심표현2', '수치']:
    print(f'{\"✓\" if kw in t else \"✗\"}  \"{kw}\"')
"
```

### PDF 본문 추출 + 키워드 검증

```bash
# 1. PDF 다운로드 (대용량 가능)
curl -s -o source.pdf "$PDF_URL"

# 2. 텍스트 추출 (layout 유지)
pdftotext -layout source.pdf source.txt

# 3. 라인 번호 함께 인용 검증
grep -in "핵심 표현" source.txt
grep -in "수치 패턴" source.txt

# 4. 특정 페이지 범위만 추출
pdftotext -layout -f 7 -l 7 source.pdf -  # 7페이지만
```

### DOCX 빌드 후 하이퍼링크 검증

```python
import zipfile, re

with zipfile.ZipFile('output.docx') as z:
    rels = z.read('word/_rels/document.xml.rels').decode()
    print('외부 하이퍼링크 수:', len(re.findall('TargetMode="External"', rels)))
    print('Unique URLs:', len(set(re.findall(r'Target="(https?://[^"]+)"', rels))))
```

### 본문 라벨 ↔ 출처 매칭 검증

본문에 등장하는 모든 `[LABEL]`이 출처 섹션에 정의되어 있는지 확인:

```python
from docx import Document
import re

doc = Document('output.docx')
all_text = '\n'.join(p.text for p in doc.paragraphs)
body_labels = set(re.findall(r'\[([A-Z]+\d+(?:\s+p\.\d+)?)\]', all_text))
print(f'본문 라벨 수: {len(body_labels)}')
print(body_labels)
# 출처 섹션과 수동 대조
```

---

## B-8. 외부 검토 의견의 1차 자료 재검증 — Hallucination 대비

Gemini·동료 검토 등 외부 검토 의견을 받아 정정 작업을 할 때도, **외부 검토자의 지적 자체를 1차 자료(본문)와 다시 대조**한 후에 행동. 외부 검토자도 hallucination·메모리 혼동·다른 문서와의 착각을 발생시킴.

### 실제 사례 — 해외 증권사 C docx Executive Summary 잘못된 지적

외부 검토(Gemini)가 다음과 같이 지적:
> "Executive Summary의 세 번째 문단 텍스트가 해외 증권사 C가 아닌 '증권사 B'의 리서치 결과로 잘못 기재되어 있습니다. 현재 문단 내용: '사업 영역별로는 리테일 브로커리지(③)·거래소(④)·커스터디(⑦)·디지털자산 자산관리 Solution(⑩)에서 명시적 Move 확인... 14개 모델 중 8개 직접 커버.'"

- 검증: v1~v8 모든 빌드 스크립트에서 해당 문구 `grep -n "사업 영역별로는 리테일 브로커리지"` 실행 결과 **미발견**
- 결론: 외부 검토자의 hallucination. 증권사 B 보고서 텍스트를 해외 증권사 C 문서로 잘못 인식
- 조치: 정정 보류, 사용자에게 검증 결과 보고

### 점검 절차

```bash
# 외부 검토가 "본문 X 위치에 Y 문구가 있다"고 주장하면 — 실제 본문에서 grep
grep -n "주장된 문구" build_docx.py
# 미발견 시: 외부 검토 hallucination 가능성 — 정정 보류, 사용자에게 검증 결과 보고
```

### 외부 검토 지적 처리 체크리스트

1. 지적된 본문 위치를 직접 grep으로 확인
2. 지적된 원문 출처가 본 보고서 사용 출처에 있는지 확인
3. 지적된 정정안의 사실 근거가 본 보고서 사용 출처 본문에 verbatim 있는지 확인
4. 위 3개 중 하나라도 fail이면 사용자에게 검증 결과 보고 후 정정 보류

### 원칙

외부 검토 의견은 정정 작업의 **단서일 뿐**, 검토자의 단어를 그대로 정정 명령으로 받지 않음. 본문 + 1차 자료 출처 본문 양쪽 grep으로 검증한 후 작업.

---

## B-9. 정정 작업 시 다중 인용 ripple grep — 빌드 사이클 최소화

동일 사실(시점·수치·고유명사·관계 등)이 보고서 여러 위치에 동시 등장하는 것이 일반적. 본문 한 곳만 정정하면 **다른 위치에 옛 값이 잔존**해 추가 정정·빌드 사이클을 유발.

### 다중 인용이 자주 발생하는 위치 5종

정정 시 반드시 함께 grep:

1. **본문 (각 항목 라벨+값 불릿)** — 가장 자주 변경되는 1차 위치
2. **Executive Summary** — 본문 사실을 prose로 요약 인용
3. **타임라인** — 시점 정렬용 짧은 라벨로 재인용 (예: "2023.11.16 Hitachi 그린본드 100억엔")
4. **매핑/커버리지 표** — 근거 셀에 본문 항목 번호와 짧은 키워드 인용 (예: "1.9 Boostry consortium chain (Nomura + SMBC + 해외 증권사 F 등)")
5. **합계·집계 라인** — 표 하단의 "✓ N개 커버 + ✗ M개 미확인" 같은 수치

### 실제 사례 — 해외 증권사 C docx 정정 작업 누적

| 정정 사실 | 본문 정정 시점 | 잔존 위치 발견 시점 | 빌드 추가 사이클 |
|---|---|---|---|
| Boostry NRI 합작 표현 제거 | v5 (본문 1.9) | v8 (타임라인 Phase 1) | v5→v8 (3 사이클) |
| Hitachi 100억엔 수치 제거 | v5 (본문 1.9) | v7 (타임라인 2023.11.16) | v5→v7 (2 사이클) |
| SMBC·해외 증권사 F 컨소시엄 표현 제거 | v6 (본문 1.9) | v7 (매핑표 ⑬ 근거 셀) | v6→v7 (1 사이클) |

→ 본문 정정 시 **5종 위치 일괄 grep**으로 한 번에 잡으면 모두 단일 빌드로 끝남.

### 점검 절차 — 표준 워크플로우

```bash
# 1) 정정 대상 키워드 (수치·고유명사·시점) 전체 위치 grep
grep -n "100억엔\|SMBC\|해외 증권사 F\|NRI 합작" build_docx.py

# 2) 발견된 모든 위치 일괄 수정 (sed 또는 Edit 다중 호출)
sed -i '' "s/100억엔//g" build_docx.py
sed -i '' "s/Nomura + SMBC + 해외 증권사 F/Nomura 주도/g" build_docx.py

# 3) 잔존 검증
grep -n "100억엔\|SMBC\|해외 증권사 F\|NRI 합작" build_docx.py
# 결과 비어야 OK

# 4) 잔존 없음 확인 후 빌드 1회
python3 build_docx.py
```

### 원칙

데이터 정정 작업은 본문 한 곳이 아닌 **그 데이터의 모든 인용 컨텍스트를 동시에 정정하는 것**까지가 한 단위. 모바일에서 docx 다운로드·확인 비용이 크므로 빌드 1회당 사용자 부담 큼.

---

# Part C. 실제 검증 실패 사례 — 교훈

각 사례는 작성 당시 발생한 검증 실패와 그 원인·교훈을 기록. 동일 패턴 재발 방지 목적.

## 사례 1. Capgemini 인구 비중 (90% / 9%) — 출처 없는 수치 인용

- **상황**: HNW 세분화 표에서 Millionaires Next Door 90% / Mid-Tier 9% 인구 비중 기재
- **검증 결과**: Capgemini World Wealth Report 2024 보도자료 본문에 해당 수치 미언급. Ultra-HNW 1%+ / 자산 34%+만 명시
- **원인**: 산업 통념을 출처 인용처럼 표기
- **교훈**: 산업 통념이라도 인용 라벨 옆에는 1차 자료에 명시된 수치만 기재. 산업 통념은 별도로 표기하거나 삭제.

## 사례 2. Deloitte 4기준 framework — 웹페이지만 보고 판단

- **상황**: Hub 경쟁력 평가 4기준 (business environment / provider capability / stability / tax and regulation)을 Deloitte 출처로 인용
- **검증 시도 1**: 웹페이지 본문에 4기준 미언급 → "출처 없음" 판단
- **검증 시도 2**: PDF 원문 p.7에서 verbatim 확인됨
- **원인**: 웹페이지 텍스트만 보고 PDF를 확인하지 않음
- **교훈**: 보고서·연구·백서 형태 자료는 반드시 PDF 본문 추출 후 검증. 출처 라벨에 페이지 번호 포함 (예: `C4 p.7`).

## 사례 3. Hanwha ADFW 2025 파트너 명단 — WebSearch summary 직접 인용

- **상황**: ADFW 2025의 Strategic Partner로 UBS·HSBC·Standard Chartered·ADCB·FAB·Emirates NBD·PGIM 등을 보고
- **검증 결과**: ADGM 공식 발표 원문 fetch 후 확인 결과 위 기업 다수가 본문에 등장하지 않음. WebSearch summary가 다른 출처의 정보를 잘못 종합
- **원인**: WebSearch summary 표현을 직접 검증 없이 보고서에 옮김
- **교훈**: WebSearch는 발견 도구일 뿐 인용 출처가 아님. 모든 인용은 WebFetch 또는 직접 다운로드 후 원문 검증.

## 사례 4. MarcyPen Asia $500M 펀드 규모 — Tier 3 매체 유일 출처

- **상황**: MarcyPen Asia JV의 펀드 규모를 $500M으로 보고
- **검증 결과**: BusinessWire 공식 보도자료에 펀드 규모 수치 미언급. Music Business Worldwide(Tier 3 매체)에만 $500M 등장
- **원인**: Tier 3 매체 수치를 1차 자료처럼 인용
- **교훈**: 정량 수치(특히 펀드 규모·계약 금액)는 Tier 0~1 1차 자료에 명시되지 않으면 보고서에 포함하지 않음. Tier 3 매체에만 있으면 "별도 기재하지 않음" 처리.

## 사례 5. 한화 ADFW "처음 참여" / "한국 기업 중 유일" — 본문 미확인 framing

- **상황**: 한화가 ADFW에 "Premier Partner로 처음 참여 / 한국 기업 중 유일"이라고 보고
- **검증 결과**: 5개 1차 자료 어디에도 "first time" / "한국 기업 중 유일" 표현 미언급
- **원인**: 추정·해석을 사실로 기재
- **교훈**: 형용사적 framing(최초·유일·압도적 등)은 1차 자료 명시 확인 없이 사용 금지.

## 사례 6. Custodia Fed 거부 날짜 — 2023.3 vs 2023.1.27

- **상황**: MD 파일에 Custodia Bank의 Federal Reserve 가입 거부 날짜를 "2023.3"로 기재
- **검증 결과**: Federal Reserve 공식 press release dateline은 "2023-01-27" verbatim
- **원인**: 메모리 기반 추정 또는 다른 사건과 혼동
- **교훈**: 모든 날짜는 1차 자료 dateline으로 직접 확인. 추정 금지.

---

# Part D. 메타 코멘트 노출 금지 원칙

**검증의 흔적은 작성 과정에 남기고, 보고서 본문에는 검증의 결과(=확인된 사실)만 노출**. 보고서를 받는 클라이언트는 작성자의 검증 절차가 아닌 최종 사실관계와 분석을 필요로 함.

## 보고서 본문에 노출 금지 표현

| 금지 표현 | 사유 |
|---|---|
| "verbatim 확인" / "verbatim으로 명시됨" / "1차 자료 verbatim" | 검증 절차는 작성자의 내부 작업 |
| "본 보고서는 ... 검증된 사실만으로 구성" | 보고서가 사실에 기반한다는 것은 default |
| "본 보고서는 ... 의도적으로 제외함" | 무엇을 제외했는지 보고서가 설명하지 않음 |
| "원문 verbatim:" / "본문 verbatim:" 같은 인용 도입부 | 단순히 "PR Newswire:" / "ADGM:" 등 출처명만 기재 |
| "추정·추론 시나리오 제외" | 보고서에는 검증된 사실만 등장하므로 명시 불필요 |
| "1차 자료에서 미언급" / "본 보고서 미반영" | 없는 정보를 굳이 언급하지 않음 |
| "양 출처에서 동일 표현 등장" / "두 출처에 verbatim 명시" | 인용 옆 출처 라벨로 충분 |
| 표 컬럼 헤더 "(공지 사실)" / "(검증됨)" 같은 라벨 | 표 내용이 사실이라는 것은 default |
| 헤더의 "— 1차 자료 verbatim" / "— 본 보고서 분석" 같은 메타 라벨 | 헤더는 콘텐츠 주제만 명시 |

## 본문에 노출 OK인 출처 정직성 표기

| 허용 표현 | 사유 |
|---|---|
| "본 보고서의 분석 framework" | 분석 합성을 1차 사실로 위장하지 않기 위한 명시 |
| 출처 라벨 (`[C4 p.7]`) | 검증 추적성 확보 |
| Q() 인용 블록의 verbatim 따옴표 | 원문 표현임을 시각적으로 구분 |
| "구체 수치는 본 보고서에서 별도 기재하지 않음" (제한적) | 클라이언트가 명백히 기대할 만한 수치가 없을 때 한해서 |

## 핵심 원칙

- **작성 과정의 작업 단계는 작업 노트에만**
- **클라이언트 보고서 본문에는 사실 + 분석만**
- **출처 정직성 = 라벨링 + 분석 합성 명시** (= 메타 코멘트 ≠)

---

## 본 가이드의 활용

- 새 보고서 작성 시작 전 Part A 원칙으로 출처 풀 설계
- 작성 중 Part B 워크플로우로 각 인용 검증
- 작성 완료 후 Part D 체크리스트로 메타 코멘트 final cleanup
- 검증 실패 사례는 Part C에 누적 기록하여 동일 패턴 재발 방지

---

# Part E. 산출물 빌드·동기화 + 추가 검증 교훈 (인도네시아 리서치 세션, 2026.6)

## E-1. 링크 접속성 — 에러코드 ≠ 죽은 링크

자동 fetch(WebFetch/기본 curl)의 403/500/000은 **봇 차단·JS 대시보드·CDN 보호**인 경우가 대다수로, 브라우저에서는 정상 접속됨.

- **점검**: 브라우저 User-Agent를 붙인 curl로 재확인(`--http2` 제거가 도움 되는 경우 있음). 그래도 막히면 사용자에게 "브라우저로 열림" 확인 요청.
- **죽은 링크 판정**: 404 또는 어떤 방법으로도 도달 불가일 때만.
- **사용자 브라우저가 진실**: 사용자가 "열린다"고 하면 그게 맞음(curl 코드는 참고만). 자동 점검 결과만 보고 멀쩡한 링크를 삭제하지 말 것 — 본 세션에서 datatopics(WB)·McKinsey `.ashx`를 500/000만 보고 지웠다가 사용자 지적으로 복원함.
- **봇 차단 사이트**(IMF·McKinsey·gfmag·tandfonline 등)는 deep-link가 막히면 접속 가능한 미러·요약(databoks 등)·공개 PDF를 병기.
- **모든 인용 링크는 게재 전 접속 가능 검증** (Part A-1 #6 · B-1 1단계).

## E-2. WebSearch summary 오염 — 재확인 (Part B-2 보강)

본 세션에서도 반복 확인: WebSearch summary가 **명시한 출처에 없는 내용**을 종합함.
- CSIS 기사에 없는 "$1.4B→$34.8B·신디케이션", Bain 페이지에 없는 인니 수치, Jakarta Post에 없는 economist 인용 등 다수.
- **원칙**: WebSearch가 "출처 X가 Y라 했다"고 요약해도, **반드시 X 원문 fetch 후 Y가 본문에 있는지 grep**. 없으면 인용 금지·축소.

## E-3. 자동 리서치(deep-research 워크플로우) 출력도 출처 재대조

워크플로우의 검증 통과 claim도 **출처 귀속이 틀릴 수 있음**. 본 세션 실제 오귀속:
- DHE SDA → East Asia Forum(없음; 실제는 UNCTAD/PP 8/2025)
- KB/Woori 수치 → Korea Times(없음; 실제는 asiae)
- 대형은행 ROE → TABInsights(ROE 없음, ROAA/NIM만)
- Shinhan 2024 ROA → CEIC(2019까지만; 실제는 학술 논문)

**원칙**: 합성 결과의 모든 claim에 대해 인용 출처 본문을 직접 grep하여 귀속을 재확인. 워크플로우는 발견·초안 도구이지 최종 검증이 아님.

## E-4. 단위 표기 — 영어식 + 자릿수 검증 + 렌더러 충돌

- 한글 억/조 변환에서 **10배 오류** 빈발(원문 "420 billion" → "Rp420억"=Rp42B). 통화·대규모 수치는 **영어식 B/T**로 직역하고 영어 원문 magnitude와 대조.
- 마크다운에서 `$`는 MathJax/KaTeX 충돌 → 본문 `$`는 `\$`로 escape(코드 span 안은 `$` 그대로). 인용 태그는 **HTML 앵커**(`<a href="#R1">[R1]</a>`)로 — escape 방식 `[\[R1\]](#R1)`의 `\[ \]`는 LaTeX 디스플레이 수식으로 깨짐.
- 통화기호 일괄치환 시 부작용 주의: `US$`→`USD `가 `USUSD`로 깨진 사고 있었음.

## E-5. 실적 vs 전망 명시

성장률·전망 표에서 **아직 오지 않은 연도(전망)**와 실적을 섞으면 오해. 구분 행(실적/추정/전망) 또는 마커로 명시. (IMF Article IV 표: 직전연도까지 실적·당해 추정·이후 전망.)

## E-6. 외부 검토 의견 — 선별 반영 (Part B-8 보강)

다른 Claude Code 인스턴스의 정합성 검토(고품질)도 **본문·1차 자료와 재대조 후 선별 반영**. 본 세션: 외부 검토의 "PDF 확인 권고"(C1 45M/135M·C2 3.7%) 중 **이미 로컬 PDF 추출로 직접 검증한 항목은 caveat 미반영**. 맞는 지적(A2 제목 부제·"16위" R1→R2 귀속·N5 국가목록 한정)만 반영. 무조건 수용 금지.

## E-7. 사용자 수동 편집 보존 — diff 교차검증

스크립트로 생성한 산출물(docx 등)을 사용자가 직접 편집한 뒤 재빌드할 때 **수동 편집을 잃지 않도록**:
- 편집본 텍스트 추출 → 스크립트 출력과 **diff**로 모든 변경 식별(수동 스캔은 표 헤더 단축 같은 미세 편집을 놓침; diff가 잡음).
- 식별된 사용자 편집을 빌드 스크립트에 반영 후 재빌드 → 재-diff로 "의도된 변경만 남는지" 확인.

## E-8. 라벨 번호 위생 + 결번 정리

출처 라벨(R/C/A/N/O) 결번(R8·N9 누락 등)은 재번호로 정리. 본문 인용·앵커·검증메모의 cross-reference를 ripple grep으로 일괄 갱신(Part D-9 / docx 가이드 §9 ripple grep과 동일).

## E-9. Tier 라벨 — 작업 마스터 vs 클라이언트 산출물

- **작업 마스터(.md 리서치 노트)**: Tier 라벨 유지(출처 정직성·추적성).
- **클라이언트 산출물(docx/xlsx)**: Tier 등 메타 라벨 **제거**(docx 가이드 §7). 출처 코드(R1 등)는 클릭 인용용으로 유지.

## E-10. 콘텐츠 중복 제거 + '비약 트리거' 정의

- 같은 사실이 두 섹션(예: Bull vs 비약 트리거)에 중복되면 **한 곳에만**.
- "비약적(비선형) 성장 트리거" 섹션에는 **외생 충격으로 급격히 확대될 수 있는 것만**(자원 지정학·결제 허브 등). 점진적·구조적 성장 동력(자원 다운스트리밍 등)은 일반 Bull로 분류.

## E-11. PDF 도구 미설치 환경 대응

`pdftotext` 미설치 환경 → WebFetch의 PDF→텍스트 변환, 또는 curl 다운로드 후 로컬 추출(파이썬). PDF 전용 수치는 접속 가능 미러로 교차확인.

## E-12. 산출물 분리·버전·하이퍼링크

- **표 안 출처도 클릭 가능 하이퍼링크**로(라벨별). 백틱 URL은 클릭 불가.
- 산출물은 덮어쓰기 금지·새 버전 발행. 같은 주제는 전용 폴더, 원자료·스크립트는 `_raw_sources/` 분리.
