# claude-skills-portfolio

> **English summary.** Two [Claude Code](https://claude.com/claude-code) skills I built and run every day, published here as a portfolio with personal data removed.
>
> - **`world-study`** turns subscribed newsletters into a layered study note (fact → cause → concept → exception). The rules were *derived*, not invented: 57 source issues were compared against 177 hand-written note entries.
> - **`trade-run`** is a 6-stage paper-trading pipeline. The LLM reads newsletters, disclosures and prices and only *proposes*; deterministic Python (`risk_guard.py`) approves or rejects, `execute.py` sends, `fill.py` waits until every order is filled, cancelled or expired. Success is measured by discipline — stop execution rate, zero incidents, complete records — not by returns.
> - Both are judged by a **sealed step-gate engine** (`.claude/skills/_stepgate/stepgate.py`): the worker never reads the rubric, the engine re-checks the files on disk, and each stage needs the previous stage's pass token.
> - Stdlib-only Python 3.9+. Excluded: account snapshots, journals, notes, newsletter bodies, credentials. Included: a synthetic rehearsal dataset and a 77-case offline regression suite, so a fresh clone runs without any API key.

---

## 이 저장소는 무엇인가

개인 프로젝트에서 매일 실운영 중인 Claude Code 스킬 두 개와, 그 밑에서 도는 Python 도구·게이트 엔진을 공개용으로 옮긴 것이다. 원본 저장소는 개인 노트·계좌 스냅샷·뉴스레터 원문과 섞여 있어 비공개이고, 이 저장소는 그중 **코드·규격·합성 데이터**만 골라 사람 이름·경로·계좌 비율을 치환한 사본이다.

공통 배경:

- 뉴스는 **구독 뉴스레터로만** 본다 — 국내 3종(경제·부동산·스타트업) + 영문 Axios 계열. Gmail IMAP으로 자동 수집한다.
- 같은 뉴스레터를 두 갈래로 쓴다: **공부**(`world-study`)와 **매매**(`trade-run`).
- 두 스킬 다 산출물을 **단계 게이트**(`_stepgate/stepgate.py`)로 심판받는다. 워커는 봉인된 루브릭을 못 보고, 파일 실물을 엔진이 직접 검사한다. 앞 단계 통과 토큰이 없으면 다음 단계가 막힌다.
- 노트에 재료 밖 사실을 덧붙이면 `[AI]`(외부 사실 → `fact-check` 스킬로 검증) / `[추정]`(인과·의도 해석) 라벨을 단다.

## 구조 한눈에

```
Gmail (IMAP) ── gmail-newsletter-analyzer/digest.py ── 직전 노트 이후 밀린 발행분 전부 수집·정제
        │
        ├─ world-study ──▶ 세상공부 노트 ──▶ check_note.py(금지어·커버리지·중복) ──▶ [AI] 줄 fact-check ──▶ SG judge
        │
        └─ trade-run ──▶ capture ▶ carry ▶ map ▶ note ▶ dispatch | no_trade ▶ execute
                         ingest.py  theses.py  market_map.py  분석노트+signal  risk_guard.py ▶ execute.py ▶ fill.py ▶ journal.py
                         ◀─────── LLM: 읽고 제안 ───────▶  ◀── 결정론 Python: 판정·전송·체결 확정·저널 ──▶

모든 단계 ─── .claude/skills/_stepgate/stepgate.py  (form → 정직한 답 → judge · 봉인 루브릭 · 디스크 재검증 · 순서 토큰)
```

## 1. `world-study` — 뉴스레터로 `세상 공부` 노트 쓰기

| | |
|---|---|
| 무엇 | 뉴스레터를 읽고 학습 노트를 이어서 쓴다. **요약이 아니라** 한 줄의 사실에서 "왜 그런가"를 캐서 계층(사실 → 원인 → 개념 → 예외)으로 정리한다 |
| 어디서 왔나 | 운영자가 2026-01~03에 직접 쓰던 노트 177항목과 원문 57통을 대조해 규칙을 뽑아 스킬로 만든 것 (전이규칙 문서·짝 코퍼스는 비공개) |
| 흐름 | `digest.py`로 밀린 발행분 수집 → 선별·계층·설명 → `check_note.py` 기계 검사 → 게이트 → `[AI]` 줄 `fact-check` → `tidy_versions.py` 버전 정리 |
| 만든 도구 | `digest.py`(수집) · `gmail_imap.py`(IMAP 전송층, 추적 링크 해제) · `scan_senders.py`(발신자 조사 → 로스터 제안) · `check_note.py`(금지어·커버리지·중복·렌더링 함정 검사) · `cache_claims.py`(검증 캐시) · `tidy_versions.py`(날짜당 1개 정리) · `build_corpus.py`(노트 ↔ 원문 짝 코퍼스 생성) |
| 핵심 규칙 | 분량을 미리 정하지 않음 · 주가가 움직인 뉴스 우선, 거시지표는 거의 안 씀 · 하위 항목은 사실이 아니라 메커니즘 · 용어는 "무슨 제도·왜 그 규칙·이 뉴스와 무슨 상관" 3요소 · 항목 순서는 원문 순서 그대로 |
| 파일 | `.claude/skills/world-study/SKILL.md` · `gmail-newsletter-analyzer/` · 루브릭 `_stepgate/rubrics/world-study.json` |

## 2. `trade-run` — 모의투자 자동매매 파이프라인

| | |
|---|---|
| 무엇 | 뉴스레터·공시·시세를 Claude가 읽고 **분석노트 + 매매 시그널**을 만들면, 결정론 Python이 **규율 판정·주문 집행·체결 확정·저널**을 한다. 한국투자증권 모의계좌(국내·해외) |
| 흐름 | 6단, 단계마다 게이트: `capture`(수집·측정) → `carry`(직전 run 이어받기) → `map`(시장 지도·섹터 리서치) → `note`(종목·분석노트 확정) → `dispatch` / `no_trade`(결정) → `execute`(체결 확정) |
| 설계 원칙 | ① **판단과 집행 분리** — LLM은 제안만, `risk_guard.py`가 승인/거부, `execute.py`가 전송 ② **성공 지표는 수익률이 아니라** 규율 집행률·무사고·기록 완전성 ③ 뉴스레터는 늦으니 **속도로 안 싸운다** — 뉴스는 논지를 주고, 타이밍은 미리 걸어둔 가격 트리거·예정 이벤트가 준다 ④ `no_trade`는 견해가 없을 때만 — 불확실성은 기권이 아니라 **포지션 크기**로 흡수 ⑤ 두 계좌를 한 포트폴리오로 ⑥ 주문 접수와 체결은 다른 사건 — 체결·취소·만료 중 하나로 확정될 때까지 run이 붙어 있는다 |
| 만든 도구 | `ingest.py`(재료·스냅샷·DART) · `stage.py`(단계 드라이버) · `market_map.py`(시장 지도·섹터 보드·일정) · `theses.py`(논지 원장 — 매수·매도 조건을 미리 걸어둠) · `scenarios.py`(시나리오 원장) · `review.py`·`sessions.py`(회고·일과 원장) · `verify_numbers.py`·`srcledger.py`·`crosscheck.py`(전사·출처·대조 검증) · `base_rates.py`·`price_levels.py`(기저율·가격 기준) · `risk_guard.py`(규율) · `execute.py`·`fill.py`(집행·체결 확정) · `journal.py`(저널·트레일링 스톱) · `run_auto.py`(무인 운영자) · `req_audit.py`(요구 ↔ 문서 ↔ 게이트 대조표) · `test_risk_guard.py`(회귀 77건) 등 |
| 안전장치 | 뉴스레터·공시 본문은 신뢰할 수 없는 외부 입력(프롬프트 인젝션)으로 다룬다 · 유니버스 밖 종목은 주문이 아니라 `watchlist_candidates`로만 제안 · `limits.json`·`watchlist.json`은 재료를 읽는 동안 수정 금지 · 실계좌 키는 `real_trading_enabled=false` 래치로 차단 · `config/KILL` 파일 하나로 전면 중지 |
| 설계 근거 | 비용 차감 후 LLM 초과수익의 공개 근거가 없어(공개 사례 성능 조사 — 비공개 문서), **알파가 아니라 규율**을 목표로 삼음 |
| 파일 | `.claude/skills/trade-run/SKILL.md` + `references/stage1~6_*.md` · `ai-trading-bot/` · 루브릭 `_stepgate/rubrics/trade-run.json`(체크포인트 7개) |

## 3. 단계 게이트 엔진 (`_stepgate`)

스킬이 "다 했다"고 말하는 것과 실제로 다 한 것을 가르는 장치다. 표준 라이브러리만 쓰는 단일 파일(`stepgate.py`)이다.

- **워커 / 심판 분리** — 작업 스킬은 `form`으로 관찰 문항만 받아 정직히 답하고, `judge`가 봉인 루브릭으로 PASS/FAIL을 낸다. 루브릭은 워커 컨텍스트에 들어가지 않는다(열람은 사후 감사가 잡는다).
- **극성 회전** — 같은 문항이 회차마다 Y정답·N정답 표현 중 하나로 뒤집혀 나와, 지난 답 벡터를 외워 재사용하는 것이 통하지 않는다.
- **디스크 재검증** — 답이 "예"여도 엔진이 산출물 파일을 직접 열어 패턴·크기·인용을 확인한다. 강제력은 비밀이 아니라 검증에 의존한다.
- **순서 토큰** — 앞 체크포인트의 PASS 토큰이 없으면 다음 체크포인트는 GATE FAIL. 과제 폴더별로 파티션되어 동시 워크플로가 서로를 죽이지 않는다.
- **사후 감사** (`gate-audit` 스킬) — 세션 로그에서 게이트 미호출·루브릭 열람·FAIL 무시·순서 위반 등을 결정론으로 잡는다.
- 루브릭은 런타임에는 워커에게 봉인되지만 **이 저장소에는 공개돼 있다** — 포트폴리오 목적이지 보안 주장이 아니다.

`fact-check`(출처·시효 검증)·`life-research`(출처 검증형 조사)·`gate-audit`(사후 감사)는 두 스킬이 호출하는 형제 스킬이라 함께 담았다.

## 4. 포함 / 제외

| 포함 | 제외 |
|---|---|
| 스킬 5종의 `SKILL.md`(+ `trade-run/references/` 7건) | 실계좌·모의계좌 스냅샷, 저널, 시그널, 분석노트, 시장 지도 |
| `_stepgate/stepgate.py` + 봉인 루브릭 5개·감사 루브릭 3개 | 뉴스레터 원문(저작권)·개인 학습 노트·짝 코퍼스 |
| `ai-trading-bot/` Python 32파일 + `config/`(한도·유니버스·규칙) | 설계 근거 조사·개발 일지(CHANGELOG)·설계 문서 |
| `gmail-newsletter-analyzer/` Python 8파일 + 로스터 스키마 | 자격증명(`.env`, 토큰 캐시), 받은편지함 메타데이터 |
| `_rehearsal/` 합성 데이터 1세트 · `journal/` 합성 픽스처 2개 | 백업·이전 버전 파일 |

치환한 것: 사람 이름 → "운영자", 절대경로 → `<REPO>`, 브로커 접수번호 → `…`, 계좌 잔고 비율·투자비중 수치 → 정성 표현. 종목·수량·가격 같은 규칙 설명용 예시는 그대로 두었다. 코드 주석이 인용하는 `AI자동매매_공개사례_성능조사_v1_0.md`는 비공개 문서다.

## 5. 실행해 보기 (API 키 없이)

```bash
git clone https://github.com/swkim46/claude-skills-portfolio && cd claude-skills-portfolio
cp .env.example .env                       # 키가 비어 있어도 아래는 전부 돈다 (Python ≥ 3.9, 추가 패키지 없음)

# 규율 레이어 회귀 테스트 — 77건, 네트워크 없음
(cd ai-trading-bot && python3 test_risk_guard.py)

# fail-closed 시연 ① — 리허설 시그널은 2026-09-03 생성분이라 "낡았다"로 전량 거부된다
(cd ai-trading-bot && python3 risk_guard.py _rehearsal/signal_REHEARSAL_kr.json \
    --snapshot _rehearsal/snapshot_REHEARSAL_kr.json --out /tmp/approved_stale.json)

# fail-closed 시연 ② — generated_at을 지금으로 찍은 사본은 승인되고, 목표 비중 미달 경고가 붙는다
(cd ai-trading-bot && python3 - <<'PY'
import json, datetime
s = json.load(open("_rehearsal/signal_REHEARSAL_kr.json"))
s["generated_at"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")
json.dump(s, open("/tmp/signal_fresh_kr.json", "w"), ensure_ascii=False)
PY
python3 risk_guard.py /tmp/signal_fresh_kr.json --snapshot _rehearsal/snapshot_REHEARSAL_kr.json --out /tmp/approved_fresh.json)

# 노트 검사기·검증 캐시 셀프테스트
python3 gmail-newsletter-analyzer/check_note.py --selftest
python3 gmail-newsletter-analyzer/cache_claims.py selftest

# 게이트 엔진 — world-study 노트 체크포인트의 출제를 본다 (합격 기준은 봉인)
python3 .claude/skills/_stepgate/stepgate.py form world-study note dir="$PWD/gmail-newsletter-analyzer"
python3 .claude/skills/_stepgate/stepgate.py clear world-study dir="$PWD/gmail-newsletter-analyzer"

# 요구 ↔ 스킬 문서 ↔ 게이트 루브릭 대조표 (실제 run 산출물이 없으면 '히트 0' 항목이 정보성으로 뜬다)
(cd ai-trading-bot && python3 req_audit.py --quiet)
```

키를 넣으면: `python3 ai-trading-bot/kis_client.py`(모의 토큰·시세·잔고 3건) · `python3 gmail-newsletter-analyzer/gmail_imap.py`(IMAP 연결) · `python3 ai-trading-bot/ingest.py --market kr --with-news --days 1`(재료·스냅샷).

Claude Code에서 이 저장소 루트를 열면 `.claude/skills/`가 자동 등록되어 `/trade-run kr`, "세상 공부" 같은 호출이 스킬을 띄운다. 각 스킬 문서의 `<REPO>`는 클론한 절대경로로 읽는다.

## 6. 면책

- **모의투자 전용**이며 투자 조언이 아니다. 실계좌 키는 `config/limits.json`의 `real_trading_enabled=false` 래치가 막는다.
- 뉴스레터·공시 본문은 신뢰할 수 없는 외부 입력으로 다룬다 — 프롬프트 인젝션 방어가 설계 요건이다.
- `ai-trading-bot/journal/market_map.json`·`theses.json`은 회귀 테스트용 합성 픽스처이며 실제 run이 덮어쓴다. `data/`·`journal/`·`signals/`·`analysis/`·`세상공부/`·`_raw_sources/`의 런타임 출력은 `.gitignore`로 막혀 있다.
- `test_risk_guard.py`의 ㊳ 실측 고정 항목(특정 날짜의 반쪽 run 역산)은 실제 run 산출물이 있을 때만 검사한다.

## 7. 라이선스

MIT — [LICENSE](LICENSE)
