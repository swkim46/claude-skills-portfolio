# AI 인사이트 자동매매 (모의투자)

> **포트폴리오 공개본.** 실계좌 잔고·저널·시세 스냅샷·분석노트·시그널은 제외했고, `_rehearsal/`의 합성 데이터와
> 회귀 테스트(`test_risk_guard.py`)만으로 동작을 확인할 수 있다. 원본 운영 저장소는 비공개다.

뉴스레터·공시·시세를 Claude가 읽고 **제안**하면, 결정론 Python이 **검증·집행**한다.
설계 근거 조사(AI 자동매매 공개 사례 성능 조사)는 비공개 내부 문서다 — 결론은 「안전 장치」 절의 인용문 참고.

> **이 시스템의 성공 지표는 수익률이 아니다.** ① 규율 집행률(손절 100% 집행)
> ② 무사고(의도치 않은 주문 0) ③ 벤치마크 병기 기록의 완전성 — 이 셋이다.
> 조사에서 확인된 사실: 비용 차감 후 LLM 초과수익의 공개 근거는 없고, 하락장에서는
> 전 LLM 에이전트가 buy-and-hold에 미달했다. 기대하는 것은 알파가 아니라 규율이다.

---

## 멈추는 법 (먼저 읽을 것)

```bash
touch config/KILL                                   # 전면 중지 (파싱 이전에 판정)
rm config/KILL                                      # 재개
launchctl bootout gui/$(id -u)/com.example.trade-kr     # 자동 실행 자체를 끔 (3단계 이후)
```
연속 실패 3회면 시스템이 **스스로 KILL을 만든다.** 그 경우 원인을 확인한 뒤 사람이 지워야 재개된다.

## 현황 보기

```bash
python3 dashboard.py --open      # 맥에서 바로 열기
```
성공지표 3종(규율 집행률·무사고·기록 완전성)이 이 페이지의 본론이다 — 수익률이 아니다.
폰에서 보려면 비공개 Artifact로 게시한다(주소는 `config/.dashboard.json`).

## 운영 방식: 수동 (2026-09-08 결정)

**돌리고 싶을 때 사람이 부른다.** 예약 실행(launchd)은 걸지 않는다.

이 노트북은 항상 전원에 연결돼 있지 않고, 예약 실행은 잠자기 중에는 정시에
돌지 않고 **다음에 깨울 때 밀려서 한 번 실행**된다. 아침 09:35 것이 오후에 깨면서 나가면
상황이 달라진 시장에 주문하는 셈이라, 안 도는 것보다 나쁘다. 특히 미국장(23:50 KST)은
거의 항상 잠들어 있는 시각이다.

**무인화는 별도 상시 가동 서버로 미룬다.** 그때까지 `run_auto.py`는 유지한다 — 서버에
올릴 진입점이 그대로 이 스크립트이고, 지금도 손으로 부르면 전 과정을 돈다.

```bash
python3 run_auto.py --self-test            # 환경 점검(주문·LLM 없음)
python3 run_auto.py --market kr --no-send  # 전 과정, 주문만 안 보냄
python3 run_auto.py --market kr            # 실제 주문까지 (자체 claude -p 세션에서 분석)
```
시각 기준: 국내 09:35(+11:05) · 미국 23:50(+00:50). 미국 23:50은 서머타임과 무관하게
장중이라(EDT 10:50 / EST 09:50 ET) 시각 조정이 필요 없다. 세션 날짜는 **시장 현지 날짜**다.

**집행은 LLM 세션 밖에 있다.** `claude -p`는 분석노트·시그널·게이트까지만 하고,
`risk_guard`·`execute`·`journal`은 `run_auto.py`가 직접 부른다 — "LLM은 제안만"을
문서가 아니라 프로세스 경계로 강제하기 위해서다.

### 서버로 옮길 때 필요한 것 (미리 적어둠)

- 증권사 API가 해외 IP를 막는지 확인 (국내 VPS면 무관)
- 옮겨야 할 비밀정보: KIS 키(`.env`) · Gmail 앱 비밀번호 · Claude 인증
- 상시 가동이면 예약이 밀릴 일이 없으므로 재시도 슬롯(11:05·00:50)의 존재 이유가 줄어든다
- 옮기기 전에 이 맥에서 수동으로 여러 번 돌려 outcome 분포를 봐 둘 것

## 매일 하는 일

Claude에게 **`/trade-run kr`** 이라고 하면 아래를 순서대로 밟는다(스킬이 오케스트레이션).
집행 전에 반드시 사람 확인을 한 번 받는다. 손으로 돌리려면:

```bash
cd <REPO>/ai-trading-bot

python3 ingest.py --market kr --with-news --days 1   # 재료·스냅샷(+DART 공시)
# → Claude가 material_*.md를 읽고 분석노트 + signal_*.json 작성
python3 extract_ai_claims.py analysis/분석노트_YYMMDD_kr_v1_0.md      # [AI] 클레임 추출
python3 ../gmail-newsletter-analyzer/cache_claims.py split <위 출력 경로>
# → 생성된 *_작업.md로 fact-check 실행 → verdict를 시그널에 반영
python3 risk_guard.py signals/signal_YYMMDD_kr.json --snapshot data/snapshot_YYMMDD_kr.json --live
python3 execute.py signals/approved_YYMMDD_kr.json           # dry-run으로 먼저 확인
python3 execute.py signals/approved_YYMMDD_kr.json --send --note <분석노트>   # 전송 + 체결 확정(fill)까지
python3 journal.py --daily --market kr                       # 반드시 마지막에
python3 account_status.py --market kr --stamp YYMMDD --refresh   # ★ 보고 마지막 블록: 시장별 주식:현금·수익률 + 합산 투자비중
```

**돈은 입금된 것 안에서 알아서 한다(2026-09-16).** 그 시장 현금이 모자라면 `risk_guard`가 깎고(같은 시장
대기 논지 몫은 남긴다), 그래도 모자라면 최대 보유를 회전 매도해 메운다 — 사람에게 환전·입금을 묻지 않는다.
**손절·목표 가격 조건은 장중 터치 즉시 규율 매도**로 나가고, run 사이는 `python3 stops.py --market auto --send`
(루틴 `*/20 0-4,9-15,22-23 * * 1-5`)가 20분마다 본다. 접수된 주문은 `fill.py`가 체결·취소·만료로 확정한다.
**지정가는 전송 직전 최신 시세로 갱신**(국내·해외, 불리한 쪽만, 수량은 승인 금액 안; 승인가 대비 2% 넘게 달아나면 미전송)하고,
미체결은 정정 → (정정이 거부되거나 대기 초과면) **취소 확정 뒤 최신가로 1회 재발주**한다(2026-09-17). 살아 있는 주문은 다시 보내지 않는다.

`journal.py`가 `position_peaks.json`을 갱신하고 트레일링 스톱이 그걸 읽는다.
**저널을 건너뛰면 트레일링이 멈춘다** — 매 run 마지막에 반드시 돌린다.

**run이 어디서 끝나든(체결·기권·장외) 보고의 마지막은 `account_status.py` 출력이다(2026-09-17).** 시장별
자산·현금·투자·주식:현금·당일/누적 수익률·벤치마크 대비와 포트폴리오 합산 투자비중(vs 목표 60%/하한 20%)을
`journal/equity_curve.jsonl`에서 만들어 `data/run_evidence/account_status_<stamp>_<mkt>.md`로 남기고, `execute`·`no_trade`
게이트가 그 파일을 본다. `--refresh`는 양 시장 `journal --daily`를 먼저 돌린다.

주간(금요일): `python3 journal.py --weekly`

### 급할 때

```bash
touch config/KILL     # 즉시 전면 중지. risk_guard·execute 양쪽이 파싱 이전에 판정한다.
rm config/KILL        # 해제
```

---

## 안전 장치 — 무엇이 주문을 막는가

`limits.json`은 **운영자가 정한다.** risk_guard가 이 파일의 sha256을 approved에 찍고,
execute가 집행 직전 다시 대조한다. 승인 이후 리밋을 풀면 그 approved는 무효가 된다.

두 종류를 구분한다. **무결성 검사는 항상 켜져 있고**(끄는 스위치가 없다), **투자 판단 규칙은
`limits.json`에서 켜고 끈다**(`null` = 꺼짐).

### 항상 켜짐 — 무결성 (아무도 의도하지 않은 주문을 막는다)

| 층 | 막는 것 |
|---|---|
| `config/KILL` | 파일 존재만으로 전면 중지 (JSON 파싱에 의존하지 않는 신호) |
| 스키마·신선도 | 필드 누락·타입 오류·3시간 초과 → **부분 구제 없이 run 전체 거부** |
| 유니버스 | watchlist ∪ 보유 밖의 티커는 주문 불가 — 뉴스 본문발 인젝션의 유일한 통로 |
| long-only | 미보유 종목 SELL 거부 (공매도 불가) |
| MISMATCH 근거 | fact-check가 틀렸다고 판정한 근거가 붙은 제안은 거부 |
| 예수금 미확인 | 조회 실패 시 0원으로 뭉개지 않고 **매수만 보류**(매도는 통과) |
| 집행 직전 재검증 | `limits.json` 해시 대조 + approved 30분 신선도 — 리밋을 푼 뒤 옛 승인 재실행 차단 |

### 현재 프로필: **minimal** (2026-09-07 — 소액 운용 전제)

> 아래 표는 2026-09-07 프로필의 스냅샷이다. 현재값의 정본은 `config/limits.json`이다(예: `per_position_max_pct: 12`, `daily_max_order_pct_of_equity: 20`).

| 규칙 | 현재 | 되살리려면 |
|---|---|---|
| **기계적 손절** | **OFF** — 자동 매도 안 함 | `stop_loss_pct: -7.0` |
| 트레일링 스톱 | OFF | `trailing_stop_from_peak_pct: -10.0` |
| 서킷브레이커 | OFF | `portfolio_daily_loss_halt_pct: -3.0` |
| 종목당 비중·종목 수·현금 하한 | OFF | `per_position_max_pct` 등에 숫자 |
| confidence 하한·T1/T2 근거 필수 | OFF | `min_confidence`, `require_match_t1t2_for_buy` |
| **일일 주문금액 상한** | **자산의 20%/일** (`daily_max_order_pct_of_equity`) | ← **실질 브레이크. 끄지 말 것** |
| 하락 보유 알림 | −10% 이하면 재료에 '⚠️ 주의' 표기 | `soft_alert_pct` |

**손절을 껐다는 것은 눈을 감았다는 뜻이 아니다.** −10% 이하로 빠진 보유는 재료에 '주의'로 뜨고,
분석 단계에서 *논지가 훼손됐는지*를 보고 SELL을 제안하거나 왜 계속 보유하는지 노트에 적는다.
파는 판단이 기계에서 분석으로 옮겨간 것이다.

> 참고: 이 프로젝트의 근거 조사(비공개)가 확인한 것은
> "LLM 종목선택의 초과수익 근거는 없고, 자동화의 실증된 효용은 기계적 손절 집행"이었다.
> 손절을 끄면 그 효용이 빠지고 남는 것은 근거 정리와 편의다 — 소액이라는 전제 위에서의 선택.

회귀 검사: `python3 test_risk_guard.py` (20건, 네트워크 불필요 — 규칙별 테스트는 그 규칙을
명시적으로 켜서 돌리므로 `limits.json`을 튜닝해도 깨지지 않는다)

---

## Phase 0 — 착수 전 체크리스트

### 계좌·키

- [ ] 한국투자증권 **비대면 계좌 개설** (앱)
- [ ] [KIS Developers](https://apiportal.koreainvestment.com/intro)에서 Open API 서비스 신청
- [ ] **모의투자 신청** 후 paper 앱키/시크릿 발급
- [ ] 저장소 루트 `.env`에 기입(`.env.example`을 복사 — 두 프로젝트가 같은 파일을 읽는다):
      ```
      KIS_PAPER_APP_KEY=...
      KIS_PAPER_APP_SECRET=...
      KIS_PAPER_ACCOUNT=00000000-01
      ```
- [ ] `python3 kis_client.py` 실행 → 토큰·현재가·잔고 3건이 도는지 확인

### 실측해서 이 표를 채운다

| 항목 | 문서상 (2026-09-03 공식 저장소) | **실측 (2026-09-07)** |
|---|---|---|
| 토큰 발급 | 24h 유효, 재발급 빈도 제한 | ✅ OK (길이 346, 캐시 동작) |
| 국내 시세·잔고 | `FHKST01010100` / `VTTC8434R` | ✅ OK |
| 해외 시세 | `HHDFS00000300` | ✅ OK (AAPL 조회 성공) |
| 모의 해외 잔고 | `VTTS3012R` | ✅ OK |
| 모의 해외 현재잔고 | `VTRP6504R` | ✅ OK — **예수금 필드 `frcr_dncl_amt_2` 확정** |
| **모의 REST 호출 제한** | 낮음(`EGW00201`) | ⚠️ **0.35초 간격에서 2번째 호출 실패.** → `paper 0.8s`로 상향 + EGW00201 백오프 재시도(GET만) → 연속 5건 6.6초에 통과 |
| 모의계좌 초기 자금 | (미확인) | ✅ **₩10,000,000** |
| **외화 예수금**(`frcr_dncl_amt_2`) | — | USD 0 — 단 아래 참조 |
| **해외 주문가능금액**(`VTTS3007R`) | — | ✅ **USD 100,000** — 모의계좌는 외화가 별도 시딩돼 있어 **환전 없이 미국 주문 가능**(AAPL @320 기준 309주) |
| **환전 API** | — | ❌ **저장소에 환전 TR 없음(0건).** 코드로 환전 불가 → 실전 전환 시 환전은 **사람이 앱/HTS에서** (위탁계좌 USD 실시간 환전 평일 00:10~06:00·09:00~23:30, 토·공휴일 불가) |
| 모의 미국 주문 | `VTTT1002U`/`VTTT1001U`, **지정가만** | ⬜ 미실행 (실제 주문은 리허설 후에) |
| 모의 해외 **예약주문** | `VTTT3014U` 등 paper TR 존재 | ⬜ 미확인 |
| 모의 **주간거래** | 모의 TR 없음 → 미지원 추정 | ⬜ 미확인 |

미국 예약주문이 모의에서 안 되면, 미장 개장(22:30/23:30 KST)까지 깨어 있을 수 없으므로
**미국은 "기록 모드"로 시작**한다 — 제안·검증·저널만 남기고 주문은 보내지 않는다.

### 완료 판정 — ✅ 통과 (2026-09-07)
paper 키로 토큰 → 국내 현재가 → 잔고 조회 성공. `ingest.py` 실계좌 실행으로 재료·스냅샷 생성,
DART 공시 수집, 벤치마크 기준점(KODEX200 @110,820 / equity ₩10,000,000) 기록까지 확인.

---

## 파일 구조

| 경로 | 역할 |
|---|---|
| `kis_client.py` | KIS REST 클라이언트 (stdlib only). 기본이 모의, 실전은 `allow_real=True` 필요 |
| `ingest.py` | 잔고·시세·공시 → `data/snapshot_*.json` (기계용) + `data/material_*.md` (Claude용) |
| `dart_feed.py` | DART 공시 피드. 키 없으면 **'미수집'으로 명시**(≠ 공시 없음) |
| `extract_ai_claims.py` | 분석노트 → `cache_claims.py`가 읽는 번호 목록 형식으로 `[AI]` 추출 |
| `risk_guard.py` | **결정론 리스크 레이어.** 네트워크·LLM 호출 없음 — 그래서 오프라인 전건 테스트 가능 |
| `execute.py` | 주문 집행. dry-run 기본, `--send` 필수. 집행 전 리밋 해시·나이 재검증 |
| `journal.py` | equity 곡선 + 그림자 벤치마크 + 고점 기록 + 주간 리포트 |
| `test_risk_guard.py` | 거부 케이스 15종 회귀 검사 |
| `config/limits.json` | 하드 리밋 — **운영자 규칙** |
| `config/watchlist.json` | 종목 유니버스 — AI 제안(`watchlist_candidates`) + 사람 승인제 |
| `signals/_sample_signal.json` | 시그널 JSON 형식 견본 |

스냅샷(기계용)과 재료(사람용)를 파일로 분리한 이유: risk_guard는 뉴스를 읽으면 안 되고
(결정론이어야 하므로), Claude는 잔고 숫자를 고쳐 쓰면 안 된다(제안만 해야 하므로).
경계를 코드 구조로 강제한다.

---

## 진행 상황

- [x] **Phase 1 — 뼈대** (결정론 레이어, LLM 없이): 모듈 + 거부 케이스 **17종 통과**
- [x] **Phase 2 — 분석 체인** (키 없이 가능한 범위 완료, 2026-09-03)
      - `trade-run` 스킬 신설(봉인 루브릭 11문항 + 감사 lens 10종) · DART 피드 · `extract_ai_claims`
      - 보완 3건 반영(hashkey 제거 · 해외 예수금을 `VTRP6504R`로 분리 · 테스트 잔재)
      - **체인 리허설 1회 완주**: 재료 → 분석노트 → 시그널 → fact-check(T1 승격) → GATE PASS →
        risk_guard → execute dry-run. 산출물은 `analysis/*REHEARSAL*`에 견본으로 보존
      - 남은 것(키 필요): 실 run 5회+ · `no_trade` 실사례 · MISMATCH 재발행 실사례
- [x] **Phase 0 — 계좌·키** (2026-09-07): 모의·실전 키 등록, 실전은 `limits.json` 래치로 차단
- [x] **실체결 검증** (2026-09-08): 모의계좌 매수·매도 왕복 성공. 이때만 보이는 버그 3건
      수정 — 현금 항목 이중계상 · 호가단위 미준수 · 같은 날 기록 덮어쓰기. 거래비용 추적 신설
- [ ] **분석 체인 실 run**: 지금까지 주문은 전부 손으로 쓴 시그널이었다.
      뉴스 → 분석노트 → fact-check → 게이트 → 주문까지 **실제 판단으로** 한 바퀴가 아직 없다
- [ ] **무인화 — 별도 상시 서버** (2026-09-08 결정, 시점 미정):
      노트북 예약 실행은 전원·잠자기 의존이라 불안정해 채택하지 않는다. 위 "서버로 옮길 때
      필요한 것" 참고. 그때까지는 수동 운영
- [ ] **6개월 후** — 실계좌 *논의* 조건 판정 (비공개 조사 리포트 기준: 하락 구간 포함 6개월 ·
      사고 0 · 규율 집행률 100% · 세제 선행 조사)
