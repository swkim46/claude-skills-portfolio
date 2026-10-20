#!/usr/bin/env python3
"""요구사항 대조표 — 사용자가 말한 요구가 스킬에 실제로 남아 있는지 기계로 확인한다.

왜 있는가: 요구는 조용히 빠진다. 스킬을 개편하거나 규칙을 옮길 때 문장 하나가 사라져도
아무도 모르고, 몇 주 뒤에 "이거 왜 안 해?"로 돌아온다. 그래서 **요구를 로스터 행으로**
고정하고 매번 센다 — 섹터 보드에서 미조사를 행으로 남기는 것과 같은 수법이다.

두 열을 따로 본다:
  · 문서   = 스킬 문서(SKILL.md + references/)에 규칙으로 적혀 있나
  · 게이트 = 그 규칙을 체크포인트가 검문하나 — **적혀만 있으면 지켜지지 않는다**

★ 봉인 안전: 루브릭에서 키워드 **존재 여부만** 보고 문항·합격 기준은 출력하지 않는다.

사용:
    python3 req_audit.py            # 표 출력, 누락이 있으면 종료코드 1
    python3 req_audit.py --quiet    # 누락만 출력
"""
import glob
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SKILL = HERE.parent / ".claude" / "skills" / "trade-run"
STEPGATE_DIR = HERE.resolve().parent / ".claude" / "skills" / "_stepgate"
RUBRIC = STEPGATE_DIR / "rubrics" / "trade-run.json"
# 형제 스킬 `trade-feedback`(토요일 자가 피드백)도 같은 로스터로 센다 — 매매 규칙을 고치는 쪽의 요구가 빠지면
# 고친 규칙이 검증·철회 없이 쌓인다(2026-10-03).
SKILL_FB = HERE.parent / ".claude" / "skills" / "trade-feedback"
RUBRIC_FB = STEPGATE_DIR / "rubrics" / "trade-feedback.json"
# fact-check 게이트 ②(`verdict`)도 같은 로스터로 센다 — 판정표 품질 문항은 호출자가 아니라 판정표를 만든 쪽이 답하므로
# fact-check 루브릭에 산다(2026-10-08). 이 감사는 그 문항이 실재하는지만 본다(봉인 안전 — 출력 없음).
RUBRIC_FC = STEPGATE_DIR / "rubrics" / "fact-check.json"
# skill-maintainer 게이트(`verify`)도 센다 — 이 프로젝트 코드를 고치는 절차(백업·사라진 줄 대조·테스트)는 그 게이트가 검문한다(2026-10-13).
RUBRIC_SM = STEPGATE_DIR / "rubrics" / "skill-maintainer.json"

# (요구, 문서 앵커, 게이트 앵커 | None=게이트 대상 아님)
# 새 요구가 나오면 **여기에 행을 추가**한다. 행이 없으면 그 요구는 존재하지 않는 것으로 취급된다.
# ★ 게이트 앵커가 `NOT:<정규식>`이면 **금지 앵커**다 — 그 표현이 루브릭에 **없어야** 통과한다(2026-09-23).
#   왜: 규칙을 폐지해도 봉인 루브릭이 따라오지 않으면 게이트가 죽은 규칙을 계속 집행한다. 실사례 — 09-23 dispatch
#   게이트가 09-22에 폐지된 "이벤트 창이면 절반"을 Y로 물어, run이 답할 수 없는 항목에 "해당 없음"을 적어야 했다.
REQS = [
 ("① 거시→섹터→종목 순서로 접근",          r"위에서 아래로|거시 사건 → 파급", r"top_down_order"),
 ("② 파급 경로를 연쇄로 잇기",              r"연쇄는 가장 약한 고리", r"link_fields_written|breaks_if"),
 ("③ 기업 발표도 매크로에 연계",            r"기업 발표는 그 기업만의 일이 아니다", r"event_splits_sectors"),
 ("④ 연계 시장·종목까지 확장",              r"축 → 섹터|유니버스 밖 대표 종목", r"sector_names_chosen"),
 ("⑤ 놓친 분야 리서치(순환 슬롯)",          r"③ 순환|순환 슬롯", r"slot_cycle_filled"),
 ("⑥ 리서치는 매 run 필수",                 r"리서치는 \*\*매 run의 필수 단계\*\*", r"research_landed"),
 ("⑦ 막히면 루프 돌아 뚫어라",              r"사다리 3단|쿼리 소진", r"ladder_walked"),
 ("⑧ 매일 갱신·전날 이어받기",              r"§1-A 이 시장 이어받기", r"prev_scenarios_judged"),
 ("⑨ 오전 국내준비+미국회고 / 밤 반대",     r"09:35|22:35", r"review_into_note"),
 ("⑩ PER·EPS·지지선을 계산해서 쓴다",       r"price_levels\.py", r"basis_not_live_price"),
 ("⑪ 미국 주식도 추적",                     r"이중 시장 규칙", r"dual_market_rows"),
 ("⑫ 유니버스 유동 운용",                   r"universe_apply\.py|편입 대기열", r"universe_reviewed_both_ways"),
 ("⑬ 무인 운영 — 사람 확인을 게이트로",     r"무인 운영이 목표", r"unattended_no_ask"),
 ("⑭ 언젠가 결단은 내린다(기권 아님)",      r"'안 사는 것'이 기본값이면 안 된다", r"reason_is_no_view"),
 ("⑮ 전권 위임 — 입금액만 사용자가",        r"사람 확인은 게이트로 대체", r"delegation_scope"),
 ("⑯ 시나리오 선작성→발화→집행→매도",       r"논지 원장|entry_triggers", r"fired_as_is|thesis_moved_to_held"),
 ("⑰ 정보 지연 인지(속도로 못 이긴다)",     r"우위는 \*\*속도가 아니다\*\*", r"horizon_weeks_not_days"),
 ("⑱ 노트 언어 = 재료 언어",                r"작성 언어 — 노트 언어를", None),   # 2026-10-13 노트 읽는 사람용 검문 폐지 — 문서 규칙만
 ("⑲ 매매 분석에도 fact-check",             r"5-C\. fact-check", r"\"track1\": \\\{"),   # 2026-10-13 — 주문 근거는 dispatch가 승인 파일 track1로
 ("⑳ 소셜·유료 출처는 방법만 (스레드 보류)", r"소셜 미디어·유료 콘텐츠", r"tier3_method_only"),
 ("㉑ 사회·시장 동태를 전날과 이어서",       r"cycle --market|market_map\.py cycle", r"slot_cycle_filled"),
 ("㉒ 한·미 합쳐 30종목 이상 추적",          r"유니버스 전수", r"universe_rows_intact"),
 ("㉓ 파이프라인 분리 + 단계마다 게이트",    r"6단 파이프라인", r"capture"),
 ("㉔ 각 단계가 조사·판단·근거를 기록",      r"작업기록|worklog", r"worklog_rejects|worklog_carry"),
 ("㉕ 시나리오 상세 · 종목 수 최대",         r"깊이 — 권장 바닥", r"scenario_both_markets"),   # 2026-10-13 깊이 수는 권장 — 시나리오·유니버스만 게이트
 ("㉖ 게이트를 최대한 빡빡하게",             r"단계마다 게이트를 통과한다", None),
 ("㉗ 최종 액션(매수·매도)이 노트에",        r"§11 집행 결과", r"s11_fill_written|s11_discipline_written"),
 ("㉘ 뉴스레터 수집(밀린 발행분 전부)",      r"ingest\.py --market .* --with-news", r"material_intact"),
 ("㉙ 리서치를 life-research로 위임",        r"Skill\(life-research\)", r"research_priority_kept"),
 ("㉚ 주문 접수 ≠ 체결",                     r"주문 접수와 체결은 다른 사건", r"fill_verified|unfilled_reported"),
 ("㉛ 인젝션 방어 — 본문 지시 불복",         r"신뢰할 수 없는 외부 입력", r"injection_not_obeyed"),
 ("㉜ '미확인'을 '없음'으로 바꾸지 않는다",   r"'미확인'을 '없음'으로", r"unknown_not_none"),
 ("㉝ 조건부 기저율로 판단",                 r"base_rates\.py", r"base_rate_own_horizon"),
 ("㉞ 벤치마크 병기 기록",                   r"벤치마크 병기", r"equity_curve|journal_daily_ran"),
 ("㉟ 중복 지출은 일일 상한으로 막는다",     r"spent_today", r"dup_run_by_rule"),
 # 2026-09-10 전수조사에서 나온 요구 — 무인 운영자가 스킬과 반대 규칙을 갖고 있었다.
 ("㊱ 무인 운영자를 스킬이 명시한다",        r"run_auto\.py", None),
 ("㊲ 소유권 경계(1~5단 모델·--send 드라이버)", r"소유권 경계|--send는 드라이버", None),
 ("㊳ 기권도 게이트 면제 없다",              r"기권도 (검문 대상|면제가 아니다)", r"no_trade"),
 ("㊴ --send는 dispatch 토큰에서만",         r"`dispatch` 토큰에서만|dispatch GATE PASS 뒤에만", None),
 ("㊵ 거부를 미체결·취소·만료로 쓰지 않는다", r"거부를 (미체결|취소·만료)로 쓰지 마라", r"rejected_not_unfilled"),
 ("㊶ 미기록 집행은 사고로 기록한다",        r"failed_record", None),
 ("㊷ 이미 가진 것부터 뒤진다(사다리 0단)",   r"corpus\.py search", r"corpus_searched_before_closing"),
 ("㊸ 도구 산출 ↔ 노트를 디스크로 대조",      r"crosscheck\.py", r"crosscheck_clean"),
 ("㊹ 재실행이 이력을 덮지 않는다",           r"덮어쓰지 않았다|_superseded", None),
 ("㊺ 밀린 세션·반쪽 run을 탐지해 이어받는다", r"sessions\.py", r"missed_sessions_carried"),
 ("㊻ 시나리오를 영속 id로 추적",              r"scenarios\.py", r"scenarios_all_judged"),
 ("㊼ 회피 감사(실현↔대응)를 센다",            r"실현.*대응|회피 감사", r"미판정 0|scenarios_all_judged"),
 # 2026-09-10 (2) 2차 — 사용자 신규 요구: 정보의 세 시점
 ("㊽ 모든 정보에 세 시점(as-of·retrieved·written)", r"as-of.*retrieved.*written|세 시점",
  r"\{threepoint\}"),   # 2026-10-13 — 각서 문항 대신 srcledger 3시점 검사 파일(디스크 기준)
 ("㊾ as-of는 출처에서 — 짐작 금지",           r"as-of.*기준일|짐작", r"\{threepoint\}"),
 ("㊿ 장 마감 후면 집행 불가를 명시",           r"집행 불가", None),
 ("(51) 수집 창이 간극을 덮는지 대조",          r"수집 창", None),
 ("(52) 고점은 시장별로 — 반대편을 지우지 않는다", r"시장별|position_peaks", None),
 # 3차 — 죽은 상태를 되살리거나 지운다
 ("(53) 회피 감사 3지표를 주간 리포트에",       r"회피 감사 — 주간 리포트에|no_trade. 비율", None),
 ("(54) 순환 관찰을 --sectors로 기록",          r"--sectors", r"slot_cycle_filled"),
 # 2026-09-11 — 미국 run이 생성줄 하나 때문에 막혔다(진단에 네 시간)
 ("(55) 1단 생성줄은 번역·재작성 금지",         r"1단이 생성한 줄은 번역하지 않는다|1단 생성 — 그대로 둘 것", None),
 ("(56) 스탬프는 ✓ 줄과 시각 줄을 분리",        r"완료 스탬프는 두 줄이다", None),   # 2026-10-13 스탬프는 진행 표시 — 게이트가 세지 않는다
 # 2026-09-11 (2) — 두 시장은 독립이 아니다. 포트폴리오를 한 계좌로 취급한다(D8).
 ("(57) 모든 비중의 분모는 포트폴리오 합산 자산", r"포트폴리오 합산|한 계좌로 취급",
  r"portfolio_denominator"),
 ("(58) 목표는 포트폴리오, 집행은 그 시장 현금", r"집행은 그 시장 (현금|자산)",
  r"portfolio_denominator"),
 ("(59) 반대편 대기 논지 몫은 판단된 예약(allocation.reserved)으로", r"allocation\.reserved|판단된 예약",
  r"other_market_considered"),
 ("(60) 축 집중은 양 시장 합산으로 재고 공개선을 넘기면 명분", r"양 시장을 합쳐 재고|concentration_disclose_pct", r"portfolio_denominator"),
 ("(61) 노트가 두 시장을 같은 척도로 적는다",    r"1% = |자본 기준", r"portfolio_denominator"),
 ("(62) 반대편 예정 진입을 고려하고 적는다",     r"반대편 예정 진입|반대편 대기 논지",
  r"other_market_considered"),
 # 2026-09-11 (3) — 두 시장을 하나의 run 흐름으로 (D1~D7)
 ("(63) 회고 대상은 반대편 시장이 아니라 **직전 run**", r"직전 run", r"prev_run_reviewed"),
 ("(64) 시나리오는 축에 걸리고 **대응만** 시장에 속한다", r"조건은 시장에 속하지 않는",
  r"scenarios_all_judged"),
 ("(65) 실현됐는데 미집행인 대응을 이어받는다", r"scenarios\.py due|집행 대기", r"prev_run_reviewed"),
 ("(66) 논지·시나리오는 축 id로 이어진다(고아 축 0)", r"axis_id", r"axis_joined"),
 ("(67) 섹터 등급은 시장 무관하게 persist된다", r"market_map\.py grade|등급이 run을 넘어",
  r"axis_joined"),
 ("(68) 직전 run의 산출(노트·리서치·지도)을 오늘 입력으로", r"직전 run 산출",
  r"prev_run_reviewed"),
 ("(69) 출처는 공용 원장에 누적하고 재사용한다 — 저장소 사실 층(facts.py reuse · known)", r"facts\.py reuse",
  r"prev_run_reviewed"),
 # 2026-09-14 — 끝 목표: 세상을 읽고 돈이 갈 곳을 **앞서** 잡는다 (E1~E7)
 ("(70) 축은 방향·기간·수혜 순서가 있는 **전망**이다", r"forecast add|전망 원장", r"forecast_made"),
 ("(71) 뉴스가 1차를 말하는 날 2차·3차 수혜를 적는다", r"2차·3차 수혜", r"forecast_made"),
 ("(72) 반영 여부는 감이 아니라 20일 상대수익으로 잰다", r"market_map\.py priced|priced", r"forecast_made"),
 ("(73) 세상공부·링크 원문이 재료의 입력이다", r"세상공부|링크 원문", r"material_deficit"),
 ("(74) 재료 결손은 경고가 아니라 차단이다", r"재료 결손 없음", r"material_deficit"),
 ("(75) 일정에 유니버스 종목이 걸리면 논지 또는 \[통과\]가 있어야 한다", r"gaps --check|\[통과\]",
  r"forecast_made"),
 ("(76) 법이 강제하는 행동을 스크린한다(자사주 소각 창)", r"screen_treasury|소각 창", None),
 ("(77) 같은 축이라도 동인이 다르면 후행주가 아니다", r"동인이 다르면 후행주가 아니다", r"base_rates_checked"),
 ("(78) 기저율은 매수 전 게이트다", r"base_rates_<stamp>|기저율.*매수 전", r"base_rates_checked"),
 ("(79) 전망 선행률·적중률·초과수익을 매주 낸다(성공 지표 ④)", r"성공 지표 ④|선행률", None),
 ("(80) 아쉬움을 깔때기 숫자로 센다", r"regret\.py|아쉬움 깔때기", None),
 # 2026-09-14 (2) — 사용자 결정: 선물형만 배제, 레버리지·인버스 허용 (수익 기준)
 ("(81) 선물형만 배제, 레버리지·인버스는 산다", r"선물형만", None),
 ("(82) 레버리지는 호흡 ≤10일·상한÷배수·축×배수", r"호흡 ≤10일|leveraged_max_horizon_days", None),
 ("(83) 하락 견해는 SELL 또는 인버스 BUY(공매도 없음)", r"인버스 ETF BUY|인버스 BUY", None),
 # 2026-09-15 — 다른 세션이 보고한 도구 결함 4건
 ("(84) '직전'은 날짜 스탬프로 고른다 — 리허설은 운영 폴더 밖", r"latest_stamped|_rehearsal/", None),
 ("(85) 1단 산출물 없으면 run이 아니다(허깨비 회고 방지)", r"1단\s*산출물.*run으로 치지 않는다|phantom", None),
 ("(86) forecast add 재실행은 priced를 지우지 않는다", r"priced.*지우지 않는다", None),
 ("(87) universe_apply는 매 run 5단에서 돈다(무인 전용 아님)", r"universe_apply\.py --signal.*--apply",
  r"universe_applied"),
 # 2026-09-16 — 체결까지 run의 책임 · 현금 비중
 ("(88) 접수는 run의 끝이 아니다 — 체결·취소·만료로 확정될 때까지", r"접수는 run의 끝이 아니다|확정될 때까지\*\* run이 붙어 있는다",
  r"fill_confirmed"),
 ("(89) 살아 있는 주문 재전송 금지 — 재발주는 취소 확정 뒤 승인 금액 안에서", r"취소가 브로커 TR로 확정된 뒤|승인 금액을 넘지 않게 내린다",
  r"fill_confirmed"),
 ("(90) 장외 전송 차단", r"장외면\s*전송을 거부한다|장외면 `--send` 거부|--force-hours", None),
 ("(91) 시장 현금 부족은 깎아서 산다", r"현금에 맞춰 수량을 깎아서|깎아서 산다", None),
 ("(92) 미달이면 최소 건수·축·e0 강제(deficit_short)", r"미달 강제|deficit_short", r"deficit_met"),
 # (93) 미달 배수 3.0 — 2026-09-22 폐지(상수 배수 → gap 채우기). 요구 자체는 (110)이 잇는다.
 ("(93) 미달이면 크기를 키워 채운다(옛 미달 배수 → gap 채우기)", r"미달 배수.*지웠다|gap 채우기", None),
 # 2026-09-16 (2) — 입금된 돈 안에서 · 손절 즉시 · 명령 규약
 ("(94) 사람에게 환전·입금을 묻지 않는다 — 깎기·예약·회전 매도", r"환전·입금을 묻지 않는다|회전 매도", None),
 ("(95) 손절·목표는 종가를 기다리지 않는다 — 장중 즉시 규율 매도", r"종가를 기다리지 않는다|장중 터치 즉시", None),
 ("(96) 명령 규약 — 새 셸·전체 경로·--batch", r"명령 규약|--batch", None),
 ("(97) 스탬프 시각은 도구가 찍는다", r"stage\.py stamp", None),
 ("(98) 링크 본문은 fetch.py", r"fetch\.py", None),
 ("(99) 표기는 장식 없이 — crosscheck가 변형을 잡는다", r"장식 변형|장식 없이 그대로", None),
 ("(100) 시장 선택은 열린 시장 — 시각 제한 없음(stage.py market)", r"stage\.py market|시각 제한은 없다", None),
 # 2026-09-17 — 사용자 지시: 끝날 때마다 계좌 현황(주식/현금 비율·수익률)을 보고 끝에
 ("(100b) 보고 끝에 계좌 현황 블록(주식:현금·수익률)", r"account_status\.py", r"\{status\}"),
 # 2026-09-17 (2) — 사용자 지시: 지정가는 최신 시세로 · 취소 확정 뒤 재발주
 ("(101) 전송 직전 지정가를 최신 시세로(해외 포함)·가격 이탈이면 미전송", r"국내·해외 모두\*\* 갱신|send_refresh_max_pct",
  r"limit_refreshed"),
 ("(102) 정정 거부 서버에서는 취소→재발주", r"곧바로 취소→재발주|fill_reorder_max", r"fill_confirmed"),
 # 2026-09-21 — 두산 46주 승인 밖 중복 매도: 같은 승인은 한 번만(멱등) · 승인 초과는 게이트가 센다
 ("(103) 같은 approved 재호출은 멱등 — 승인 초과는 사고(INC-)로 기록·게이트 검문", r"멱등|승인 초과", r"reconciled|overfill|incident_recorded"),
 ("(104) 재료에 없다 ≠ 발표 안 됐다 — 예정된 공식 발표는 발표 주체를 직접 확인", r"재료에 없다 ≠ 발표 안 됐다|발표 주체", None),
 ("(105) 이월 시나리오는 persistent_id로 — promote가 그것으로 동일성 판단·merge", r"persistent_id", None),
 ("(106) 1차 목표 여유 없는 매수는 거부 — 다음 run이 새 기준으로 목표를 다시 건다(set-trigger)", r"목표 여유|min_target_gap_pct|set-trigger", None),
 ("(107) 휴장은 holidays.json — 전환일(2026-10-14) 전 스탬프는 세션 기준일과 다르다(stamp_to_session) · 일정 kind 어휘", r"holidays\.json|kind 어휘|EVENT_KINDS", None),
 # 2026-09-22 — 사용자: 상수 목표·상수 제약 지양, 판단이 비율을 정한다 · 현금엔 명분 · 미달은 즉시 도달 · 리서치=재료 확장 · 이벤트=베팅
 ("(108) 주식 비율은 상수가 아니라 이어받는 판단(allocation · based_on · change.why)", r"allocation\.py prev|based_on", r"allocation_carried|allocation_judged"),
 ("(109) 현금을 남기려면 명분과 해제 조건(cash_reason · cash_release_when)", r"cash_reason|현금 명분", r"allocation_judged"),
 ("(110) 판단한 비율에 못 미치면 증분을 키워 즉시 채운다(gap · scaled_up · covered_pct)", r"gap 채우기|scaled_up|covered_pct", r"gap_covered|covered_pct"),
 ("(111) 집중은 상한이 아니라 명분(concentration_why · 공개선)", r"concentration_why|공개선", None),
 ("(112) 이벤트는 할인이 아니라 베팅 — 이벤트 계수 폐지", r"베팅할 자리|이벤트 할인 폐지|이벤트 계수는 \*\*없다\*\*", None),
 ("(113) 리서치는 재료 확장 — 뉴스레터에 없던 것을 더하고 판단이 인용한다", r"재료 확장", r"research_expands"),
 ("(114) 확신도는 크기를 정하지 않는다 — 크기는 size_why", r"size_why", None),
 ("(115) 확정 중 예외로 끊겨도 주문·기록은 남는다 — fill.py 뒤 같은 approved 재호출", r"확정 대기 중 예외|확정 중 예외", None),
 ("(116) 실행 시간·읽은 분량을 계측한다(sessions.py timing)", r"sessions\.py timing", None),
 # 2026-09-23 — 사용자: 일일 상한 없애고 · 이중 매도 고치고 · 데이터 결함 고쳐
 ("(117) 소진된 매도 다리는 다시 발화하지 않는다(체결이 fired를 찍는다)", r"소진된 다리|fired", r"leg_not_respent|fired"),
 ("(118) 시세 TR은 NYS/NAS/AMS · 주문 TR은 NYSE/NASD/AMEX — 섞으면 빈 응답", r"quote_excd|시세용 코드", None),
 ("(119) 빈 응답은 거부가 아니라 미확인 — 못 물은 것을 '거부'로 적지 않는다", r"미확인\*\*\(거부 아님\)|못 물은 것을", None),
 ("(120) 회전 매도는 자금 대상 매수가 나갈 수 있을 때만", r"그 돈을 쓸 매수가 나갈 수 있을 때만|자금 대상", None),
 ("(121) 실전 래치가 열리면 일일 상한이 필수다", r"real_latch_needs_cap|실전 래치", None),
 ("(122) 폐지된 규칙이 게이트에 남아 있지 않다(금지 앵커)", r"규칙을 폐지했으면 셋을",
  r"NOT:절반|이벤트 계수|구간표|종목당 상한|투자 천장|일일 상한|by_confidence|max_invested"),
 # 2026-10-03 — PDF 다운로드 버튼 · 자가 피드백(놓친 급등을 규칙 수정으로)
 ("(123) PDF는 fetch.py로만 연다 — WebFetch·브라우저 금지(다운로드 카드·본문 실패)", r"PDF 주소는 반드시 `fetch\.py`로|PDF는 `python3 fetch\.py <url>`로만", r"pdf_via_fetch"),
 ("(124) 급등은 창 안 저점→고점 최대 상승폭 — 올랐다 되돌린 종목도 센다", r"저점→고점 최대 상승폭", r"peak_not_close"),
 ("(125) '막은 것'은 유형으로 적는다 — '기록 없음'은 흔적이 전혀 없을 때만", r"'막은 것'은 유형으로 적힌다", None),
 ("(126) 깔때기의 논지는 그 창에 살아 있던 것만 센다", r"논지는 그 창에 살아 있던 것만", None),
 ("(127) 시총 상위 메인 종목을 같이 잰다(main_names)", r"main_names", r"main_names_measured"),
 ("(128) 피드백은 고점 전 단서로 판정·유형으로 제안·반례 점검", r"사후 지식으로 채점하지 않는다", r"pre_peak_clue"),
 ("(129) 결함이 아니면 규칙을 고치지 않는다 · 원인은 유형 문장", r"근거가 틀렸을 때만 고친다", r"no_change_without_defect|cause_no_proper_noun"),
 ("(130) 승인 대기(confirm)는 자동 적용 금지 — 권한 정책이 없으면 전부 승인 대기", r"승인 대기\(`confirm`\) 행은 적용하지 않는다", r"confirm_untouched"),
 ("(131) 적용분은 다음 주 지표로 검증·철회한다", r"지난주 적용분 검증", r"due_resolved|next_check_set"),
 ("(132) 금요일 주간 리포트는 ④절까지 완주한다(토요일 피드백의 입력)", r"주간 리포트가 끝까지 돌았는가", None),
 # 2026-10-03 — 수익 하한 10%·상한 없음 · 첫 피드백 승인분(FB-261003-1~11)
 ("(133) 수익 10%는 하한이지 목표가 아니다 — 상한 없음 · 상승 여지 바닥 · 큰 자리에 크게", r"바닥\(하한\)이지 목표가 아니다|하한이지 목표가 아니다", None),
 ("(134) 문서에 '목표 10%' 표현이 없다(하한을 목표로 쓰면 10%에서 멈추는 장치가 생긴다)", r"NOT:목표 10%|10% 목표", None),
 ("(135) 급등 당일 매수 금지는 그 세션에만 — 가격 사유로 거른 종목은 다음 run 재평가 목록", r"재평가 목록", r"reeval_judged"),
 ("(136) 열린 전망의 2·3차 유니버스 수혜는 §6에 논지 id 또는 [통과]", r"2·3차 수혜는 평가까지 한다|2·3차 유니버스 수혜", r"beneficiaries_judged"),
 ("(137) 수익 쪽 매도의 기본은 추적 다리 — 고정 목표는 +10% 위 저항 근거·1/3 이하", r"추적 다리", r"profit_leg_trailing"),
 ("(138) 매크로만으로 팔지 않는다 — 매크로+종목 조건을 함께", r"매크로만으로 팔지 않는다", r"macro_with_stock"),
 ("(139) 국내 관문 KOSPI·KOSDAQ150 · 냉각 후보 재판정 · 시총 상위 자동 편입", r"KOSPI·KOSDAQ150", None),
 ("(140) 첫 진입이 2% 미만이면 증액 조건(increase_when)", r"increase_when", None),
 ("(141) 진입 기준가는 닿을 확률(reach_prob)을 적고 50% 이상 다리 하나", r"reach_prob", None),
 ("(142) 회전 매도는 논지가 가장 약한 보유에서 — 축 확인·이익 중인 승자는 자금원이 아니다", r"논지가 가장 약한", None),
 ("(143) 장중 감시 루프 하트비트(mode=loop)와 run 시작 시 공백 보고", r"mode=loop|하트비트", None),
 ("(144) 모델 매도에 논지 꼬리표를 자동으로 붙인다", r"모델 매도 꼬리표", None),
 ("(145) 같은 승인의 매도 대금은 체결 확인 뒤에만 매수에 쓴다", r"needs_sell_fill", None),
 ("(146) 초당 한도로 접수 전 거부된 주문만 재전송", r"접수 전 거부", None),
 ("(147) 폐지된 '이벤트면 절반'이 문서에도 남아 있지 않다", r"NOT:절반으로 줄인다", None),
 # 2026-10-07 — fact-check 감사 최대 두 번·수정은 1차 뒤 한 번 + 반복 유형 게이트 승격(문항 수 상한 없음)
 ("(148) fact-check 감사는 최대 두 번 — 1차 FAIL이면 한 번 고치고 2차, 두 번째 결과로는 고치지 않는다", r"두 번째 감사 결과로는 고치지 않는다", r"fc_audit_flow"),
 ("(149) 감사에서 반복해 잡힌 유형은 노트 게이트 문항으로 — 문항 수 상한 없음", r"문항 수 상한은 없다", r"fc_qualifier_kept|fc_chain_traced"),
 ("(150) run 보고에 감사 흐름 한 줄과 승격 대기 N건", r"승격 대기 N건", None),
 ("(151) 토요일 피드백이 승격 대기를 게이트 문항으로 올린다", r"게이트 승격", r"gate_promotions_handled"),
 ("(152) '통과할 때까지 재제출' 지시가 매매 문서에 남아 있지 않다", r"NOT:재제출한다", None),
 # 2026-10-08 — 판정표 품질 lens는 fact-check 게이트 ②로 승격(호출자 게이트마다 복제하지 않는다)
 ("(153) 판정표 품질 lens의 승격처는 fact-check 게이트 ②(판정표를 만든 쪽이 답한다)", r"판정표 품질 lens는 `fact-check.json`의 `verdict`", r"fact-check:verdict"),
 ("(154) 접근 라벨은 실제 시도 결과로 — '유료·페이월'을 열어 보지 않고 적지 않는다", r"접근 라벨", r"fc_access_label_tried"),
 ("(155) 판정 어휘 정직 — 확인 못 한 것은 UNVERIFIABLE, 낡은 것은 STALE, 머리 집계 = 표", r"판정 어휘", r"fc_vocab_honest"),
 ("(156) 앞뒤 문맥 대조 — 같은 숫자가 있다는 것만으로, 다른 문단 문장으로 MATCH를 주지 않는다", r"앞뒤 문맥", r"fc_context_read"),
 ("(157) 대상 동일성 — 발행처·기관을 링크 주소·라벨이 아니라 본문으로 확인", r"대상 동일성", r"fc_entity_confirmed"),
 ("(158) 하위 클레임마다 원문 대조 행", r"하위 클레임 행", r"fc_subclaims_rowed"),
 # 2026-10-10 — 자가 피드백 FB-261010(측정 결함 두 개 · 검증 방식)
 ("(159) 적용분 검증은 적용 후 사건으로 — 창 지표만으로 유지·철회를 판정하지 않는다 · 표본이 없으면 --next-check로 미룬다", r"적용 후 값", r"verify_post_events"),
 ("(160) 아쉬움 감사의 축은 이름 목록과 전망 수혜 표 둘 다로 센다", r"전망 수혜 표 둘 다로", None),
 ("(161) 보유 비중의 분모는 종목 평가금액 합 + 현금(미국 평가손익을 분모로 쓰지 않는다)", r"종목 평가금액 합 \+ 현금", None),
 # 2026-10-11 — 사용자 승인분(FB-261010-5·6·7)
 ("(162) 새 종목은 이 시장 보유 평균보다 작게 사지 않는다 — 작게 사려면 small_why · BUY마다 계좌 기여", r"이 시장 보유 평균보다 작게 사지 않는다", r"size_contribution_stated"),
 ("(163) 하루만 제안된 대기 후보는 매 run 재제안(오늘 근거) 또는 [내림]", r"대기 후보\(하루만 제안됨\)", r"queue_one_day_judged"),
 ("(164) 국내 편입 시총 하한은 1조(2조 하한은 수혜 중소형주를 오른 뒤에야 들였다)", r"시총 1조·거래대금 100억", None),
 ("(165) 문서에 국내 시총 2조 관문이 현행으로 남아 있지 않다", r"NOT:\(시총 2조·거래대금 100억\)", None),
 # 2026-10-11 — 사용자: "테스트가 멈추지 않도록 · 멈추는 걸 모르지 않도록"
 ("(166) 테스트는 run_tests.py로 끝까지 돌리고 완주를 기록한다 — 토요일마다·코드를 고칠 때마다", r"python3 run_tests\.py", r"tests_completed"),
 ("(167) run 보고에 [테스트] 줄 — ★(멈춤·실패·건수 감소·오래된 기록)면 맨 앞", r"`\[테스트\]` 줄", r"tests_line_reported"),
 # 2026-10-12 — 국내 시나리오 손실 피드백(FB-261012-1~9) · trade-feedback 손실·오판 차선
 ("(168) 이벤트 시나리오는 세 갈래(좋음·나쁨·지연/무소식) · 가지 사이 빈 구간 없음", r"시나리오는 세 갈래로 건다", r"event_three_branches"),
 ("(169) 발화한 되돌림·지지 매수 다리마다 [하락 원인] — 동인을 깨면 그것이 무효", r"\[하락 원인\]", r"drop_cause_line"),
 ("(170) 두 시장 전망이 같은 메커니즘에 반대 부호면 conflicts_with로 잇고 §7에 충돌 가지", r"conflicts_with", r"conflict_linked"),
 ("(171) 요인(driver) 집중은 상한이 아니라 §7 '꺾이면' 행 — 축마다 요인 태그", r"요인 집중은 상한이 아니라 판정이다", r"driver_branch_row|axis_driver_tagged"),
 ("(172) 열린 축 둘 이상에 걸린 종목은 price_driver — 무효 판정 다리는 그 축의 지표로", r"price_driver", None),
 ("(173) 정책·지정학 공급 충격에는 '정부가 값을 내리려 개입한다'를 주 가지로", r"정부가 값을 내리려 개입한다", None),
 ("(174) 보유 논지는 하나도 빠짐없이 추적 다리(judge형 목표도 예외 없음)", r"하나도 빠짐없이 추적 다리", r"held_trail_leg"),
 ("(175) 오래 열린 시나리오는 지연 가지를 세우거나 --expire로 닫는다(미도래 이월 금지)", r"오래 열린 시나리오", None),
 ("(176) 손실·시나리오 오판은 논지가 등록 때 정한 시계로만 — 가격 하락만으로 오판을 주지 않는다", r"가격 하락만으로 오판을 주지 않는다", r"loss_clock_based"),
 ("(177) 일시적·관찰 판정은 이월하고 보정표(loss_verdict_flip_rate)로 빡빡함을 잰다", r"loss_verdict_flip_rate", r"temporary_carried"),
 # 2026-10-12 — 사용자: "국내장과 미국장은 100% 연계되지는 않지만 독립적이지도 않다 — 무조건 상호 연관해서 전망과 시나리오를 짜라"
 ("(178) 전망은 요인 단위 — 수혜 표에 두 시장 종목 또는 no_cross_market_why", r"no_cross_market_why", r"forecast_cross_market"),
 ("(179) 같은 요인 반대 방향 전망은 conflicts_with 또는 split_why", r"split_why", r"forecast_cross_market"),
 ("(180) 시나리오는 이벤트 단위 — 행마다 [KR]·[US] 대응 또는 영향 없음 사유", r"\[US 영향 없음", r"scenario_both_markets"),
 ("(181) 반대 시장이 연 같은 이벤트는 새 행 대신 persistent_id로 이어받고 respond로 대응만 더한다", r"scenarios\.py respond", r"scenario_both_markets"),
 ("(182) 2단 교차 시장 입력(xmarket)을 §1-B ④로 — 반대 시장이 바꾼 판단과 이쪽 대응이 빈 시나리오", r"교차 시장 판단 이어받기", r"xmarket_block_carried"),
 # 2026-10-12 — 사용자: 실계좌 전환(소액 계좌 · 주문당 상한 · 1주 100만 초과는 2주 · 사람 확인 없음 · 손절은 run 시각 · 주간거래는 손절 전용)
 ("(183) 계좌는 account_mode 하나가 정한다 — paper·real_readonly·real, 사람만 바꾼다", r"account_mode", None),
 ("(184) 실계좌 매수는 한 종목 한 번 100만 원 · 1주 100만 원 초과 종목은 최대 2주(매도는 깎지 않는다)", r"주문당 상한", r"order_cap_applied"),
 ("(185) 실계좌 체결은 체결조회 TR이 1순위 · §11 첫 줄 승인/체결조회/잔고 대조표", r"체결조회 TR이 1순위", r"real_reconcile_table"),
 ("(186) 미국 주간거래는 손절·추적 매도 전용 — 신규 매수·이익실현 목표는 정규장", r"손절·추적\s*다리만", None),
 ("(187) 실계좌 수익 바닥은 시장별 세후 값(국내 10.3 · 미국 11.5)", r"min_target_gap_pct_by_market", None),
 ("(188) 해외주식 양도세 원장 — 결제일 환율로 원화 환산 · 12월 손실 실현 후보", r"tax_ledger", None),
 ("(189) run 마지막에 푸시 알림 한 줄(체결·실패·미확정·KILL)", r"PushNotification", None),
 ("(190) 국내 실계좌 주문은 SOR — 거래소 구분 사유 거부면 KRX로 한 번 재전송", r"SOR", None),
 # 2026-10-13 — 10-12 블록 치환이 전송 줄을 지워 다음 run의 전송이 전부 죽은 사고. 사용자: "이런 문제 생기지 않게 확실하게"
 ("(191) 집행 경로 .py를 고치면 가짜 증권사 종단 테스트(test_execute_path)를 포함한 run_tests 완주 뒤에만 --send", r"test_execute_path", r"tests_cover_code_change"),
 ("(192) 캡처 [테스트] 줄은 마지막 완주 뒤 집행 경로 코드 변경을 ★로 알리고 재실행으로만 지운다", r"마지막 완주 뒤 바뀜", r"tests_cover_code_change"),
 ("(193) 코드 편집은 파일마다 백업 → code_edit_check(사라진 줄을 함수별로 전부 설명) → 테스트 러너", r"code_edit_check", r"code_removed_accounted"),
 # 2026-10-13 — fact-check 두 갈래 · 노트는 매매 필수 요소만(사용자: "노트 읽어볼 생각이 딱히 없어서, 매매에 필요한 요소들만 제대로
 #   들어가 있으면 다른 감사는 진행하지 마 · 대신 필수 요소는 무조건 노트에" · "주문·보유 조건 근거는 주문 전에 무조건 pass · 7차까지")
 ("(194) 노트 검문은 매매 필수 요소만 — 필수 요소는 무조건 노트에(note_lint note → NOTE-LINT OK)", r"매매 필수 요소 — 노트에", r"^NOTE-LINT OK$|NOTE-LINT OK(?!\|)"),
 ("(195) 주문·보유 조건 근거는 주문 전에 판정표로 검증 — 판정표에서 안 서는 근거의 제안만 제외(fc_track1 decide --mode pre · 2026-10-13 17:30 개정: 감사는 주문 뒤)", r"갈래 ① — 주문 근거 검증", r"\"track1\": \\\{"),
 # 2026-10-13 17:30 — 사용자: "감사 완료하고 매매하는 건 너무 과하다 · 매매 관련 팩트체크는 배경보다 빡빡하게 감사하되 매매하고 나서
 #   감사를 마무리하는 이전 구조로 · 5차까지 · 매매 의사결정을 바꿔야 하는 감사 결과가 나오면 소급 적용"
 ("(200) 주문 근거 감사는 전파 즉시 배경 · 최대 5회 · 결과마다 판정표 재채점 → decide --mode post · 안 오면 미수신(2026-10-14 개정)", r"decide --mode post", r"track1_audit_post"),
 ("(201) 재채점이 체결 매수의 근거를 뒤집으면 소급 매도 — 장중이면 같은 run에서 보낸다", r"갈래 ① 감사 — 결과 반영 · 최대 5회 · 소급", r"reversal_sent_same_run"),
 ("(202) 장이 닫혀 못 판 소급은 다음 그 시장 run이 자동으로 판다(이어받기 '## 감사 소급' · 사람 확인 없음)", r"감사 소급 — 직전 run의 감사가 뒤집은 매수", r"reversal_pending_sent"),
 ("(203) 주문 근거 감사는 배경보다 빡빡하게 — 주문 전용 감사 항목(kind: order)", r"kind: order", None),
 ("(204) 폐지된 '주문 전에 감사 PASS까지 기다린다'가 문서에 현행으로 남아 있지 않다", r"NOT:감사 PASS 또는 FAIL 7회 뒤에만", None),
 # 2026-10-13 22:00 — 차트스쿨 01(지지와 저항) 대조 · 사용자 승인 A·C (B 종가 이탈은 미국 run 뒤)
 ("(205) 지지는 선이 아니라 구간 — 손절은 구간 아랫선 아래 · 진입은 구간 안(price_levels 구간 lo~hi · theses.leg_problems)", r"지지는 선이 아니라 구간이다", None),
 ("(206) 손절선 내리기 금지 — set-trigger가 거부, 정당한 재설정만 --allow-loosen과 사유로(교정 loosened)", r"손절선은 내리지 않는다", None),
 # 2026-10-13 밤 사용자: "날짜 기록은 무조건 현지 날짜(국장이면 한국, 미장이면 미국) 기준으로 하는 걸로 하자" ·
 #   장 마감 뒤 run = 거래하는 세션의 현지 날짜(다음 거래일) · 과거 파일은 그대로 · 10-12·10-13 미국 두 run만 제 이름으로
 ("(207) 스탬프·기록 날짜는 거래하는 세션의 현지 날짜 — 국내 한국·미국 동부 · 마감 뒤·주말은 다음 거래일 · 전환일 2026-10-14(sessions.run_stamp · 현재 run 포인터)", r"날짜 규칙\(2026-10-14~\)", None),
 ("(208) 국내·미국이 같은 날 같은 스탬프 — 순서는 스탬프가 아니라 시작 시각(sessions.py prev)", r"\*\*같은 스탬프\*\*를 쓴다", None),
 # 2026-10-13 22:00 사용자 승인(차트스쿨 01 B) — 지지가 깨졌는지는 종가로 · 다음 run이 무효 조건 재판정 · 그 run은 추가 매수 없음
 ("(209) 지지 구간 종가 이탈 — 보유의 직전 종가가 구간 아래면 이어받기 '## 지지 이탈' · §6 [지지 이탈] 재판정 · 그 run 추가 매수 거부(support_break)", r"지지 이탈 — 지지 구간을 기준으로 산 보유가", r"support_break_judged"),
 # 2026-10-13 밤 사용자: "다른 거 할 때 미리 fact-check를 백그라운드에 돌려놓으면 안 되나 · 감사를 돌릴 수 있는 가장 빠른 타이밍에
 #   백그라운드로 돌리고 다음 스텝으로 · 안 왔으면 기다려야지" — 2026-10-14 배경화
 ("(210) fact-check는 전부 배경 — 묶음 ②-A(3단 끝)·②-B(4단 심층 끝)·①(시그널 초안 직후) · 감사는 교정 반영 즉시", r"fact-check는 전부 배경이다", r"fc2_joined"),
 ("(211) 배경 에이전트는 init·clear·apply·원장·노트 편집을 하지 않는다 — 교정은 corrections 블록, 적용은 본 세션이 경계에서", r"하지 않는다\*\*: `SG init`·`SG clear`", None),
 ("(212) run을 닫기 전에 ①·② 감사 결과를 기다린다 — 10분 침묵이면 한 번 다시 띄우고 두 번째도 침묵이면 미수신 · 늦은 ① 결과는 다음 run 이어받기가 받는다", r"run을 닫기 전에 결과를 기다린다", r"track1_audit_post"),
 ("(213) 주문 전 ① 판정표는 기다린다 — LINT OK·REFS OK 없이 facts.py decide --mode pre 금지 · 두 번 멈추면 본 세션이 남은 행을 채운다", r"근거가 서야 주문이 나간다", r"fc1_background_joined"),
 ("(214) 폐지된 '갈래 ② 게이트·감사는 6단 집행 뒤 · ① 감사는 체결 확정 직후 띄운다'가 문서에 현행으로 남아 있지 않다", r"NOT:6단 집행 뒤에 돈다|직후 주문 근거 판정표 감사를 띄운다|`execute` GATE PASS 뒤에\*\* 돈다", None),
 ("(196) 필수 요소는 전부 출처 — [S번호]·basis_refs·regime_refs·sources(note_lint signal → 승인 파일 note_lint)", r"필수 요소는 전부 출처를 단다", r"\"note_lint\": \"NOTE-LINT OK"),
 ("(197) 배경 서술 fact-check는 같은 깊이로 · 감사는 전파 즉시 배경 · 묶음마다 최대 두 번(2026-10-14 개정)", r"갈래 ② 감사 — 묶음 합류", None),
 ("(198) 교정은 사실 한 행 갱신(facts.py correct) 뒤 재렌더 — 사본이 없으니 전파 단계가 없다(2026-10-17)", r"facts\.py correct", r"NOT:PROPAGATE OK|fc_propagate"),
 ("(199) 폐지된 노트 깊이 검문(바이트 하한)이 문서에 현행으로 남아 있지 않다", r"NOT:\*\*≥12,000바이트\*\*", r"NOT:depth_not_padded|three_timestamps|fc_correction_propagated"),
 # ── 2026-10-15 실계좌 전환 준비 주간 — 매도 뒤 재투자 · 실계좌 1주 · 실전 점검표 · close · 슬롯
 ("(215) 같은 run의 매도 대금은 gap에 들어간다 — gap은 매도 뒤 투자비중으로 잰다(회전 매도 제외)", r"판 돈도 gap에 들어간다", None),
 ("(216) 승인 뒤 따로 나간 매도가 판 돈은 그 run이 재투자하거나 미룬 사유를 남긴다(refill 기록 · close 종료코드 3 · refill-defer)", r"매도 뒤 재투자 — 이 run 안에서 판 돈은 이 run이 다시 쓴다", r"post_sell_refill"),
 ("(217) 이어받기에 `## 재투자 대기` 절 — 직전 run이 미룬 재투자를 이 run이 받는다", r"재투자 대기 — 직전 run이 판 돈이", r"## 재투자 대기"),
 ("(218) 예약 기본값에서 이번 매수 종목·judge 칸·체결 칸·만기 논지를 뺀다", r"예약에서 빠지는 것", None),
 ("(219) 실계좌는 주 단위 최소 1주(자동 사유 one_share · 집중 공개선은 실제 1주로)", r"실계좌는 주 단위 최소 1주", None),
 ("(220) run마다 실전 점검표를 close로 만들어 계좌 현황 앞에 그대로 붙이고 푸시 뒤 push_log", r"실전 점검표", r"real_check_reported"),
 ("(221) run 마지막에 stage.py close(미룬 주간거래 점검 · 재투자 대기 · 점검표) — 점검표 없으면 execute·no_trade가 막힌다", r"stage\.py close --market", r"close_ran"),
 ("(222) 정규 슬롯 국내 09:30 · 미국 개장 5분 뒤(11-02부터 23:35)", r"\*\*09:30\*\* 국내 개장 30분 뒤", None),
 ("(223) 폐지된 '장중 20분 감시 루프가 돈다'가 문서 일과표에 현행으로 남아 있지 않다", r"NOT:\*\*장중 20분마다\*\* \(루틴", None),
 ("(224) 재투자 pass는 refill.py — 매도 뒤 잔고·새 시세 · 매수 제안+후보만 · 시그널 300분 · 배분 원장 안 씀 · 체결 뒤 consumed", r"refill\.py --market <mkt> --stamp <YYMMDD> --done", r"post_sell_refill"),
 ("(225) 시그널에 refill_candidates(없으면 [] + refill_none_why) — fc_track1 extract 전에 · 제안과 같은 검증", r"refill_candidates` — \"현금이 생기면 다음에 살 것\"", r"refill_candidates_listed"),
 # ── 2026-10-15 감사 — 노트·판정표 작성 규율이 주된 방향(사용자: "감사자는 최소한으로 완화 · 나머지는 노트 작성 퀄리티")
 ("(226) 폐지(2026-10-17) — 교정 전파 범위 규칙: 사실은 한 곳(저장소)에만 있어 전파할 사본이 없다 · 문서에 현행으로 남아 있지 않다", r"NOT:전파 범위가 리서치 갈래·출처 등록 입력·거래 기록", r"NOT:PROPAGATE SCOPE"),
 ("(227) 주문 근거 고정본은 다시 뽑지 않는다 — 근거를 바꾸거나 더하면 render-claims --batch track1 --append(새 번호)", r"render-claims --batch track1", None),
 ("(228) 판정표 작성 — 부재 단정은 검색 기록 · 주문 근거 행은 받치는 것 · 무시는 반증 링크·행 지목(lint s·z·ⓙ)", r"받치는 것: <논지\|크기\|배분\|보유\|매도>", None),
 ("(229) 감사자 쪽은 버그·정보·안전만 — 같은 내용 중복 감사 안 띄움 · 대체 결과는 회차 제외 · 결과 전문 findings_file · 식별자·수정 금지", r"findings_file=", None),
 ("(230) 실계좌는 국내:미국 몫을 run마다 판단(market_share · 합 100 · why · 이어받기) — 없으면 risk_guard 거부", r"실계좌는 시장 몫도 판단이다", r"market_share_judged"),
 ("(231) 반대편 자산은 같은 계좌 계열만 — 실계좌 run이 모의 스냅샷·자산 행을 더하지 않는다", r"반대편은 같은 계좌 계열만 더한다", None),
 ("(232) 실계좌 전환은 account_switch.py — 사용자 요청 때만 · 예행이 모의 행 섞임을 센다 · 모의 보유는 armed로 되돌린다", r"전환은 `account_switch\.py`로만", None),
 ("(233) 토요일 피드백은 그 주 실전 점검표를 읽고 ★ 항목마다 원인·조치(§5-b 실전 준비)", r"§5-b 실전 준비", r"real_ready_reviewed"),
 ("(234) [AI] 줄은 인용과 함께 태어난다 — 새 T1·T2 사실은 원문 인용·URL 없이 등록 거부 · 노트 검사가 [AI]가 가리키는 출처 행의 URL·인용·as-of", r"T1·T2 사실은 원문 인용과 함께 등록한다", None),
 ("(235) 정식 감사 전에 같은 패킷으로 다른 에이전트의 모의 감사 1회 — 지적은 유형 단위로 전 행에", r"모의 감사\(2026-10-15", r"preaudit_done"),
 ("(236) 리서치 갈래·심층은 사실마다 원문 인용·URL·as-of를 돌려준다 — 못 받은 사실은 [확인 안 됨]", r"원문 문장을 그대로\(20자 이상\)", None),
 ("(237) MATCH로 쓸 원문은 fetch.py로 열어 보관 — 판정표 MATCH 인용을 보관 원문과 기계 대조(lint x)", r"MATCH로 쓸 원문도 `fetch\.py`로 연다", None),
 # ── 2026-10-17 데이터 재설계(계획서 ai-squishy-acorn §0 R1~R14) — 사실·데이터는 저장소 하나, 문서는 조회 결과 ──
 ("(238) R1 사실·데이터는 저장소(journal/trading.db) 하나 — 시그널을 표로 옮기는 것도 도구(db.py import)", r"db\.py import", None),
 ("(239) R2 리서치 전에 이미 아는 사실을 연다(facts.py known) — 바뀐 것·빈 것만 조사", r"facts\.py known", None),
 ("(240) R3 검증된 고정 사실은 다시 검증하지 않는다", r"다시 검증하지 않는다", None),
 ("(241) R5 하나 바뀌면 렌더 문서가 따라온다 — 노트 값 자리·§3·§12는 stage.py render-note가 채운다", r"stage\.py render-note", None),
 ("(242) R5 참조 무결성은 REFS OK로 본다(전파 확인 PROPAGATE OK 대신)", r"REFS OK", None),
 ("(243) R6 조사 결과는 종류별 행(kind + 필수 칸)으로 돌려주고 facts.py add로 등록 — 필수 칸 없으면 거부", r"필수 칸", None),
 ("(244) R6 뉴스레터는 재료 — 발행분이 다룬 종목·섹터·축을 facts.py mention으로 적고 §3은 그 표에서 렌더", r"facts\.py mention", None),
 ("(245) R8 감사 규칙은 그대로(① 5회·② 묶음 2회·run 끝 기다림) — 시점만 '교정 반영 즉시'", r"교정 반영 즉시", None),
 ("(246) R11 토요일 피드백 지표에 run 시간(체결 뒤 마감·수집→전송)과 뉴스 전 사실·재료 반복이 있다", r"post_fill_minutes_avg_7d", None),
 ("(247) R12 '뉴스보다 먼저 안 사실 수'가 run마다 남는다(ex_run.before_news_n)", r"before_news_n", None),
 ("(248) 폐지(2026-10-17) — 매매 쪽 교정 전파 도구(fc_propagate)·고정본 재추출 지시가 trade-run 문서에 현행으로 남아 있지 않다", r"NOT:fc_propagate\.py plan", None),
 # ── 2026-10-17 자가 피드백 FB-261017(자동 적용분 · 게이트 승격)
 ("(249) 진입을 막는 시나리오 대응은 업종·요인 범위를 적고 반대 시장 원 대응보다 넓지 않게 · 효력은 next_check까지(FB-261017-3)", r"진입을 막는 대응은 범위를 적는다", r"response_scoped"),
 ("(250) 이미 나온 판정 입력(공시 목록의 잠정실적·주요 공시 · 판정일이 지난 예정 사건)은 그 run이 읽고 판정한다 — crosscheck가 센다(FB-261017-4)", r"이미 나온 판정 입력을 읽는다", r"dart_inputs_read"),
 ("(251) 전망 수혜 이름의 [통과]는 지도가 그 이름에 적은 분류를 인용하고 반박한다(FB-261017-5)", r"\[통과\]는 지도를 인용하고 반박한다", r"pass_quotes_map"),
 ("(252) 전망의 수단 바스켓 이름은 전부 후보 또는 [통과] · 축 중복은 동인으로(FB-261017-6)", r"수단 바스켓은 전부 평가한다", r"basket_names_proposed"),
 ("(253) 주문 근거 행은 그 매수의 논지·크기 사유를 실제로 떠받치는 사실이어야 MATCH(승격 — fact-check:verdict)", r"받치는 것: <논지\|크기\|배분\|보유\|매도>", r"order_evidence_pertinence"),
 ("(254) 매수 결정을 좌우한 숫자는 1차 출처까지 열거나 시도를 적는다(승격 — fact-check:verdict)", r"재인용 링크는 1차 출처까지 열고", r"order_evidence_primary"),
 ("(255) 요인이 꺾이면 방향이 아니라 판정 — 원인·상태·종목별 저점매수/유지/정리를 run이 적고 risk_guard가 판정 없는 매수를 거부(FB-261017-7)", r"\[요인 판정\]", r"factor_judged|factor_resolved"),
 ("(256) 확인된 축의 보유가 이 시장 보유 평균의 절반 미만이면 이 run의 증액 BUY 또는 holds small_why·계좌 기여(FB-261017-8)", r"confirmed_axis_min_vs_avg_ratio", r"undersized_resolved"),
 ("(257) 소급 매도는 decide --mode post 출력에서만 — 판단으로 되팔지 않는다 · 낡은 근거는 같은 run에서 재확인 또는 보유 재판정 · 미결이면 close가 닫지 않는다(FB-261017-9)", r"판단으로 되팔지 않는다", r"retro_tool_only"),
 ("(258) 거부 회피 재실행은 risk_guard가 기억한다 — 유효성 거부 뒤 고친 제안은 회차를 넘겨 그 제안만 거부(ex_screen) · 나머지는 전송 · 한도는 첫 회차 해시 고정(2026-10-19)", r"거부 회피 재실행", r"reroll_reported"),
 ("(259) 논지 근거 = 저장소 사실 번호 — 주문은 논지 근거 위에 서고 틀린 근거는 재검토 → set-evidence 또는 닫기 · 부족 검사는 주문이 아니라 처리를 요구(2026-10-19)", r"set-evidence", r"deficit_met"),
 # ── 2026-10-19~ Codex 누적 리뷰 조치(공통 구조 여섯 · 일곱 묶음)
 ("(260) 집중 공개선은 심사·gap 채우기·최종 묶음에서 같은 식으로 잰다 — 명분 없는 매수는 선에서 멈추고 미달 사유 '집중 선에서 멈춤'(Codex R05)", r"집중 선에서 멈춤", None),
 ("(261) 판정은 그 판정이 본 사실 판에만 선다 — 값이 바뀐 사실의 옛 판정은 안 센다(REVERIFY · 재판정 필요) · 승인 뒤 값이 바뀌면 전송 0 · 값만 바뀐 체결 근거는 소급 아님(Codex R04)", r"재판정 필요", None),
 ("(262) 저장소 판이 코드 판과 다르면 db.py migrate(백업·판마다 검증·롤백) 뒤 이어가고 실패면 멈춘다(Codex R12)", r"db\.py migrate", None),
 ("(263) 사실은 본 본문 판을 가리킨다 — 리서치 행에 fetch.py가 찍은 판(source_version) · 내용이 바뀌면 새 판 · lint는 그 판과 대조(Codex U01·S01)", r"source_version", None),
 ("(264) capture 필수 실패는 종료 2 · 선택 수집 실패는 노트 §10 [제한·1단] 줄로 남기고 지우지 않는다 · carry·map은 capture 성공 기록이 전제(Codex U02)", r"\[제한·1단\]", None),
 ("(265) close 종료코드 0/3/4/5/6/7 — 0이 아니면 run을 닫지 않는다 · 감사 회차는 다른 폴더(예행) 감사를 세지 않는다(Codex U02·U03)", r"외부 점검 명령 실패", None),
 ("(266) 국면·기저율·가격 수준·후보 측정·회고는 확정 종가(마지막 닫힌 세션)만 · 장중 행은 마감 뒤 재관측해 확정 · 휴장일 표 커버리지 ★면 갱신(Codex U04)", r"확정 종가", None),
 ("(267) 날짜가 정해진 사건의 가지는 events[]·cond로 구조화한다 — 지표 공간 미커버·지연 가지 없음·대응이 다른 겹침은 scenarios.py check가 세고 적용일부터 note_lint가 막는다 · 같은 조건이어도 대응이 다르면 병합하지 않는다 · 대응 완료는 발생 단위(Codex S04·S05·J02)", r"사건과 가지 조건을 구조로 적는다", None),
 ("(268) 주문 행은 보낸 승인 판(해시+순번)에 잇고 체결은 주문마다 마지막 관측이다 · 장부 표는 자료형·값 제약 · 그날 기록이 덮였으면 전송 0(Codex R10·R11·R12·R02)", r"기록이 덮인", None),
 ("(269) 통합자산은 한 곳(공유 현금 한 번·그날 환율·입출금 제외) · 비용은 분자·분모 같은 계좌 계열 · 측정 판이 다른 주의 수익률·비용과 비교하지 않는다(Codex R06·R07)", r"측정 판 2", None),
 ("(270) 미반영 수혜는 이번 세션의 유효한 측정만 센다 — 지도 단계가 열린 축을 자동 측정 · 측정 실패는 마지막 성공을 지우지 않는다 · 경계와 미측정을 가른다(Codex U05)", r"미반영은 이번 세션 측정만", None),
 ("(271) 전망 채점은 세움·판정(그 기간)·기한 도래(판정·미판정·기각)·미성숙 코호트로 — 기한 도래 목록은 미판정만(Codex U06)", r"전망 기한 코호트", None),
 ("(272) 재료 결손 생성줄 뒤에 재료 상태 줄(출처 요청·수신·빈·실패·장기 미발행·링크 선택·필요 재료 미확보) · 링크는 새 호만 5/5/5·이미 연 주소 건너뜀(Codex G01)", r"재료 상태 줄", None),
 ("(273) 매수 목표는 바닥 검사 전에 target_basis로 정한다 — 임의성 낮은 수준이 아니면 수준 자체의 사유(바닥을 가리키는 사유 거부) · 기대 상승폭은 목표까지 거리를 넘지 않는다 · 기저율은 시계와 함께(Codex J01)", r"목표 기준 선택 사유 없음", None),
 ("(274) 매수마다 논지 구조 다섯 칸(작동 경로·반영/새로움·가장 강한 반대 사실·틀렸다는 관측·결판 기한) — 적용일부터 note_lint가 막는다(Codex G02)", r"논지 구조 — 사실이 맞아도", None),
 ("(275) 모델 매수는 전송 직전 시세로 손절선·진입 구간·목표 여유를 다시 보고 어긋나면 보내지 않는다 · 전송 시점 시세 기록 · 위험 축소 매도는 대상 아님(Codex J04)", r"전송 직전 전제 재검사", None),
 ("(276) 재발주·정정도 보내기 전에 의도를 그날 기록에 쓴다 — 접수 직후 죽어도 재시작이 다시 보내지 않고 당일 목록으로 붙인다(Codex W01)", r"재발주·정정도 보내기 전에 기록한다", None),
 ("(277) 전송 직전 갱신·상한으로 바뀐 실제 요청을 보내기 전에 다시 적는다 · 당일 목록 대사는 실제 요청 수량으로(Codex W02)", r"실제 요청", None),
 ("(278) 판정은 큐 행의 판을 옮긴다 — 지금 판으로 추정하지 않는다 · 요청 뒤 값이 바뀐 판 없는 판정은 거부 · 근거 본문 판 교체는 인용 대조(Codex W03)", r"지금 판으로 추정하지 않는다", None),
 ("(279) 감사 PASS는 지금 판정표 판을 본 감사만 — 해시가 다르면 STALE · 회차는 이력 그대로(Codex W06)", r"지금 판정표 판", None),
 ("(280) 시세를 못 읽는 원인을 없앤다 — 토큰 만료면 새 토큰으로 한 번 다시 · 빈 값 거래소면 다른 거래소 · 거래소 없는 주문은 찾은 거래소로(2026-10-19 사용자)", r"시세를 못 읽는 원인", None),
]


# `stepgate.run_criterion`이 자리표시자를 **치환하는** 필드. 이 밖에 `{...}`를 쓰면
# 리터럴로 남아 그 기준은 영원히 히트 0이 된다(살아 보이지만 죽은 기준).
RESOLVED_FIELDS = {"file", "glob", "path", "pat", "cell_pat",
                   "min", "minbytes", "min_distinct", "cell_min", "max_run"}
# 자리표시자는 이름이다 — `[0-9]{4}` 같은 **정규식 수량자와 구분**해야 한다.
# (숫자만인 `{4}`를 자리표시자로 세면 내 검사기가 오탐을 만든다.)
RX_PH = re.compile(r"\{([A-Za-z_]\w*)\}")


def chain_checks(rb: dict, docs: str) -> list:
    """요구 → 게이트 사슬이 **실제로 이어지는지** 검사한다. (이름, ok, 근거)

    키워드 grep의 한계: 게이트 앵커를 문항 산문에서 우연히 찾아도 '있음'이 되고,
    워커가 제출하지 않는 자리표시자를 쓴 기준은 **파일을 못 찾아 항상 FAIL**인데
    "기준이 있다"로 세어진다. 둘 다 지금까지 실제로 일어났다(E10·E11).
    """
    out = []

    # 구조 색인 — 값이 아니라 **이름·경로·패턴의 존재**만 본다.
    obs_ids, cps, crit_fields = set(), set(rb.keys()), []
    for cp, b in rb.items():
        for o in b.get("observations", []):
            if isinstance(o, dict) and o.get("id"):
                obs_ids.add(o["id"])
        for c in b.get("criteria", []):
            if isinstance(c, dict):
                crit_fields.append((cp, c))

    # ① 게이트 앵커가 **실재하는 id/cp**인가 — 산문 우연 일치를 걸러낸다.
    weak = []
    for name, _d, gpat in REQS:
        if gpat is None or str(gpat).startswith("NOT:"):
            continue                       # 금지 앵커는 **없어야** 통과라 id를 가리키지 않는다
        alts = [a for a in re.split(r"\|", gpat) if a]
        if not any(re.fullmatch(r"[\w§\- ]+", a) for a in alts):
            continue                       # 정규식 앵커는 산문 대조가 정상이다
        hit_struct = any(a in obs_ids or a in cps for a in alts)
        if not hit_struct:
            weak.append(f"{name.split()[0]}→{gpat}")
    out.append(("게이트 앵커가 실재 id·cp를 가리킨다", not weak,
                f"전 {sum(1 for r in REQS if r[2])}건이 구조에 걸린다" if not weak else
                f"**산문에만 걸리는 앵커 {len(weak)}건**: {', '.join(weak[:4])}"
                + (" …" if len(weak) > 4 else "")))

    # ② 기준이 쓰는 자리표시자를 **워커가 제출하도록 문서가 지시하는가.**
    #    안 그러면 그 기준은 파일을 못 찾아 항상 FAIL이다 — 있으나 없는 검사다.
    used = set()
    for _cp, c in crit_fields:
        for k, v in c.items():
            if isinstance(v, str):
                used.update(RX_PH.findall(v))
    documented = set(re.findall(r"(\w+)=<", docs)) | {"deliverable", "dir"}
    undoc = sorted(used - documented)
    out.append((f"기준의 자리표시자 {len(used)}종이 문서에 지시돼 있다", not undoc,
                "전건 지시됨" if not undoc else
                f"**문서가 제출을 지시하지 않는 자리표시자 {undoc}** — 그 기준은 "
                f"인자를 못 받아 항상 FAIL이고, '기준이 있다'로 세어진다"))

    # ③ 치환되지 않는 필드에 자리표시자를 쓰지 않았는가.
    dead = []
    for cp, c in crit_fields:
        for k, v in c.items():
            if k not in RESOLVED_FIELDS and isinstance(v, str) and RX_PH.search(v):
                dead.append(f"{cp}.{k}")
    out.append(("자리표시자가 치환되는 필드에만 있다", not dead,
                "위반 0" if not dead else
                f"**치환 안 되는 필드에 자리표시자 {dead}** — 리터럴로 남아 히트 0이 된다"))

    # ④ 리터럴 경로를 쓰는 기준은 **지금 실제로 히트하는가**(죽은 기준 탐지).
    sys.path.insert(0, str(STEPGATE_DIR))
    try:
        import stepgate as sg
    except ImportError as e:
        out.append(("리터럴 기준 실측", False, f"stepgate import 실패: {e}"))
        return out
    lit_fail = []
    lit_n = 0
    for cp, c in crit_fields:
        tgt = c.get("file") or c.get("glob") or c.get("path") or ""
        if not tgt or RX_PH.search(str(tgt)):
            continue                       # 워커 인자에 의존하는 기준은 여기서 못 잰다
        lit_n += 1
        ok, why = sg.run_criterion(c, {})
        if not ok:
            lit_fail.append(f"{cp}: {why[:70]}")
    out.append((f"리터럴 경로 기준 {lit_n}건이 실제로 히트한다", not lit_fail,
                "전건 히트" if not lit_fail else
                f"**히트 0인 기준 {len(lit_fail)}건**(죽은 기준일 수 있다): "
                + "; ".join(lit_fail[:3])))
    return out


def main() -> int:
    quiet = "--quiet" in sys.argv
    # ★ **백업 파일은 문서가 아니다**(2026-09-23). `_*_backup_*.md`가 `references/`에 같이 살아서, 규칙을 지워도
    #   낡은 백업이 앵커를 만족시켜 감사가 "있음"이라고 답했다 — 백업을 청소하는 순간 드러난 거짓 통과다.
    docs = ""
    for p in [SKILL / "SKILL.md"] + sorted(SKILL.glob("references/*.md")) + [SKILL_FB / "SKILL.md"]:
        if p.name.startswith("_") or "_backup_" in p.name or not p.exists():
            continue
        docs += p.read_text(encoding="utf-8")
    if not docs:
        print(f"스킬 문서를 못 찾았다: {SKILL}", file=sys.stderr)
        return 2

    rb = json.loads(RUBRIC.read_text(encoding="utf-8"))
    # cp 이름 + 관찰 id/문항 + 기준의 경로·패턴만 모은다(합격 기준은 출력하지 않는다)
    gate = " ".join(rb.keys())
    rb_all = dict(rb)
    if RUBRIC_FB.exists():                      # 형제 스킬 루브릭도 키워드 존재만 본다(봉인 안전 — 출력 없음)
        for _cp, b in json.loads(RUBRIC_FB.read_text(encoding="utf-8")).items():
            rb_all["trade-feedback:" + _cp] = b
            gate += " trade-feedback:" + _cp
    if RUBRIC_FC.exists():                      # fact-check 게이트 ②의 문항도 키워드 존재만 본다(봉인 안전 — 출력 없음)
        for _cp, b in json.loads(RUBRIC_FC.read_text(encoding="utf-8")).items():
            rb_all["fact-check:" + _cp] = b
            gate += " fact-check:" + _cp
    if RUBRIC_SM.exists():                      # skill-maintainer verify — 코드 편집 절차 문항(봉인 안전 — 출력 없음)
        for _cp, b in json.loads(RUBRIC_SM.read_text(encoding="utf-8")).items():
            rb_all["skill-maintainer:" + _cp] = b
            gate += " skill-maintainer:" + _cp
    for _cp, b in rb_all.items():
        for o in b.get("observations", []):
            gate += " " + o.get("id", "") + " " + " ".join(v.get("ask", "") for v in o.get("variants", []))
        for c in b.get("criteria", []):
            gate += " " + " ".join(str(c.get(k, "")) for k in ("pat", "glob", "file", "path"))

    rows, gaps = [], []
    for name, dpat, gpat in REQS:
        if dpat is not None and str(dpat).startswith("NOT:"):
            din = not re.search(dpat[4:].split("|")[0], docs)    # 문서 금지 앵커 — 첫 대안만 금지어(나머지는 설명용)
        else:
            din = True if dpat is None else bool(re.search(dpat, docs))
        if gpat is None:
            gin = None
        elif str(gpat).startswith("NOT:"):
            hit = re.search(gpat[4:], gate)
            gin = not hit                      # 금지 앵커 — 루브릭에 있으면 실패
        else:
            gin = bool(re.search(gpat, gate))
        rows.append((name, din, gin))
        if not din or gin is False:
            gaps.append((name, din, gin))

    if not quiet:
        print(f"{'요구사항':<40}{'문서':>6}{'게이트':>8}")
        print("─" * 56)
        for name, din, gin in rows:
            g = "—" if gin is None else ("있음" if gin else "**없음**")
            print(f"{name:<40}{'있음' if din else '**없음**':>6}{g:>8}")
        print("─" * 56)

    # ★ 사슬 검사 — 키워드 grep이 "있음"이라고 한 것이 실제로 이어지는지 본다.
    chain = chain_checks(rb_all, docs)          # 형제 스킬 루브릭도 같은 사슬 검사(자리표시자·실재 id)
    n_chain_fail = sum(1 for _, ok, _ in chain if not ok)
    if not quiet:
        print()
        print("사슬 검사 — 요구 → 게이트 항목 → 디스크 기준이 실제로 이어지는가")
        print("─" * 56)
        for name, ok, why in chain:
            print(f"  {'✓' if ok else '**✗**'} {name}")
            print(f"      {why}")
        print("─" * 56)

    nd = sum(1 for _, d, _ in rows if not d)
    ng = sum(1 for _, _, g in rows if g is False)
    print(f"요구 {len(rows)}건 · 문서 누락 {nd}건 · 게이트 누락 {ng}건 "
          f"· 사슬 결함 {n_chain_fail}건")
    if n_chain_fail and quiet:
        for name, ok, why in chain:
            if not ok:
                print(f"  ✗ 사슬: {name} — {why}")
    if gaps:
        print("\n★ 누락 — 요구가 빠졌거나 검문되지 않는다:")
        for name, din, gin in gaps:
            why = []
            if not din:
                why.append("문서에 규칙이 없다")
            if gin is False:
                why.append("게이트가 검문하지 않는다")
            print(f"  · {name} — {' / '.join(why)}")
        return 1
    print("전 요구가 문서에 있고 게이트가 검문한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
