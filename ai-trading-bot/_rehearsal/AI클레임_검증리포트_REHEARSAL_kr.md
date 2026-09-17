# fact-check 검증 리포트 — REHEARSAL kr

> **검증일** 2026-09-03 · **대상** `analysis/AI클레임_REHEARSAL_kr_작업.md` (원 산출물:
> `analysis/분석노트_REHEARSAL_kr_v1_0.md`) · **모드** 간이(클레임 1건 < 10건)
> Phase 2 체인 리허설의 검증 단계.

## 판정 요약

**MISMATCH 0건 · STALE 0건 · 🔴(비가역) 0건**

| # | 클레임(위치) | 판정 | 심각도 | 출처 티어 | 비고 |
|---|---|---|---|---|---|
| 1 | (L26) HBM은 DRAM 다이를 수직 적층하고 TSV(실리콘 관통전극)로 연결해 대역폭을 끌어올린 메모리 규격 | **MATCH** | ⚪ | **T1** (제조사 공식) | 캐시(T2)가 못 덮던 TSV 요소를 T1으로 새로 확인·승격 |

## 상세

### 1. HBM 정의 — MATCH (T1 승격)

**클레임**: "HBM(High Bandwidth Memory)은 DRAM 다이를 수직 적층하고 TSV(실리콘 관통전극)로 연결해 대역폭을 끌어올린 메모리 규격이다." (분석노트 L26)

**캐시 대조 결과 — 부분 커버**: 검증캐시의 `HBM3E` 항목(2026-09-03 확인, 매칭 1.00)은
"D램 수직 적층으로 대역폭 확대"까지만 담고 있어 **수직 적층·대역폭은 뒷받침하나 `TSV로 연결`은
뒷받침하지 않았다.** 캐시 적중이 곧 면제가 아니라는 규칙대로, 미커버 요소를 별도로 조달했다.

**T1 확인**: 삼성전자 반도체 공식 HBM 페이지에서 다음 문장을 확인했다 —

> "High Bandwidth Memory (HBM) consists of multiple memory dies stacked vertically and
> interconnected through TSVs (Through-Silicon Vias)."
> — [Samsung Semiconductor — HBM](https://semiconductor.samsung.com/dram/hbm/) (확인 2026-09-03)

세 요소가 모두 일치한다: ① 수직 적층(`stacked vertically`) ② TSV 연결
(`interconnected through TSVs (Through-Silicon Vias)`) ③ 고대역폭(제품명 및 캐시의 "대역폭 확대").
"메모리 규격"이라는 서술도 HBM이 JEDEC 표준이라는 점에서 과장이 아니다.

**출처 위계 조치**: load-bearing 정의이므로 캐시의 T2 출처
([뉴스토마토](https://www.newstomato.com/ReadNews.aspx?no=1305417), 접속 200 확인)를
**T1(제조사 공식)으로 승격**했다. 두 출처는 서로 모순되지 않는다.

**방법론 각주(정직 표기)**: 삼성 공식 페이지는 JS 렌더링(SPA)이라 `curl` raw 본문에 해당 문구가
없었다. 'raw에 없음 = 사실 없음'으로 단정하지 않고 WebFetch로 교차확인해 위 문구를 얻었다.
즉 이 인용은 **raw grep이 아니라 WebFetch 경유**다 — 정의성 진술이고 독립된 T2와도 일치해
판정에 충분하다고 보았으나, 이 사실만은 인용 경로를 밝혀 둔다.

## 접속성

| 링크 | 상태 | 비고 |
|---|---|---|
| newstomato.com/ReadNews.aspx?no=1305417 (캐시 출처) | ✅ 200 | — |
| semiconductor.samsung.com/dram/hbm/ (T1) | ✅ 200 | SPA — 본문은 WebFetch로 확인 |
| product.skhynix.com/products/dram/hbm.go | ✅ 200 | 교차 후보(미사용) |
| jedec.org/…/jesd235d | 🔵 403 | **봇 차단**(사람은 열림 추정) — 유료 아님. 미사용이라 영향 없음 |

## 교정 지시서

**교정할 항목 없음.** 분석노트 L26은 그대로 두어도 된다.

권고(선택, ⚪): 노트의 `[AI]` 줄에 출처를 붙이면 다음 run에서 재검증 비용이 줄어든다.

- **위치**: `analysis/분석노트_REHEARSAL_kr_v1_0.md` L26, 검색 문자열 `` `[AI]` HBM(High Bandwidth Memory)은 ``
- **현재**: `` `[AI]` HBM(High Bandwidth Memory)은 DRAM 다이를 수직 적층하고 TSV(실리콘 관통전극)로 연결해 대역폭을 끌어올린 메모리 규격이다.``
- **권고**: 문장 끝에 ` ([삼성전자 공식](https://semiconductor.samsung.com/dram/hbm/), 확인 2026-09-03))` 부착

## 이용 전 재확인 목록

**없음.** 이 클레임은 기술 규격의 정의로 시효성이 낮다(세대 추가는 있어도 적층·TSV 구조 자체는
바뀌지 않는다). ⏳ 항목 0건.

## 검증 메모 (원문에 붙일 용도)

> 2026-09-03 fact-check: `[AI]` 1건 검증 완료 — MATCH. HBM의 TSV 적층 구조를 제조사 공식(T1)에서
> 확인해 캐시의 T2 출처보다 상위로 승격. MISMATCH·STALE·🔴 각 0건.

---

## 종료 ledger

| 단계 | 수행 | 증빙 |
|---|---|---|
| 1. 추출(URL + 클레임 + 위치) | ☑ | 클레임 1건(L26) · 작업지시서 내 URL 1건 |
| 1-bis. 무출처·링크누락 식별 | ☑ | 무출처 1건 → T1 부착 권고로 처리(⚪) |
| 1-ter. 검증 캐시 대조 | ☑ | 캐시 히트 1건(매칭 1.00) — **부분 커버 판정**, 미커버 요소(TSV)는 신규 조달 |
| 2-bis. 병렬 조달 | N-A | 클레임 10건 미만 |
| 2. 접속성 | ☑ | 접속가능 3 / 봇차단 1(jedec, 미사용) / 페이월 0 / 죽은링크 0 |
| 3. 내용 일치 | ☑ | MATCH 1 · MISMATCH 0 · STALE 0 · UNVERIFIABLE 0 |
| 3-bis. 시효 2축 | ☑ | 시효 있는 사실 0건 (기술 규격 정의) |
| 3-ter. 출처 위계 | ☑ | load-bearing 1건 → **T1까지 승격 완료**. T3만 근거인 것 0건 |
| 3-qua. 계획 내적 정합성 | N-A | 일정·예산 산출물 아님 |
| 실패패턴 점검 | ☑ | SPA 값 추출 실패를 '부재'로 단정하지 않고 WebFetch 교차확인 · 봇차단(403)을 페이월로 오기하지 않음 |
| 4. 심각도 분류 | ☑ | 🔴 0 / 🟡 0 / ⏳ 0 / 🔵 1(jedec 봇차단, 미사용) / ⚪ 1(출처 부착 권고) |
| 5. 산출물 | ☑ | 이 파일 + 임시파일 `_raw_sources/_tmp.html` 삭제 |
